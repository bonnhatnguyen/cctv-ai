"""Injectable RTSP reader with credential-safe errors and rolling evidence storage."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from pathlib import Path
from typing import Any, Protocol

from app.schemas import CameraHealth


@dataclass(frozen=True)
class Frame:
    camera_id: str
    timestamp_ms: int
    image: Any


@dataclass
class StreamHealth:
    status: CameraHealth = CameraHealth.HEALTHY
    consecutive_failures: int = 0
    reason: str | None = None


class FrameReader(Protocol):
    def read(self) -> Any: ...


class RtspFrameSource:
    """A reader adapter that does not retain or expose RTSP URLs."""

    def __init__(self, camera_id: str, reader: FrameReader, rtsp_env_key: str | None = None):
        self.camera_id = camera_id
        self._reader = reader
        self._rtsp_env_key = rtsp_env_key
        self.health = StreamHealth()

    @classmethod
    def from_environment(cls, camera_id: str, rtsp_env_key: str, reader_factory):
        url = os.environ.get(rtsp_env_key)
        if not url:
            raise RuntimeError(f"RTSP source unavailable for camera {camera_id}")
        return cls(camera_id, reader_factory(url), rtsp_env_key)

    def next_frame(self) -> Frame | None:
        try:
            image = self._reader.read()
        except Exception:
            image = None
        if image is None:
            self.health.consecutive_failures += 1
            if self.health.consecutive_failures >= 3:
                self.health.status = CameraHealth.DEGRADED
                self.health.reason = "camera_degraded"
            return None
        self.health = StreamHealth()
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        return Frame(self.camera_id, now_ms, image)


class RollingClipStore:
    """Keeps a bounded in-memory frame index and exports deterministic evidence bundles.

    Production callers can replace ``encoder`` with an FFmpeg encoder. The default
    writes a manifest so evidence remains explicit even for non-image test frames.
    """

    def __init__(self, root: Path | str, retention_seconds: int, encoder=None):
        self.root = Path(root)
        self.retention_ms = retention_seconds * 1000
        self.encoder = encoder
        self._frames: dict[str, deque[Frame]] = defaultdict(deque)

    def append(self, camera_id: str, frame: Frame) -> None:
        if frame.camera_id != camera_id:
            raise ValueError("frame camera_id does not match append camera")
        frames = self._frames[camera_id]
        frames.append(frame)
        cutoff = frame.timestamp_ms - self.retention_ms
        while frames and frames[0].timestamp_ms < cutoff:
            frames.popleft()

    def export(self, camera_id: str, start_ms: int, end_ms: int) -> Path:
        if end_ms < start_ms:
            raise ValueError("end_ms must not precede start_ms")
        frames = [frame for frame in self._frames[camera_id] if start_ms <= frame.timestamp_ms <= end_ms]
        if not frames:
            raise FileNotFoundError(f"no retained frames for camera {camera_id}")
        output_dir = self.root / camera_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{start_ms}-{end_ms}.mp4"
        if self.encoder:
            self.encoder(frames, output)
        else:
            output.write_text("\n".join(str(frame.timestamp_ms) for frame in frames), encoding="utf-8")
        return output
