from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import shutil
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image

from .contracts import MediaView, StorageView
from .media import PreparedMedia, StorageFull
from .repository import AnnotationRepository, ClipNotReady, PrivateAnnotationClip


class FrameIntegrityError(RuntimeError):
    pass


class FrameOutOfRange(IndexError):
    pass


class FrameQueueFull(RuntimeError):
    pass


class FrameRequestTimeout(TimeoutError):
    pass


class SourceChanged(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class FrameService:
    def __init__(
        self,
        annotation_root: Path,
        repository: AnnotationRepository,
        *,
        cache_limit_bytes: int = 512 * 1024**2,
        queue_limit: int = 32,
        request_deadline_seconds: float = 10,
        prepared_limit_bytes: int = 20 * 1024**3,
        free_disk_reserve_bytes: int = 2 * 1024**3,
    ) -> None:
        self.root = annotation_root.resolve()
        self.repository = repository
        self.cache_limit_bytes = cache_limit_bytes
        self.request_deadline_seconds = request_deadline_seconds
        self.prepared_limit_bytes = prepared_limit_bytes
        self.free_disk_reserve_bytes = free_disk_reserve_bytes
        self._slots = threading.BoundedSemaphore(queue_limit)
        self._cache: OrderedDict[tuple[str, str, int], bytes] = OrderedDict()
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._source_identity: dict[str, tuple[int, int, str]] = {}
        self._lease_condition = threading.Condition()
        self._leases: dict[tuple[str, str], int] = {}

    def _owned_media_bytes(self) -> int:
        total = 0
        for directory in (self.root / "prepared", self.root / "staging"):
            if directory.exists():
                total += sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        return total

    def storage(self) -> StorageView:
        used = self._owned_media_bytes()
        free = shutil.disk_usage(self.root).free
        return StorageView(used_bytes=used, limit_bytes=self.prepared_limit_bytes, free_bytes=free)

    def available_preparation_bytes(self) -> int:
        storage = self.storage()
        quota_remaining = max(0, storage.limit_bytes - storage.used_bytes)
        disk_remaining = max(0, storage.free_bytes - self.free_disk_reserve_bytes)
        return min(quota_remaining, disk_remaining)

    def resolve_source_path(self, clip: PrivateAnnotationClip) -> Path:
        private_job = self.repository.jobs.get_private(str(clip.source_job_id))
        source_root = self.repository.database.source_data_root.resolve()
        expected = (source_root / "jobs" / str(clip.source_job_id) / "source.mp4").resolve()
        candidate = Path(private_job.source_path).resolve()
        if candidate != expected or not _is_below(candidate, source_root):
            raise FileNotFoundError("source path is outside the private job directory")
        return candidate

    def _verify_source(self, clip: PrivateAnnotationClip) -> Path:
        try:
            source = self.resolve_source_path(clip)
        except FileNotFoundError:
            self.repository.mark_source_state(clip.id, "missing")
            raise
        if not source.is_file():
            self.repository.mark_source_state(clip.id, "missing")
            raise FileNotFoundError("source file is missing")
        stat = source.stat()
        cached = self._source_identity.get(str(clip.id))
        if cached is not None and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            actual_hash = cached[2]
        else:
            actual_hash = _sha256_file(source)
            self._source_identity[str(clip.id)] = (stat.st_size, stat.st_mtime_ns, actual_hash)
        if clip.source_sha256 is not None and actual_hash != clip.source_sha256:
            self._drop_clip_cache(clip.id)
            self.repository.mark_source_state(clip.id, "hash_mismatch")
            raise SourceChanged("source bytes no longer match the prepared clip")
        return source

    def verify_source_path(self, clip: PrivateAnnotationClip) -> Path:
        """Resolve and hash-check the immutable source before external inference."""
        self._source_identity.pop(str(clip.id), None)
        return self._verify_source(clip)

    def prepared_root(self, clip_id: UUID) -> Path:
        clip = self.repository.get_private_clip(clip_id)
        if clip.generation_id is None:
            raise ClipNotReady("clip has no prepared generation")
        path = (self.root / "prepared" / str(clip_id) / str(clip.generation_id)).resolve()
        if not _is_below(path, (self.root / "prepared").resolve()):
            raise FrameIntegrityError("prepared generation path escaped its private root")
        return path

    def _validated_manifest(self, clip: PrivateAnnotationClip) -> tuple[dict, Path]:
        if clip.preparation_state != "ready" or clip.media is None or clip.artifact_manifest is None:
            raise ClipNotReady("clip media is not ready")
        root = self.prepared_root(clip.id)
        manifest_path = root / "manifest.json"
        try:
            disk_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FrameIntegrityError("prepared manifest is missing or invalid") from exc
        if disk_manifest != clip.artifact_manifest:
            raise FrameIntegrityError("prepared manifest does not match the committed generation")
        manifest = disk_manifest
        required = {
            "schema_version", "source_sha256", "frame_count", "width", "height",
            "fps_num", "fps_den", "sample_aspect_ratio", "frame_sha256", "chunks", "preview",
        }
        if set(manifest) != required or manifest["schema_version"] != 1:
            raise FrameIntegrityError("prepared manifest schema is invalid")
        if (
            manifest["source_sha256"] != clip.source_sha256
            or manifest["frame_count"] != clip.media.frame_count
            or len(manifest["frame_sha256"]) != clip.media.frame_count
        ):
            raise FrameIntegrityError("prepared manifest metadata is inconsistent")
        expected_start = 0
        for chunk in manifest["chunks"]:
            if set(chunk) != {"filename", "start_frame", "frame_count", "sha256"}:
                raise FrameIntegrityError("prepared chunk manifest is invalid")
            if chunk["start_frame"] != expected_start or chunk["frame_count"] <= 0:
                raise FrameIntegrityError("prepared chunks are not contiguous")
            if Path(chunk["filename"]).name != chunk["filename"]:
                raise FrameIntegrityError("prepared chunk filename is invalid")
            expected_start += chunk["frame_count"]
        if expected_start != clip.media.frame_count:
            raise FrameIntegrityError("prepared chunks do not cover the clip")
        return manifest, root

    def publish(
        self, clip_id: UUID, prepared: PreparedMedia, staging: Path, generation_id: UUID | None = None
    ):
        storage = self.storage()
        if storage.used_bytes > storage.limit_bytes or storage.free_bytes < self.free_disk_reserve_bytes:
            raise StorageFull("annotation prepared-media quota is full")
        generation = generation_id or uuid4()
        target = (self.root / "prepared" / str(clip_id) / str(generation)).resolve()
        prepared_base = (self.root / "prepared").resolve()
        if not _is_below(target, prepared_base):
            raise FrameIntegrityError("publish target escaped the private root")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(target)
        staging.resolve().replace(target)
        result = self.repository.complete_preparation(
            clip_id,
            source_sha256=prepared.source_sha256,
            media=MediaView(
                frame_count=prepared.frame_count,
                fps_num=prepared.fps_num,
                fps_den=prepared.fps_den,
                width=prepared.width,
                height=prepared.height,
                sample_aspect_ratio=prepared.sample_aspect_ratio,
            ),
            artifact_manifest=prepared.artifact_manifest,
            prepared_bytes=prepared.prepared_bytes,
            generation_id=generation,
        )
        clip = self.repository.get_private_clip(clip_id)
        source = self.resolve_source_path(clip)
        stat = source.stat()
        self._source_identity[str(clip_id)] = (stat.st_size, stat.st_mtime_ns, prepared.source_sha256)
        return result

    @contextmanager
    def _lease(self, clip: PrivateAnnotationClip):
        if clip.generation_id is None:
            raise ClipNotReady("clip has no prepared generation")
        key = (str(clip.id), str(clip.generation_id))
        with self._lease_condition:
            current = self.repository.get_private_clip(clip.id)
            if current.preparation_state != "ready" or current.generation_id != clip.generation_id:
                raise ClipNotReady("clip generation is no longer readable")
            self._leases[key] = self._leases.get(key, 0) + 1
        try:
            yield
        finally:
            with self._lease_condition:
                remaining = self._leases.get(key, 1) - 1
                if remaining <= 0:
                    self._leases.pop(key, None)
                else:
                    self._leases[key] = remaining
                self._lease_condition.notify_all()

    def _drop_clip_cache(self, clip_id: UUID) -> None:
        prefix = str(clip_id)
        with self._cache_lock:
            for key in [key for key in self._cache if key[0] == prefix]:
                self._cache_bytes -= len(self._cache.pop(key))

    def _cache_get(self, key: tuple[str, str, int]) -> bytes | None:
        with self._cache_lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def _cache_put(self, key: tuple[str, str, int], value: bytes) -> None:
        if len(value) > self.cache_limit_bytes:
            return
        with self._cache_lock:
            replaced = self._cache.pop(key, None)
            if replaced is not None:
                self._cache_bytes -= len(replaced)
            self._cache[key] = value
            self._cache_bytes += len(value)
            while self._cache_bytes > self.cache_limit_bytes:
                _, removed = self._cache.popitem(last=False)
                self._cache_bytes -= len(removed)

    def read_frame(self, clip_id: UUID, index: int) -> bytes:
        if not self._slots.acquire(blocking=False):
            raise FrameQueueFull("exact-frame request queue is full")
        try:
            clip = self.repository.get_private_clip(clip_id)
            with self._lease(clip):
                self._verify_source(clip)
                manifest, root = self._validated_manifest(clip)
                if index < 0 or index >= manifest["frame_count"]:
                    raise FrameOutOfRange(index)
                assert clip.generation_id is not None
                key = (str(clip_id), str(clip.generation_id), index)
                cached = self._cache_get(key)
                if cached is not None:
                    return cached
                chunk = next(
                    item
                    for item in manifest["chunks"]
                    if item["start_frame"] <= index < item["start_frame"] + item["frame_count"]
                )
                chunk_path = (root / "chunks" / chunk["filename"]).resolve()
                if not _is_below(chunk_path, (root / "chunks").resolve()) or not chunk_path.is_file():
                    raise FrameIntegrityError("prepared chunk is missing")
                if _sha256_file(chunk_path) != chunk["sha256"]:
                    raise FrameIntegrityError("prepared chunk hash mismatch")
                try:
                    completed = subprocess.run(
                        [
                            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(chunk_path),
                            "-map", "0:v:0", "-an", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1",
                        ],
                        capture_output=True,
                        check=False,
                        timeout=self.request_deadline_seconds,
                        startupinfo=self._startupinfo(),
                    )
                except subprocess.TimeoutExpired as exc:
                    raise FrameRequestTimeout("exact-frame request exceeded its deadline") from exc
                if completed.returncode:
                    raise FrameIntegrityError("prepared chunk could not be decoded")
                frame_bytes = manifest["width"] * manifest["height"] * 3
                expected_bytes = chunk["frame_count"] * frame_bytes
                if len(completed.stdout) != expected_bytes:
                    raise FrameIntegrityError("prepared chunk decoded to an unexpected size")
                requested_png: bytes | None = None
                for offset in range(chunk["frame_count"]):
                    frame_index = chunk["start_frame"] + offset
                    rgb = completed.stdout[offset * frame_bytes : (offset + 1) * frame_bytes]
                    if hashlib.sha256(rgb).hexdigest() != manifest["frame_sha256"][frame_index]:
                        raise FrameIntegrityError("exact frame hash mismatch")
                    image = Image.frombytes("RGB", (manifest["width"], manifest["height"]), rgb)
                    buffer = io.BytesIO()
                    image.save(buffer, "PNG", optimize=False)
                    png = buffer.getvalue()
                    self._cache_put((str(clip_id), str(clip.generation_id), frame_index), png)
                    if frame_index == index:
                        requested_png = png
                if requested_png is None:
                    raise FrameIntegrityError("requested frame was absent from its prepared chunk")
                return requested_png
        finally:
            self._slots.release()

    @staticmethod
    def _startupinfo() -> subprocess.STARTUPINFO | None:
        if os.name != "nt":
            return None
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        return startupinfo

    def preview_path(self, clip_id: UUID) -> Path:
        with self.preview_lease(clip_id) as path:
            return path

    @contextmanager
    def preview_lease(self, clip_id: UUID):
        clip = self.repository.get_private_clip(clip_id)
        with self._lease(clip):
            self._verify_source(clip)
            manifest, root = self._validated_manifest(clip)
            preview = manifest["preview"]
            if set(preview) != {"filename", "sha256"} or Path(preview["filename"]).name != preview["filename"]:
                raise FrameIntegrityError("preview manifest is invalid")
            path = (root / preview["filename"]).resolve()
            if not _is_below(path, root) or not path.is_file() or _sha256_file(path) != preview["sha256"]:
                raise FrameIntegrityError("clean preview is missing or corrupt")
            yield path

    def delete_clip_media(self, clip_id: UUID) -> None:
        self._drop_clip_cache(clip_id)
        target = (self.root / "prepared" / str(clip_id)).resolve()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging = self.root / "staging" / str(clip_id)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    def release_generation(self, clip_id: UUID) -> None:
        clip = self.repository.get_private_clip(clip_id)
        if clip.preparation_state != "releasing":
            raise ClipNotReady("clip is not marked for release")
        if clip.generation_id is None:
            self._drop_clip_cache(clip_id)
            self.repository.complete_release(clip_id)
            return
        key = (str(clip.id), str(clip.generation_id))
        deadline = time.monotonic() + self.request_deadline_seconds
        with self._lease_condition:
            while self._leases.get(key, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FrameRequestTimeout("prepared media still has active readers")
                self._lease_condition.wait(remaining)
            target = self.prepared_root(clip_id)
            prepared_base = (self.root / "prepared").resolve()
            if not _is_below(target, prepared_base):
                raise FrameIntegrityError("release target escaped the private root")
            if target.exists():
                shutil.rmtree(target)
        self._drop_clip_cache(clip_id)
        self.repository.complete_release(clip_id)
