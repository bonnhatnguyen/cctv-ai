from __future__ import annotations

import hashlib
import json
import sqlite3
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .contracts import (
    AssistanceRunCancel,
    AssistanceRunCreate,
    AssistanceRunListView,
    AssistanceRunView,
    AssistanceSuggestionListView,
    AssistanceSuggestionView,
    SuggestionReject,
)
from .database import AnnotationDatabase
from .repository import ClipNotReady, NotFound, PayloadConflict, RevisionConflict, SourceUnavailable


class AssistanceStateConflict(RuntimeError):
    pass


class AssistanceQueueFull(RuntimeError):
    pass


@dataclass(frozen=True)
class AssistanceRunBinding:
    source_sha256: str
    roi_revision_id: UUID
    guideline_version: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_hash(request: AssistanceRunCreate) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


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


def _encode_suggestion_cursor(view_start_frame: int, resource_id: str) -> str:
    raw = json.dumps([view_start_frame, resource_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_suggestion_cursor(cursor: str | None) -> tuple[int, str] | None:
    if cursor is None:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if (
            not isinstance(value, list) or len(value) != 2
            or not isinstance(value[0], int) or value[0] < 0
            or not isinstance(value[1], str)
        ):
            raise ValueError
        return value[0], value[1]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid suggestion pagination cursor") from exc


class AssistanceRepository:
    def __init__(
        self,
        database: AnnotationDatabase,
        *,
        queue_limit: int = 4,
        stride: int = 5,
        max_frames: int = 1800,
        dino_device: str = "cuda:0",
        asset_hashes: dict[str, str] | None = None,
    ) -> None:
        if stride < 1 or max_frames < 1:
            raise ValueError("assistance sampling limits must be positive")
        if dino_device not in {"cpu", "cuda:0"}:
            raise ValueError("invalid DINO device")
        self.database = database
        self.queue_limit = queue_limit
        self.stride = stride
        self.max_frames = max_frames
        self.dino_device = dino_device
        self.asset_hashes = dict(asset_hashes or {})

    def _config_sha256(self, model: str, device: str, stride: int | None = None) -> str:
        payload = json.dumps(
            {
                "schema_version": 1,
                "model": model,
                "device": device,
                "stride": self.stride if stride is None else stride,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _run_view(row: sqlite3.Row) -> AssistanceRunView:
        return AssistanceRunView(
            id=row["id"], clip_id=row["clip_id"], model=row["model"],
            status=row["status"], start_frame=row["start_frame"],
            end_frame=row["end_frame"], processed_frames=row["processed_frames"],
            scheduled_frames=row["scheduled_frames"], error_code=row["error_code"],
            source_sha256=row["source_sha256"], roi_revision_id=row["roi_revision_id"],
            guideline_version=row["guideline_version"], device=row["device"],
            config_sha256=row["config_sha256"], asset_sha256=row["asset_sha256"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _suggestion_view(row: sqlite3.Row) -> AssistanceSuggestionView:
        return AssistanceSuggestionView(
            id=row["id"], run_id=row["run_id"], clip_id=row["clip_id"],
            proposal_key=row["proposal_key"], label=row["label"],
            action_start_frame=row["action_start_frame"],
            action_end_frame=row["action_end_frame"],
            view_start_frame=row["view_start_frame"], view_end_frame=row["view_end_frame"],
            crossing_estimate=row["crossing_estimate"],
            crossing_bracket_start=row["crossing_bracket_start"],
            crossing_bracket_end=row["crossing_bracket_end"], reason=row["reason"],
            review_state=row["review_state"],
            accepted_annotation_id=row["accepted_annotation_id"],
        )

    @staticmethod
    def _require_clip(connection: sqlite3.Connection, clip_id: UUID) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM annotation_clips WHERE id=?", (str(clip_id),)
        ).fetchone()
        if row is None:
            raise NotFound("annotation clip was not found")
        return row

    @staticmethod
    def _require_run(connection: sqlite3.Connection, run_id: UUID) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM assistance_runs WHERE id=?", (str(run_id),)
        ).fetchone()
        if row is None:
            raise NotFound("assistance run was not found")
        return row

    def create_run(self, clip_id: UUID, request: AssistanceRunCreate) -> AssistanceRunView:
        request_sha = _request_hash(request)
        with self.database.write_transaction() as connection:
            replay = connection.execute(
                "SELECT * FROM assistance_runs WHERE clip_id=? AND operation_id=?",
                (str(clip_id), str(request.operation_id)),
            ).fetchone()
            if replay is not None:
                if replay["request_sha256"] != request_sha:
                    raise PayloadConflict("operation id was already used with a different payload")
                return self._run_view(replay)
            clip = self._require_clip(connection, clip_id)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            if clip["preparation_state"] != "ready" or clip["roi_revision_id"] is None:
                raise ClipNotReady("clip is not ready for assistance")
            if clip["source_state"] != "available" or not clip["source_sha256"]:
                raise SourceUnavailable("clip source is unavailable")
            queued = connection.execute(
                "SELECT COUNT(*) AS count FROM assistance_runs WHERE status IN ('queued','running')"
            ).fetchone()["count"]
            if queued >= self.queue_limit:
                raise AssistanceQueueFull("assistance queue is full")
            media = json.loads(clip["media_json"] or "null")
            if media is None or request.end_frame >= media["frame_count"]:
                raise ValueError("assistance frame interval is outside the clip")
            scheduled_frames = (request.end_frame - request.start_frame) // self.stride + 1
            if scheduled_frames > self.max_frames:
                raise ValueError("assistance scheduled frame limit exceeded")
            run_id = uuid4()
            now = _now()
            device = self.dino_device if request.model == "dino" else "cpu"
            config_sha256 = self._config_sha256(request.model, device)
            asset_sha256 = self.asset_hashes.get(request.model)
            connection.execute(
                """INSERT INTO assistance_runs(
                    id,clip_id,operation_id,request_sha256,source_sha256,roi_revision_id,
                    guideline_version,model,device,stride,start_frame,end_frame,status,
                    processed_frames,scheduled_frames,config_sha256,asset_sha256,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,1,?,?,?,?,?, 'queued',0,?,?,?,?,?)""",
                (
                    str(run_id), str(clip_id), str(request.operation_id), request_sha,
                    clip["source_sha256"], clip["roi_revision_id"], request.model,
                    device, self.stride, request.start_frame, request.end_frame, scheduled_frames,
                    config_sha256, asset_sha256, now, now,
                ),
            )
            return self._run_view(self._require_run(connection, run_id))

    def replay_run(
        self, clip_id: UUID, request: AssistanceRunCreate
    ) -> AssistanceRunView | None:
        request_sha = _request_hash(request)
        with self.database.read_connection() as connection:
            replay = connection.execute(
                "SELECT * FROM assistance_runs WHERE clip_id=? AND operation_id=?",
                (str(clip_id), str(request.operation_id)),
            ).fetchone()
            if replay is None:
                return None
            if replay["request_sha256"] != request_sha:
                raise PayloadConflict("operation id was already used with a different payload")
            return self._run_view(replay)

    def get_run(self, run_id: UUID) -> AssistanceRunView:
        with self.database.read_connection() as connection:
            return self._run_view(self._require_run(connection, run_id))

    def get_clip_run(self, clip_id: UUID, run_id: UUID) -> AssistanceRunView:
        run = self.get_run(run_id)
        if run.clip_id != clip_id:
            raise NotFound("assistance run was not found for this clip")
        return run

    def get_suggestion(self, suggestion_id: UUID) -> AssistanceSuggestionView:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM assistance_suggestions WHERE id=?",
                (str(suggestion_id),),
            ).fetchone()
            if row is None:
                raise NotFound("assistance suggestion was not found")
            return self._suggestion_view(row)

    def run_binding(self, run_id: UUID) -> AssistanceRunBinding:
        run = self.get_run(run_id)
        return AssistanceRunBinding(run.source_sha256, run.roi_revision_id, run.guideline_version)

    def list_runs(
        self,
        clip_id: UUID,
        *,
        active_only: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> AssistanceRunListView:
        if limit < 1 or limit > 100:
            raise ValueError("run history limit must be between 1 and 100")
        key = _decode_cursor(cursor)
        with self.database.read_connection() as connection:
            self._require_clip(connection, clip_id)
            conditions = ["clip_id=?"]
            parameters: list[Any] = [str(clip_id)]
            if active_only:
                conditions.append("status IN ('queued','running')")
            if key is not None:
                conditions.append("(created_at < ? OR (created_at = ? AND id < ?))")
                parameters.extend([key[0], key[0], key[1]])
            rows = connection.execute(
                f"SELECT * FROM assistance_runs WHERE {' AND '.join(conditions)} "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
            page = rows[:limit]
            next_cursor = (
                _encode_cursor(page[-1]["created_at"], page[-1]["id"])
                if len(rows) > limit else None
            )
            return AssistanceRunListView(
                items=[self._run_view(row) for row in page], next_cursor=next_cursor
            )

    def next_queued_run(self) -> AssistanceRunView | None:
        with self.database.read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM assistance_runs WHERE status='queued' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            return self._run_view(row) if row is not None else None

    def is_cancelled(self, run_id: UUID) -> bool:
        return self.get_run(run_id).status == "cancelled"

    def mark_running(self, run_id: UUID, *, scheduled_frames: int) -> AssistanceRunView:
        with self.database.write_transaction() as connection:
            run = self._require_run(connection, run_id)
            if run["status"] != "queued":
                raise AssistanceStateConflict("only queued runs can start")
            connection.execute(
                "UPDATE assistance_runs SET status='running',scheduled_frames=?,updated_at=? WHERE id=?",
                (scheduled_frames, _now(), str(run_id)),
            )
            return self._run_view(self._require_run(connection, run_id))

    def run_stride(self, run_id: UUID) -> int:
        with self.database.read_connection() as connection:
            row = self._require_run(connection, run_id)
        stride = row["stride"]
        if stride is None:
            # Compatibility for v3 rows. Their stride cannot be reconstructed;
            # only execute when the current settings reproduce the stored hash.
            stride = self.stride
        if row["config_sha256"] != self._config_sha256(
            row["model"], row["device"], stride
        ):
            raise AssistanceStateConflict("assistance run config changed")
        return int(stride)

    def update_progress(self, run_id: UUID, processed_frames: int) -> AssistanceRunView:
        if processed_frames < 0:
            raise ValueError("processed frame count cannot be negative")
        with self.database.write_transaction() as connection:
            run = self._require_run(connection, run_id)
            if run["status"] != "running":
                raise AssistanceStateConflict("only running runs can report progress")
            if processed_frames > run["scheduled_frames"]:
                raise ValueError("processed frame count exceeds scheduled frames")
            if processed_frames > run["processed_frames"]:
                connection.execute(
                    "UPDATE assistance_runs SET processed_frames=?,updated_at=? WHERE id=?",
                    (processed_frames, _now(), str(run_id)),
                )
            return self._run_view(self._require_run(connection, run_id))

    def publish(
        self,
        run_id: UUID,
        expected_binding: AssistanceRunBinding,
        proposals: list[dict[str, Any]],
    ) -> AssistanceRunView:
        with self.database.write_transaction() as connection:
            run = self._require_run(connection, run_id)
            if run["status"] != "running":
                raise AssistanceStateConflict("only running runs can publish")
            clip = self._require_clip(connection, UUID(run["clip_id"]))
            current = AssistanceRunBinding(
                clip["source_sha256"], UUID(clip["roi_revision_id"]), 1
            )
            if current != expected_binding or current != AssistanceRunBinding(
                run["source_sha256"], UUID(run["roi_revision_id"]), run["guideline_version"]
            ):
                raise AssistanceStateConflict("assistance run binding is stale")
            now = _now()
            seen: set[tuple[Any, ...]] = set()
            for proposal in proposals:
                semantic_key = tuple(
                    proposal.get(field) for field in (
                        "label", "action_start_frame", "action_end_frame",
                        "view_start_frame", "view_end_frame", "crossing_estimate",
                        "crossing_bracket_start", "crossing_bracket_end", "reason",
                    )
                )
                if semantic_key in seen:
                    continue
                seen.add(semantic_key)
                connection.execute(
                    """INSERT INTO assistance_suggestions(
                        id,run_id,clip_id,proposal_key,label,action_start_frame,
                        action_end_frame,view_start_frame,view_end_frame,crossing_estimate,
                        crossing_bracket_start,crossing_bracket_end,reason,evidence_json,
                        review_state,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                    (
                        str(uuid4()), str(run_id), run["clip_id"], proposal["proposal_key"],
                        proposal.get("label"), proposal.get("action_start_frame"),
                        proposal.get("action_end_frame"), proposal["view_start_frame"],
                        proposal["view_end_frame"], proposal.get("crossing_estimate"),
                        proposal.get("crossing_bracket_start"),
                        proposal.get("crossing_bracket_end"), proposal["reason"],
                        json.dumps(proposal.get("evidence", {}), sort_keys=True, separators=(",", ":")),
                        now, now,
                    ),
                )
            connection.execute(
                """UPDATE assistance_runs SET status='succeeded',processed_frames=scheduled_frames,
                    error_code=NULL,updated_at=? WHERE id=?""",
                (now, str(run_id)),
            )
            return self._run_view(self._require_run(connection, run_id))

    def fail(self, run_id: UUID, error_code: str) -> AssistanceRunView:
        with self.database.write_transaction() as connection:
            run = self._require_run(connection, run_id)
            if run["status"] == "failed":
                return self._run_view(run)
            if run["status"] in {"succeeded", "cancelled"}:
                raise AssistanceStateConflict("completed run cannot fail")
            connection.execute(
                "UPDATE assistance_runs SET status='failed',error_code=?,updated_at=? WHERE id=?",
                (error_code, _now(), str(run_id)),
            )
            return self._run_view(self._require_run(connection, run_id))

    def request_cancel(
        self, run_id: UUID, request: AssistanceRunCancel
    ) -> AssistanceRunView:
        payload_sha = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.database.write_transaction() as connection:
            replay = connection.execute(
                "SELECT action,payload_sha256,response_json FROM operations WHERE resource_id=? AND operation_id=?",
                (str(run_id), str(request.operation_id)),
            ).fetchone()
            if replay is not None:
                if replay["action"] != "cancel_assistance" or replay["payload_sha256"] != payload_sha:
                    raise PayloadConflict("operation id was already used with a different payload")
                return AssistanceRunView.model_validate_json(replay["response_json"])
            run = self._require_run(connection, run_id)
            if run["status"] not in {"queued", "running", "cancelled"}:
                raise AssistanceStateConflict("completed run cannot be cancelled")
            if run["status"] != "cancelled":
                connection.execute(
                    "UPDATE assistance_runs SET status='cancelled',error_code=NULL,updated_at=? WHERE id=?",
                    (_now(), str(run_id)),
                )
            result = self._run_view(self._require_run(connection, run_id))
            connection.execute(
                "INSERT INTO operations(resource_id,operation_id,action,payload_sha256,response_json,created_at) VALUES(?,?,?,?,?,?)",
                (str(run_id), str(request.operation_id), "cancel_assistance", payload_sha,
                 result.model_dump_json(), _now()),
            )
            return result

    def reject_suggestion(
        self, clip_id: UUID, suggestion_id: UUID, request: SuggestionReject
    ) -> AssistanceSuggestionView:
        payload_sha = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        with self.database.write_transaction() as connection:
            suggestion = connection.execute(
                "SELECT clip_id FROM assistance_suggestions WHERE id=?",
                (str(suggestion_id),),
            ).fetchone()
            if suggestion is None or suggestion["clip_id"] != str(clip_id):
                raise NotFound("assistance suggestion was not found for this clip")
            replay = connection.execute(
                "SELECT action,payload_sha256,response_json FROM operations WHERE resource_id=? AND operation_id=?",
                (str(suggestion_id), str(request.operation_id)),
            ).fetchone()
            if replay is not None:
                if replay["action"] != "reject_suggestion" or replay["payload_sha256"] != payload_sha:
                    raise PayloadConflict("operation id was already used with a different payload")
                return AssistanceSuggestionView.model_validate_json(replay["response_json"])
            clip = self._require_clip(connection, clip_id)
            if clip["revision"] != request.expected_clip_revision:
                raise RevisionConflict("clip revision is stale")
            row = connection.execute(
                "SELECT * FROM assistance_suggestions WHERE id=? AND clip_id=?",
                (str(suggestion_id), str(clip_id)),
            ).fetchone()
            if row is None:
                raise NotFound("assistance suggestion was not found for this clip")
            if row["review_state"] != "pending":
                raise AssistanceStateConflict("assistance suggestion was already reviewed")
            connection.execute(
                "UPDATE assistance_suggestions SET review_state='rejected',updated_at=? WHERE id=?",
                (_now(), str(suggestion_id)),
            )
            result = self._suggestion_view(connection.execute(
                "SELECT * FROM assistance_suggestions WHERE id=?", (str(suggestion_id),)
            ).fetchone())
            connection.execute(
                "INSERT INTO operations(resource_id,operation_id,action,payload_sha256,response_json,created_at) VALUES(?,?,?,?,?,?)",
                (str(suggestion_id), str(request.operation_id), "reject_suggestion", payload_sha,
                 result.model_dump_json(), _now()),
            )
            return result

    def reconcile_running(self) -> None:
        with self.database.write_transaction() as connection:
            connection.execute(
                """UPDATE assistance_runs SET status='failed',error_code='interrupted',updated_at=?
                   WHERE status='running'""",
                (_now(),),
            )

    def list_suggestions(
        self,
        clip_id: UUID,
        *,
        state: str = "pending",
        limit: int = 50,
        cursor: str | None = None,
    ) -> AssistanceSuggestionListView:
        if limit < 1 or limit > 100:
            raise ValueError("suggestion queue limit must be between 1 and 100")
        key = _decode_suggestion_cursor(cursor)
        with self.database.read_connection() as connection:
            self._require_clip(connection, clip_id)
            cursor_where = ""
            parameters: list[Any] = [str(clip_id), state]
            if key is not None:
                cursor_where = (
                    " AND (view_start_frame > ? OR "
                    "(view_start_frame = ? AND id > ?))"
                )
                parameters.extend([key[0], key[0], key[1]])
            rows = connection.execute(
                f"""SELECT * FROM assistance_suggestions
                    WHERE clip_id=? AND review_state=?{cursor_where}
                    ORDER BY view_start_frame,id LIMIT ?""",
                (*parameters, limit + 1),
            ).fetchall()
            page = rows[:limit]
            next_cursor = (
                _encode_suggestion_cursor(page[-1]["view_start_frame"], page[-1]["id"])
                if len(rows) > limit else None
            )
            return AssistanceSuggestionListView(
                items=[self._suggestion_view(row) for row in page],
                next_cursor=next_cursor,
            )


def claim_suggestion_for_annotation(
    connection: sqlite3.Connection,
    *,
    suggestion_id: UUID,
    clip_id: UUID,
    annotation_id: UUID,
    source_sha256: str,
    roi_revision_id: UUID,
    guideline_version: int,
    now: str,
) -> None:
    row = connection.execute(
        """SELECT suggestion.*,run.status AS run_status,
                  run.source_sha256 AS run_source_sha256,
                  run.roi_revision_id AS run_roi_revision_id,
                  run.guideline_version AS run_guideline_version
           FROM assistance_suggestions AS suggestion
           JOIN assistance_runs AS run ON run.id=suggestion.run_id
           WHERE suggestion.id=?""",
        (str(suggestion_id),),
    ).fetchone()
    if row is None or row["clip_id"] != str(clip_id):
        raise NotFound("assistance suggestion was not found for this clip")
    if row["review_state"] != "pending" or row["run_status"] != "succeeded":
        raise AssistanceStateConflict("assistance suggestion is not pending")
    if (
        row["run_source_sha256"] != source_sha256
        or row["run_roi_revision_id"] != str(roi_revision_id)
        or row["run_guideline_version"] != guideline_version
    ):
        raise AssistanceStateConflict("assistance suggestion binding is stale")
    changed = connection.execute(
        """UPDATE assistance_suggestions
           SET review_state='accepted',accepted_annotation_id=?,updated_at=?
           WHERE id=? AND review_state='pending'""",
        (str(annotation_id), now, str(suggestion_id)),
    )
    if changed.rowcount != 1:
        raise AssistanceStateConflict("assistance suggestion was already reviewed")
