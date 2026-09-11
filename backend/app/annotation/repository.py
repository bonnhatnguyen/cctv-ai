from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from app.v1.jobs import JobNotFoundError, JobRepository

from .contracts import (
    CameraSetupCreate,
    CameraSetupListView,
    CameraSetupView,
    ClipListView,
    ClipView,
    MediaView,
    Point,
    RegisterClip,
    ReleasePreparedMedia,
    RetryPreparation,
    RoiView,
    RoiWrite,
    TemplateWrite,
)
from .database import AnnotationDatabase
from .geometry import validate_polygon


class NotFound(LookupError):
    pass


class RevisionConflict(RuntimeError):
    pass


class PayloadConflict(RuntimeError):
    pass


class ClipNotReady(RuntimeError):
    pass


class SourceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivateAnnotationClip:
    id: UUID
    source_job_id: UUID
    revision: int
    preparation_state: str
    source_state: str
    source_sha256: str | None
    media: MediaView | None
    artifact_manifest: dict[str, Any] | None
    generation_id: UUID | None
    prepared_bytes: int | None
    created_at: str


ModelT = TypeVar("ModelT", bound=BaseModel)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_payload(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _payload_hash(model: BaseModel) -> str:
    return hashlib.sha256(_canonical_payload(model).encode("utf-8")).hexdigest()


def _encode_cursor(created_at: str, resource_id: str) -> str:
    raw = json.dumps([created_at, resource_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if not isinstance(value, list) or len(value) != 2 or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError
        return value[0], value[1]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc


class AnnotationRepository:
    def __init__(self, database: AnnotationDatabase, jobs: JobRepository) -> None:
        self.database = database
        self.jobs = jobs

    def _replay(
        self,
        connection: sqlite3.Connection,
        resource_id: str,
        operation_id: UUID,
        action: str,
        request: BaseModel,
        response_type: type[ModelT],
    ) -> ModelT | None:
        row = connection.execute(
            "SELECT action, payload_sha256, response_json FROM operations WHERE resource_id=? AND operation_id=?",
            (resource_id, str(operation_id)),
        ).fetchone()
        if row is None:
            return None
        if row["action"] != action or row["payload_sha256"] != _payload_hash(request):
            raise PayloadConflict("operation id was already used with a different payload")
        return response_type.model_validate_json(row["response_json"])

    @staticmethod
    def _store_operation(
        connection: sqlite3.Connection,
        resource_id: str,
        operation_id: UUID,
        action: str,
        request: BaseModel,
        response: BaseModel,
    ) -> None:
        connection.execute(
            "INSERT INTO operations(resource_id, operation_id, action, payload_sha256, response_json, created_at) VALUES(?,?,?,?,?,?)",
            (
                resource_id,
                str(operation_id),
                action,
                _payload_hash(request),
                response.model_dump_json(),
                _now(),
            ),
        )

    @staticmethod
    def _roi_view(connection: sqlite3.Connection, roi_id: str | None) -> RoiView | None:
        if roi_id is None:
            return None
        row = connection.execute("SELECT * FROM roi_revisions WHERE id=?", (roi_id,)).fetchone()
        if row is None:
            raise RuntimeError("annotation database contains a dangling ROI reference")
        return RoiView(
            id=row["id"],
            revision=row["revision"],
            camera_setup_id=row["camera_setup_id"],
            polygon=[Point.model_validate(item) for item in json.loads(row["polygon_json"])],
            template_revision_id=row["template_revision_id"],
        )

    @classmethod
    def _clip_view(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> ClipView:
        media = MediaView.model_validate_json(row["media_json"]) if row["media_json"] else None
        ready_preview = (
            f"/api/v2/annotations/clips/{row['id']}/preview"
            if row["preparation_state"] == "ready"
            and row["source_state"] == "available"
            and row["preview_relative_path"]
            else None
        )
        return ClipView(
            id=row["id"],
            source_job_id=row["source_job_id"],
            original_name=row["original_name"],
            revision=row["revision"],
            preparation_state=row["preparation_state"],
            source_state=row["source_state"],
            failure_code=row["failure_code"],
            source_sha256=row["source_sha256"],
            media=media,
            roi=cls._roi_view(connection, row["roi_revision_id"]),
            preview_url=ready_preview,
            prepared_bytes=row["prepared_bytes"],
        )

    @staticmethod
    def _setup_view(connection: sqlite3.Connection, row: sqlite3.Row) -> CameraSetupView:
        return CameraSetupView(
            id=row["id"],
            name=row["name"],
            revision=row["revision"],
            template=AnnotationRepository._roi_view(connection, row["template_revision_id"]),
        )

    @staticmethod
    def _require_clip(connection: sqlite3.Connection, clip_id: UUID) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM annotation_clips WHERE id=?", (str(clip_id),)).fetchone()
        if row is None:
            raise NotFound("annotation clip was not found")
        return row

    @staticmethod
    def _require_setup(connection: sqlite3.Connection, setup_id: UUID) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM camera_setups WHERE id=?", (str(setup_id),)).fetchone()
        if row is None:
            raise NotFound("camera setup was not found")
        return row

    @staticmethod
    def _store_clip_revision(
        connection: sqlite3.Connection, clip: ClipView, created_at: str | None = None
    ) -> None:
        connection.execute(
            "INSERT INTO clip_revisions(clip_id, revision, snapshot_json, created_at) VALUES(?,?,?,?)",
            (str(clip.id), clip.revision, clip.model_dump_json(), created_at or _now()),
        )

    def register_clip(self, request: RegisterClip) -> ClipView:
        try:
            job = self.jobs.get(str(request.source_job_id))
            private = self.jobs.get_private(str(request.source_job_id))
        except JobNotFoundError as exc:
            raise NotFound("source job was not found") from exc
        source_state = "available" if Path(private.source_path).is_file() else "missing"
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                str(request.source_job_id),
                request.operation_id,
                "register_clip",
                request,
                ClipView,
            )
            if replay is not None:
                return replay
            existing = connection.execute(
                "SELECT * FROM annotation_clips WHERE source_job_id=?",
                (str(request.source_job_id),),
            ).fetchone()
            if existing is not None:
                result = self._clip_view(connection, existing)
                self._store_operation(
                    connection,
                    str(request.source_job_id),
                    request.operation_id,
                    "register_clip",
                    request,
                    result,
                )
                return result
            clip_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO annotation_clips(
                    id, source_job_id, original_name, revision, preparation_state,
                    source_state, failure_code, source_sha256, media_json,
                    artifact_manifest_json, generation_id, roi_revision_id,
                    preview_relative_path, prepared_bytes, created_at, updated_at
                ) VALUES(?,?,?,0,'preparing',?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?,?)""",
                (str(clip_id), str(request.source_job_id), job.original_name, source_state, now, now),
            )
            row = self._require_clip(connection, clip_id)
            result = self._clip_view(connection, row)
            self._store_clip_revision(connection, result, now)
            self._store_operation(
                connection,
                str(request.source_job_id),
                request.operation_id,
                "register_clip",
                request,
                result,
            )
            return result

    def get_clip(self, clip_id: UUID) -> ClipView:
        with self.database.read_connection() as connection:
            return self._clip_view(connection, self._require_clip(connection, clip_id))

    def find_clip_by_source_job(self, source_job_id: UUID) -> ClipView | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM annotation_clips WHERE source_job_id=?", (str(source_job_id),)
            ).fetchone()
            return self._clip_view(connection, row) if row is not None else None

    def get_private_clip(self, clip_id: UUID) -> PrivateAnnotationClip:
        with self.database.read_connection() as connection:
            row = self._require_clip(connection, clip_id)
            return PrivateAnnotationClip(
                id=UUID(row["id"]),
                source_job_id=UUID(row["source_job_id"]),
                revision=int(row["revision"]),
                preparation_state=row["preparation_state"],
                source_state=row["source_state"],
                source_sha256=row["source_sha256"],
                media=MediaView.model_validate_json(row["media_json"]) if row["media_json"] else None,
                artifact_manifest=json.loads(row["artifact_manifest_json"])
                if row["artifact_manifest_json"]
                else None,
                generation_id=UUID(row["generation_id"]) if row["generation_id"] else None,
                prepared_bytes=row["prepared_bytes"],
                created_at=row["created_at"],
            )

    def next_clip_in_state(self, state: str) -> PrivateAnnotationClip | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT id FROM annotation_clips WHERE preparation_state=? ORDER BY created_at,id LIMIT 1",
                (state,),
            ).fetchone()
        return self.get_private_clip(UUID(row["id"])) if row is not None else None

    def list_clips(self, limit: int = 50, cursor: str | None = None) -> ClipListView:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        key = _decode_cursor(cursor)
        with self.database.read_connection() as connection:
            if key is None:
                rows = connection.execute(
                    "SELECT * FROM annotation_clips ORDER BY created_at, id LIMIT ?", (limit + 1,)
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM annotation_clips
                    WHERE created_at > ? OR (created_at = ? AND id > ?)
                    ORDER BY created_at, id LIMIT ?""",
                    (key[0], key[0], key[1], limit + 1),
                ).fetchall()
            page = rows[:limit]
            next_cursor = (
                _encode_cursor(page[-1]["created_at"], page[-1]["id"])
                if len(rows) > limit
                else None
            )
            return ClipListView(
                items=[self._clip_view(connection, row) for row in page],
                next_cursor=next_cursor,
            )

    def create_setup(self, request: CameraSetupCreate) -> CameraSetupView:
        resource_id = "camera-setup-create"
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                resource_id,
                request.operation_id,
                "create_setup",
                request,
                CameraSetupView,
            )
            if replay is not None:
                return replay
            setup_id = uuid4()
            now = _now()
            connection.execute(
                "INSERT INTO camera_setups(id,name,revision,template_revision_id,created_at,updated_at) VALUES(?,?,0,NULL,?,?)",
                (str(setup_id), request.name, now, now),
            )
            result = self._setup_view(connection, self._require_setup(connection, setup_id))
            self._store_operation(
                connection,
                resource_id,
                request.operation_id,
                "create_setup",
                request,
                result,
            )
            return result

    def list_setups(self, limit: int = 50, cursor: str | None = None) -> CameraSetupListView:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        key = _decode_cursor(cursor)
        with self.database.read_connection() as connection:
            if key is None:
                rows = connection.execute(
                    "SELECT * FROM camera_setups ORDER BY created_at, id LIMIT ?", (limit + 1,)
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM camera_setups
                    WHERE created_at > ? OR (created_at = ? AND id > ?)
                    ORDER BY created_at, id LIMIT ?""",
                    (key[0], key[0], key[1], limit + 1),
                ).fetchall()
            page = rows[:limit]
            return CameraSetupListView(
                items=[self._setup_view(connection, row) for row in page],
                next_cursor=(
                    _encode_cursor(page[-1]["created_at"], page[-1]["id"])
                    if len(rows) > limit
                    else None
                ),
            )

    def save_template(self, setup_id: UUID, request: TemplateWrite) -> CameraSetupView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                str(setup_id),
                request.operation_id,
                "save_template",
                request,
                CameraSetupView,
            )
            if replay is not None:
                return replay
            setup = self._require_setup(connection, setup_id)
            if setup["revision"] != request.expected_setup_revision:
                raise RevisionConflict("camera setup revision is stale")
            polygon = validate_polygon(request.polygon)
            revision = int(setup["revision"]) + 1
            roi_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO roi_revisions(
                    id,kind,clip_id,camera_setup_id,revision,polygon_json,
                    template_revision_id,created_at
                ) VALUES(?,'template',NULL,?,?,?,?,?)""",
                (
                    str(roi_id),
                    str(setup_id),
                    revision,
                    json.dumps([point.model_dump() for point in polygon], separators=(",", ":")),
                    None,
                    now,
                ),
            )
            connection.execute(
                "UPDATE camera_setups SET revision=?, template_revision_id=?, updated_at=? WHERE id=?",
                (revision, str(roi_id), now, str(setup_id)),
            )
            result = self._setup_view(connection, self._require_setup(connection, setup_id))
            self._store_operation(
                connection,
                str(setup_id),
                request.operation_id,
                "save_template",
                request,
                result,
            )
            return result

    def save_roi(self, clip_id: UUID, request: RoiWrite) -> ClipView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                str(clip_id),
                request.operation_id,
                "save_roi",
                request,
                ClipView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if clip["preparation_state"] != "ready":
                raise ClipNotReady("clip media is not ready")
            if clip["source_state"] != "available":
                raise SourceUnavailable("clip source is unavailable")
            self._require_setup(connection, request.camera_setup_id)
            if request.template_revision_id is not None:
                template = connection.execute(
                    "SELECT * FROM roi_revisions WHERE id=? AND kind='template'",
                    (str(request.template_revision_id),),
                ).fetchone()
                if template is None or template["camera_setup_id"] != str(request.camera_setup_id):
                    raise NotFound("camera setup template revision was not found")
            polygon = validate_polygon(request.polygon)
            revision_row = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS revision FROM roi_revisions WHERE clip_id=?",
                (str(clip_id),),
            ).fetchone()
            roi_revision = int(revision_row["revision"])
            roi_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO roi_revisions(
                    id,kind,clip_id,camera_setup_id,revision,polygon_json,
                    template_revision_id,created_at
                ) VALUES(?,'clip',?,?,?,?,?,?)""",
                (
                    str(roi_id),
                    str(clip_id),
                    str(request.camera_setup_id),
                    roi_revision,
                    json.dumps([point.model_dump() for point in polygon], separators=(",", ":")),
                    str(request.template_revision_id) if request.template_revision_id else None,
                    now,
                ),
            )
            connection.execute(
                "UPDATE annotation_clips SET revision=revision+1, roi_revision_id=?, updated_at=? WHERE id=?",
                (str(roi_id), now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            self._store_operation(
                connection,
                str(clip_id),
                request.operation_id,
                "save_roi",
                request,
                result,
            )
            return result

    def complete_preparation(
        self,
        clip_id: UUID,
        *,
        source_sha256: str,
        media: MediaView,
        artifact_manifest: dict[str, Any],
        prepared_bytes: int,
        generation_id: UUID | None = None,
    ) -> ClipView:
        with self.database.write_transaction() as connection:
            clip = self._require_clip(connection, clip_id)
            if clip["preparation_state"] != "preparing":
                raise RevisionConflict("clip is not awaiting preparation")
            now = _now()
            connection.execute(
                """UPDATE annotation_clips SET
                    revision=revision+1, preparation_state='ready', source_state='available',
                    failure_code=NULL, source_sha256=?, media_json=?, artifact_manifest_json=?,
                    generation_id=?, preview_relative_path='preview.mp4', prepared_bytes=?, updated_at=?
                WHERE id=?""",
                (
                    source_sha256,
                    media.model_dump_json(),
                    json.dumps(artifact_manifest, sort_keys=True, separators=(",", ":")),
                    str(generation_id or uuid4()),
                    prepared_bytes,
                    now,
                    str(clip_id),
                ),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            return result

    def fail_preparation(
        self,
        clip_id: UUID,
        failure_code: str,
        *,
        source_state: str | None = None,
    ) -> ClipView:
        with self.database.write_transaction() as connection:
            clip = self._require_clip(connection, clip_id)
            if clip["preparation_state"] != "preparing":
                return self._clip_view(connection, clip)
            now = _now()
            connection.execute(
                """UPDATE annotation_clips SET revision=revision+1,
                    preparation_state='failed', failure_code=?,
                    source_state=COALESCE(?,source_state), updated_at=? WHERE id=?""",
                (failure_code, source_state, now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            return result

    def retry_preparation(self, clip_id: UUID, request: RetryPreparation) -> ClipView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                str(clip_id),
                request.operation_id,
                "retry_preparation",
                request,
                ClipView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if clip["preparation_state"] not in {"failed", "released"}:
                raise RevisionConflict("clip preparation cannot be retried from its current state")
            if clip["source_state"] == "hash_mismatch":
                raise SourceUnavailable("changed source cannot be rebound to an annotation clip")
            now = _now()
            connection.execute(
                """UPDATE annotation_clips SET revision=revision+1,
                    preparation_state='preparing', failure_code=NULL,
                    artifact_manifest_json=NULL, generation_id=NULL,
                    preview_relative_path=NULL, prepared_bytes=CASE
                        WHEN prepared_bytes IS NULL THEN NULL ELSE 0 END,
                    updated_at=? WHERE id=?""",
                (now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            self._store_operation(
                connection,
                str(clip_id),
                request.operation_id,
                "retry_preparation",
                request,
                result,
            )
            return result

    def release_prepared_media(
        self, clip_id: UUID, request: ReleasePreparedMedia
    ) -> ClipView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection,
                str(clip_id),
                request.operation_id,
                "release_prepared_media",
                request,
                ClipView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if clip["preparation_state"] not in {"ready", "failed"}:
                raise RevisionConflict("clip media cannot be released from its current state")
            now = _now()
            connection.execute(
                """UPDATE annotation_clips SET revision=revision+1,
                    preparation_state='releasing', failure_code=NULL, updated_at=? WHERE id=?""",
                (now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            self._store_operation(
                connection,
                str(clip_id),
                request.operation_id,
                "release_prepared_media",
                request,
                result,
            )
            return result

    def complete_release(self, clip_id: UUID) -> ClipView:
        with self.database.write_transaction() as connection:
            clip = self._require_clip(connection, clip_id)
            if clip["preparation_state"] != "releasing":
                raise RevisionConflict("clip is not awaiting prepared-media release")
            now = _now()
            connection.execute(
                """UPDATE annotation_clips SET revision=revision+1,
                    preparation_state='released', failure_code=NULL,
                    artifact_manifest_json=NULL, generation_id=NULL,
                    preview_relative_path=NULL, prepared_bytes=0, updated_at=? WHERE id=?""",
                (now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            return result

    def fail_release(self, clip_id: UUID, failure_code: str) -> ClipView:
        with self.database.write_transaction() as connection:
            clip = self._require_clip(connection, clip_id)
            if clip["preparation_state"] != "releasing":
                return self._clip_view(connection, clip)
            if clip["failure_code"] == failure_code:
                return self._clip_view(connection, clip)
            now = _now()
            connection.execute(
                "UPDATE annotation_clips SET revision=revision+1, failure_code=?, updated_at=? WHERE id=?",
                (failure_code, now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            return result

    def mark_source_state(self, clip_id: UUID, source_state: str) -> ClipView:
        if source_state not in {"available", "missing", "hash_mismatch"}:
            raise ValueError("invalid source state")
        with self.database.write_transaction() as connection:
            clip = self._require_clip(connection, clip_id)
            if clip["source_state"] == source_state:
                return self._clip_view(connection, clip)
            now = _now()
            connection.execute(
                "UPDATE annotation_clips SET revision=revision+1, source_state=?, updated_at=? WHERE id=?",
                (source_state, now, str(clip_id)),
            )
            result = self._clip_view(connection, self._require_clip(connection, clip_id))
            self._store_clip_revision(connection, result, now)
            return result

    def count_roi_revisions(self, clip_id: UUID) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM roi_revisions WHERE clip_id=?", (str(clip_id),)
                ).fetchone()[0]
            )
