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
    ActionAnnotationCreate,
    ActionAnnotationUpdate,
    ActionAnnotationView,
    ActionMutation,
    ActionWorkspaceView,
    CameraSetupCreate,
    CameraSetupListView,
    CameraSetupView,
    ClipListView,
    ClipView,
    InteractionCreate,
    InteractionUpdate,
    InteractionView,
    MediaView,
    Point,
    RegisterClip,
    ReleasePreparedMedia,
    ReviewCoverageView,
    ReviewCoverageWrite,
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


class AnnotationOverlapConflict(RevisionConflict):
    def __init__(self, conflicting_annotation_id: UUID) -> None:
        self.conflicting_annotation_id = conflicting_annotation_id
        super().__init__(f"action overlaps conflicting annotation {conflicting_annotation_id}")


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

    @staticmethod
    def _interaction_view(row: sqlite3.Row) -> InteractionView:
        return InteractionView(
            id=row["id"],
            clip_id=row["clip_id"],
            revision=row["revision"],
            hand=row["hand"],
            tracking_job_id=row["tracking_job_id"],
            local_track_id=row["local_track_id"],
        )

    @staticmethod
    def _action_view(row: sqlite3.Row) -> ActionAnnotationView:
        return ActionAnnotationView(
            id=row["id"],
            clip_id=row["clip_id"],
            interaction_id=row["interaction_id"],
            roi_revision_id=row["roi_revision_id"],
            revision=row["revision"],
            label=row["label"],
            start_frame=row["start_frame"],
            end_frame=row["end_frame"],
            crossing_frame=row["crossing_frame"],
            object_kind=row["object_kind"],
            visibility=row["visibility"],
            uncertain_labels=json.loads(row["uncertain_labels_json"]),
            unclear_reason=row["unclear_reason"],
            review_state=row["review_state"],
            guideline_version=row["guideline_version"],
            deleted=bool(row["deleted"]),
        )

    @staticmethod
    def _coverage_view(row: sqlite3.Row) -> ReviewCoverageView:
        return ReviewCoverageView(
            id=row["id"],
            clip_id=row["clip_id"],
            roi_revision_id=row["roi_revision_id"],
            revision=row["revision"],
            start_frame=row["start_frame"],
            end_frame=row["end_frame"],
            reviewed_labels=json.loads(row["reviewed_labels_json"]),
            guideline_version=row["guideline_version"],
            active=bool(row["active"]),
        )

    @classmethod
    def _workspace_view(
        cls, connection: sqlite3.Connection, clip: sqlite3.Row
    ) -> ActionWorkspaceView:
        if clip["roi_revision_id"] is None:
            raise ClipNotReady("clip requires a basket ROI before annotation")
        interactions = connection.execute(
            "SELECT * FROM interactions WHERE clip_id=? ORDER BY created_at,id",
            (clip["id"],),
        ).fetchall()
        annotations = connection.execute(
            "SELECT * FROM action_annotations WHERE clip_id=? ORDER BY start_frame,end_frame,id",
            (clip["id"],),
        ).fetchall()
        coverage = connection.execute(
            "SELECT * FROM review_coverage WHERE clip_id=? ORDER BY start_frame,end_frame,id",
            (clip["id"],),
        ).fetchall()
        return ActionWorkspaceView(
            clip_id=clip["id"],
            clip_revision=clip["revision"],
            roi_revision_id=clip["roi_revision_id"],
            interactions=[cls._interaction_view(row) for row in interactions],
            annotations=[cls._action_view(row) for row in annotations],
            review_coverage=[cls._coverage_view(row) for row in coverage],
        )

    @staticmethod
    def _require_writable_clip(clip: sqlite3.Row) -> None:
        if clip["preparation_state"] != "ready":
            raise ClipNotReady("clip is not ready for annotation")
        if clip["source_state"] != "available":
            raise SourceUnavailable("clip source is unavailable")
        if clip["roi_revision_id"] is None:
            raise ClipNotReady("clip requires a basket ROI before annotation")

    @staticmethod
    def _require_interaction(
        connection: sqlite3.Connection, interaction_id: UUID
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM interactions WHERE id=?", (str(interaction_id),)
        ).fetchone()
        if row is None:
            raise NotFound("interaction was not found")
        return row

    @staticmethod
    def _require_action(connection: sqlite3.Connection, annotation_id: UUID) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM action_annotations WHERE id=?", (str(annotation_id),)
        ).fetchone()
        if row is None:
            raise NotFound("action annotation was not found")
        return row

    @classmethod
    def _store_action_revision(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        action: str,
        created_at: str,
    ) -> None:
        view = cls._action_view(row)
        connection.execute(
            "INSERT INTO action_revisions(annotation_id,revision,snapshot_json,action,created_at) VALUES(?,?,?,?,?)",
            (str(view.id), view.revision, view.model_dump_json(), action, created_at),
        )

    @classmethod
    def _store_coverage_revision(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        action: str,
        created_at: str,
    ) -> None:
        view = cls._coverage_view(row)
        connection.execute(
            "INSERT INTO coverage_revisions(coverage_id,revision,snapshot_json,action,created_at) VALUES(?,?,?,?,?)",
            (str(view.id), view.revision, view.model_dump_json(), action, created_at),
        )

    @classmethod
    def _advance_clip(
        cls, connection: sqlite3.Connection, clip_id: UUID, now: str
    ) -> sqlite3.Row:
        connection.execute(
            "UPDATE annotation_clips SET revision=revision+1,updated_at=? WHERE id=?",
            (now, str(clip_id)),
        )
        clip = cls._require_clip(connection, clip_id)
        cls._store_clip_revision(connection, cls._clip_view(connection, clip), now)
        return clip

    @staticmethod
    def _validate_action_owner_and_frames(
        connection: sqlite3.Connection,
        clip: sqlite3.Row,
        request: ActionAnnotationCreate | ActionAnnotationUpdate,
    ) -> None:
        media = MediaView.model_validate_json(clip["media_json"])
        if request.end_frame >= media.frame_count:
            raise ValueError("action interval exceeds clip frame count")
        if request.interaction_id is not None:
            interaction = AnnotationRepository._require_interaction(
                connection, request.interaction_id
            )
            if interaction["clip_id"] != clip["id"]:
                raise NotFound("interaction does not belong to annotation clip")

    def _validate_tracking_reference(
        self,
        clip: sqlite3.Row,
        tracking_job_id: UUID | None,
        local_track_id: int | None,
    ) -> None:
        if tracking_job_id is None:
            return
        if str(tracking_job_id) != clip["source_job_id"]:
            raise NotFound("tracking reference does not belong to annotation clip")
        try:
            job = self.jobs.get_private(str(tracking_job_id))
        except JobNotFoundError as exc:
            raise NotFound("tracking job was not found") from exc
        if job.status.value != "ready":
            raise NotFound("tracking reference has no completed evidence")
        evidence_path = Path(job.output_path).with_name("tracking.evidence.jsonl")
        try:
            with evidence_path.open("r", encoding="utf-8") as evidence:
                for line in evidence:
                    payload = json.loads(line)
                    if any(
                        box.get("track_id") == local_track_id
                        for box in payload.get("boxes", [])
                        if isinstance(box, dict)
                    ):
                        return
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise NotFound("tracking reference evidence is unavailable") from exc
        raise NotFound("local track id has no evidence")

    @staticmethod
    def _reject_unclear_confirmed_overlap(
        connection: sqlite3.Connection,
        *,
        clip_id: UUID,
        interaction_id: UUID | str | None,
        label: str,
        start_frame: int,
        end_frame: int,
        uncertain_labels: list[str],
        exclude_id: UUID | None = None,
        becoming_confirmed: bool = False,
    ) -> None:
        if interaction_id is None:
            return
        rows = connection.execute(
            """SELECT * FROM action_annotations
            WHERE clip_id=? AND interaction_id=? AND deleted=0
              AND end_frame>=? AND start_frame<=? AND id<>?""",
            (
                str(clip_id), str(interaction_id), start_frame, end_frame,
                str(exclude_id) if exclude_id else "",
            ),
        ).fetchall()
        for row in rows:
            conflict = False
            if label == "unclear":
                conflict = (
                    row["review_state"] == "confirmed"
                    and row["label"] in set(uncertain_labels)
                )
            elif becoming_confirmed and row["label"] == "unclear":
                conflict = label in set(json.loads(row["uncertain_labels_json"]))
            if conflict:
                raise AnnotationOverlapConflict(UUID(row["id"]))

    @classmethod
    def _invalidate_for_roi(
        cls, connection: sqlite3.Connection, clip_id: UUID, now: str
    ) -> None:
        action_rows = connection.execute(
            "SELECT * FROM action_annotations WHERE clip_id=? AND deleted=0",
            (str(clip_id),),
        ).fetchall()
        for row in action_rows:
            connection.execute(
                "UPDATE action_annotations SET revision=revision+1,review_state='needs_review',updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            changed = cls._require_action(connection, UUID(row["id"]))
            cls._store_action_revision(connection, changed, "roi_invalidate", now)
        coverage_rows = connection.execute(
            "SELECT * FROM review_coverage WHERE clip_id=? AND active=1",
            (str(clip_id),),
        ).fetchall()
        for row in coverage_rows:
            connection.execute(
                "UPDATE review_coverage SET revision=revision+1,active=0,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            changed = connection.execute(
                "SELECT * FROM review_coverage WHERE id=?", (row["id"],)
            ).fetchone()
            cls._store_coverage_revision(connection, changed, "invalidate", now)
        connection.execute(
            """UPDATE assistance_suggestions SET review_state='stale',updated_at=?
               WHERE clip_id=? AND review_state='pending'""",
            (now, str(clip_id)),
        )
        connection.execute(
            """UPDATE assistance_runs SET status='failed',error_code='stale_binding',updated_at=?
               WHERE clip_id=? AND status IN ('queued','running')""",
            (now, str(clip_id)),
        )

    @staticmethod
    def _action_classes(label: str, uncertain_labels_json: str) -> set[str]:
        return set(json.loads(uncertain_labels_json)) if label == "unclear" else {label}

    @classmethod
    def _invalidate_coverage_range(
        cls,
        connection: sqlite3.Connection,
        clip_id: UUID,
        start_frame: int,
        end_frame: int,
        labels: set[str],
        now: str,
    ) -> None:
        rows = connection.execute(
            """SELECT * FROM review_coverage
            WHERE clip_id=? AND active=1 AND end_frame>=? AND start_frame<=?""",
            (str(clip_id), start_frame, end_frame),
        ).fetchall()
        for row in rows:
            covered = set(json.loads(row["reviewed_labels_json"]))
            affected = covered & labels
            if not affected:
                continue
            connection.execute(
                "UPDATE review_coverage SET revision=revision+1,active=0,updated_at=? WHERE id=?",
                (now, row["id"]),
            )
            invalidated = connection.execute(
                "SELECT * FROM review_coverage WHERE id=?", (row["id"],)
            ).fetchone()
            cls._store_coverage_revision(connection, invalidated, "invalidate", now)

            pieces: list[tuple[int, int, set[str]]] = []
            if row["start_frame"] < start_frame:
                pieces.append((row["start_frame"], start_frame - 1, covered))
            overlap_start = max(row["start_frame"], start_frame)
            overlap_end = min(row["end_frame"], end_frame)
            unaffected = covered - affected
            if unaffected:
                pieces.append((overlap_start, overlap_end, unaffected))
            if row["end_frame"] > end_frame:
                pieces.append((end_frame + 1, row["end_frame"], covered))
            for piece_start, piece_end, piece_labels in pieces:
                coverage_id = uuid4()
                connection.execute(
                    """INSERT INTO review_coverage(
                        id,clip_id,roi_revision_id,revision,start_frame,end_frame,
                        reviewed_labels_json,guideline_version,active,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,1,1,?,?)""",
                    (
                        str(coverage_id), str(clip_id), row["roi_revision_id"], 1,
                        piece_start, piece_end,
                        json.dumps(sorted(piece_labels), separators=(",", ":")), now, now,
                    ),
                )
                piece = connection.execute(
                    "SELECT * FROM review_coverage WHERE id=?", (str(coverage_id),)
                ).fetchone()
                cls._store_coverage_revision(connection, piece, "create", now)

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
            if clip["roi_revision_id"] is not None:
                self._invalidate_for_roi(connection, clip_id, now)
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

    def get_action_workspace(self, clip_id: UUID) -> ActionWorkspaceView:
        with self.database.read_connection() as connection:
            return self._workspace_view(connection, self._require_clip(connection, clip_id))

    def create_interaction(
        self, clip_id: UUID, request: InteractionCreate
    ) -> ActionWorkspaceView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                "create_interaction", request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            self._validate_tracking_reference(
                clip, request.tracking_job_id, request.local_track_id
            )
            interaction_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO interactions(
                    id,clip_id,revision,hand,tracking_job_id,local_track_id,created_at,updated_at
                ) VALUES(?,?,1,?,?,?,?,?)""",
                (
                    str(interaction_id), str(clip_id), request.hand,
                    str(request.tracking_job_id) if request.tracking_job_id else None,
                    request.local_track_id, now, now,
                ),
            )
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                "create_interaction", request, result,
            )
            return result

    def update_interaction(
        self, clip_id: UUID, interaction_id: UUID, request: InteractionUpdate
    ) -> ActionWorkspaceView:
        operation_action = f"update_interaction:{interaction_id}"
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                operation_action, request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            interaction = self._require_interaction(connection, interaction_id)
            if interaction["clip_id"] != str(clip_id):
                raise NotFound("interaction does not belong to annotation clip")
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if interaction["revision"] != request.expected_interaction_revision:
                raise RevisionConflict("interaction revision is stale")
            self._validate_tracking_reference(
                clip, request.tracking_job_id, request.local_track_id
            )
            now = _now()
            related = connection.execute(
                "SELECT * FROM action_annotations WHERE interaction_id=? AND deleted=0",
                (str(interaction_id),),
            ).fetchall()
            for action_row in related:
                self._invalidate_coverage_range(
                    connection,
                    clip_id,
                    action_row["start_frame"],
                    action_row["end_frame"],
                    self._action_classes(
                        action_row["label"], action_row["uncertain_labels_json"]
                    ),
                    now,
                )
                if action_row["review_state"] == "confirmed":
                    connection.execute(
                        """UPDATE action_annotations SET revision=revision+1,
                            review_state='needs_review',updated_at=? WHERE id=?""",
                        (now, action_row["id"]),
                    )
                    invalidated = self._require_action(
                        connection, UUID(action_row["id"])
                    )
                    self._store_action_revision(
                        connection, invalidated, "interaction_invalidate", now
                    )
            connection.execute(
                """UPDATE interactions SET revision=revision+1,hand=?,tracking_job_id=?,
                    local_track_id=?,updated_at=? WHERE id=?""",
                (
                    request.hand,
                    str(request.tracking_job_id) if request.tracking_job_id else None,
                    request.local_track_id, now, str(interaction_id),
                ),
            )
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                operation_action, request, result,
            )
            return result

    def create_action(
        self, clip_id: UUID, request: ActionAnnotationCreate
    ) -> ActionWorkspaceView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                "create_action", request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            self._validate_action_owner_and_frames(connection, clip, request)
            self._reject_unclear_confirmed_overlap(
                connection,
                clip_id=clip_id,
                interaction_id=request.interaction_id,
                label=request.label,
                start_frame=request.start_frame,
                end_frame=request.end_frame,
                uncertain_labels=list(request.uncertain_labels),
            )
            annotation_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO action_annotations(
                    id,clip_id,interaction_id,roi_revision_id,revision,label,start_frame,
                    end_frame,crossing_frame,object_kind,visibility,uncertain_labels_json,
                    unclear_reason,review_state,guideline_version,deleted,created_at,updated_at
                ) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?, 'draft',1,0,?,?)""",
                (
                    str(annotation_id), str(clip_id),
                    str(request.interaction_id) if request.interaction_id else None,
                    clip["roi_revision_id"], request.label, request.start_frame,
                    request.end_frame, request.crossing_frame, request.object_kind,
                    request.visibility,
                    json.dumps(request.uncertain_labels, separators=(",", ":")),
                    request.unclear_reason, now, now,
                ),
            )
            if request.suggestion_id is not None:
                from .assistance_store import claim_suggestion_for_annotation

                claim_suggestion_for_annotation(
                    connection,
                    suggestion_id=request.suggestion_id,
                    clip_id=clip_id,
                    annotation_id=annotation_id,
                    source_sha256=clip["source_sha256"],
                    roi_revision_id=UUID(clip["roi_revision_id"]),
                    guideline_version=1,
                    now=now,
                )
            row = self._require_action(connection, annotation_id)
            self._store_action_revision(connection, row, "create", now)
            self._invalidate_coverage_range(
                connection, clip_id, row["start_frame"], row["end_frame"],
                self._action_classes(row["label"], row["uncertain_labels_json"]), now,
            )
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                "create_action", request, result,
            )
            return result

    def update_action(
        self,
        clip_id: UUID,
        annotation_id: UUID,
        request: ActionAnnotationUpdate,
    ) -> ActionWorkspaceView:
        operation_action = f"update_action:{annotation_id}"
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                operation_action, request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            row = self._require_action(connection, annotation_id)
            if row["clip_id"] != str(clip_id):
                raise NotFound("action annotation does not belong to annotation clip")
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if row["revision"] != request.expected_annotation_revision:
                raise RevisionConflict("action annotation revision is stale")
            if row["deleted"]:
                raise RevisionConflict("deleted action annotation must be restored before editing")
            self._validate_action_owner_and_frames(connection, clip, request)
            self._reject_unclear_confirmed_overlap(
                connection,
                clip_id=clip_id,
                interaction_id=request.interaction_id,
                label=request.label,
                start_frame=request.start_frame,
                end_frame=request.end_frame,
                uncertain_labels=list(request.uncertain_labels),
                exclude_id=annotation_id,
            )
            now = _now()
            self._invalidate_coverage_range(
                connection, clip_id, row["start_frame"], row["end_frame"],
                self._action_classes(row["label"], row["uncertain_labels_json"]), now,
            )
            connection.execute(
                """UPDATE action_annotations SET interaction_id=?,roi_revision_id=?,
                    revision=revision+1,label=?,start_frame=?,end_frame=?,crossing_frame=?,
                    object_kind=?,visibility=?,uncertain_labels_json=?,unclear_reason=?,
                    review_state='draft',updated_at=? WHERE id=?""",
                (
                    str(request.interaction_id) if request.interaction_id else None,
                    clip["roi_revision_id"], request.label, request.start_frame,
                    request.end_frame, request.crossing_frame, request.object_kind,
                    request.visibility,
                    json.dumps(request.uncertain_labels, separators=(",", ":")),
                    request.unclear_reason, now, str(annotation_id),
                ),
            )
            changed = self._require_action(connection, annotation_id)
            self._store_action_revision(connection, changed, "update", now)
            self._invalidate_coverage_range(
                connection, clip_id, changed["start_frame"], changed["end_frame"],
                self._action_classes(changed["label"], changed["uncertain_labels_json"]), now,
            )
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                operation_action, request, result,
            )
            return result

    def _change_action_state(
        self,
        clip_id: UUID,
        annotation_id: UUID,
        request: ActionMutation,
        action: str,
    ) -> ActionWorkspaceView:
        operation_action = f"{action}:{annotation_id}"
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                operation_action, request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            row = self._require_action(connection, annotation_id)
            if row["clip_id"] != str(clip_id):
                raise NotFound("action annotation does not belong to annotation clip")
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if row["revision"] != request.expected_annotation_revision:
                raise RevisionConflict("action annotation revision is stale")
            if action == "delete_action" and row["deleted"]:
                raise RevisionConflict("action annotation is already deleted")
            if action == "restore_action" and not row["deleted"]:
                raise RevisionConflict("action annotation is not deleted")
            if action == "confirm_action" and row["deleted"]:
                raise RevisionConflict("deleted action annotation cannot be confirmed")
            if action in {"restore_action", "confirm_action"}:
                self._reject_unclear_confirmed_overlap(
                    connection,
                    clip_id=clip_id,
                    interaction_id=row["interaction_id"],
                    label=row["label"],
                    start_frame=row["start_frame"],
                    end_frame=row["end_frame"],
                    uncertain_labels=json.loads(row["uncertain_labels_json"]),
                    exclude_id=annotation_id,
                    becoming_confirmed=action == "confirm_action",
                )
            now = _now()
            if action != "confirm_action":
                self._invalidate_coverage_range(
                    connection, clip_id, row["start_frame"], row["end_frame"],
                    self._action_classes(row["label"], row["uncertain_labels_json"]), now,
                )
            deleted = 1 if action == "delete_action" else 0
            review_state = "confirmed" if action == "confirm_action" else "draft"
            roi_revision_id = (
                clip["roi_revision_id"] if action == "confirm_action" else row["roi_revision_id"]
            )
            connection.execute(
                """UPDATE action_annotations SET revision=revision+1,deleted=?,
                    review_state=?,roi_revision_id=?,updated_at=? WHERE id=?""",
                (deleted, review_state, roi_revision_id, now, str(annotation_id)),
            )
            changed = self._require_action(connection, annotation_id)
            revision_action = {
                "delete_action": "delete",
                "restore_action": "restore",
                "confirm_action": "confirm",
            }[action]
            self._store_action_revision(connection, changed, revision_action, now)
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                operation_action, request, result,
            )
            return result

    def delete_action(self, clip_id: UUID, annotation_id: UUID, request: ActionMutation) -> ActionWorkspaceView:
        return self._change_action_state(clip_id, annotation_id, request, "delete_action")

    def restore_action(self, clip_id: UUID, annotation_id: UUID, request: ActionMutation) -> ActionWorkspaceView:
        return self._change_action_state(clip_id, annotation_id, request, "restore_action")

    def confirm_action(self, clip_id: UUID, annotation_id: UUID, request: ActionMutation) -> ActionWorkspaceView:
        return self._change_action_state(clip_id, annotation_id, request, "confirm_action")

    def create_review_coverage(
        self, clip_id: UUID, request: ReviewCoverageWrite
    ) -> ActionWorkspaceView:
        with self.database.write_transaction() as connection:
            replay = self._replay(
                connection, str(clip_id), request.operation_id,
                "create_review_coverage", request, ActionWorkspaceView,
            )
            if replay is not None:
                return replay
            clip = self._require_clip(connection, clip_id)
            self._require_writable_clip(clip)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            media = MediaView.model_validate_json(clip["media_json"])
            if request.end_frame >= media.frame_count:
                raise ValueError("review interval exceeds clip frame count")
            coverage_id = uuid4()
            now = _now()
            connection.execute(
                """INSERT INTO review_coverage(
                    id,clip_id,roi_revision_id,revision,start_frame,end_frame,
                    reviewed_labels_json,guideline_version,active,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,1,1,?,?)""",
                (
                    str(coverage_id), str(clip_id), clip["roi_revision_id"], 1,
                    request.start_frame, request.end_frame,
                    json.dumps(request.reviewed_labels, separators=(",", ":")), now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_coverage WHERE id=?", (str(coverage_id),)
            ).fetchone()
            self._store_coverage_revision(connection, row, "create", now)
            clip = self._advance_clip(connection, clip_id, now)
            result = self._workspace_view(connection, clip)
            self._store_operation(
                connection, str(clip_id), request.operation_id,
                "create_review_coverage", request, result,
            )
            return result

    def count_roi_revisions(self, clip_id: UUID) -> int:
        with self.database.read_connection() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM roi_revisions WHERE clip_id=?", (str(clip_id),)
                ).fetchone()[0]
            )
