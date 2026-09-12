from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from .contracts import (
    AssistanceRunCreate,
    AssistanceRunListView,
    AssistanceRunView,
    AssistanceSuggestionListView,
    AssistanceSuggestionView,
)
from .database import AnnotationDatabase
from .repository import ClipNotReady, NotFound, PayloadConflict, RevisionConflict, SourceUnavailable


class AssistanceStateConflict(RuntimeError):
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


class AssistanceRepository:
    def __init__(self, database: AnnotationDatabase) -> None:
        self.database = database

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
            media = json.loads(clip["media_json"] or "null")
            if media is None or request.end_frame >= media["frame_count"]:
                raise ValueError("assistance frame interval is outside the clip")
            run_id = uuid4()
            now = _now()
            device = "cuda:0" if request.model == "dino" else "cpu"
            connection.execute(
                """INSERT INTO assistance_runs(
                    id,clip_id,operation_id,request_sha256,source_sha256,roi_revision_id,
                    guideline_version,model,device,start_frame,end_frame,status,
                    processed_frames,scheduled_frames,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,1,?,?,?,?, 'queued',0,0,?,?)""",
                (
                    str(run_id), str(clip_id), str(request.operation_id), request_sha,
                    clip["source_sha256"], clip["roi_revision_id"], request.model,
                    device, request.start_frame, request.end_frame, now, now,
                ),
            )
            return self._run_view(self._require_run(connection, run_id))

    def get_run(self, run_id: UUID) -> AssistanceRunView:
        with self.database.read_connection() as connection:
            return self._run_view(self._require_run(connection, run_id))

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

    def list_runs(self, clip_id: UUID, *, active_only: bool = False) -> AssistanceRunListView:
        with self.database.read_connection() as connection:
            self._require_clip(connection, clip_id)
            where = " AND status IN ('queued','running')" if active_only else ""
            rows = connection.execute(
                f"SELECT * FROM assistance_runs WHERE clip_id=?{where} ORDER BY created_at,id",
                (str(clip_id),),
            ).fetchall()
            return AssistanceRunListView(items=[self._run_view(row) for row in rows], next_cursor=None)

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
            for proposal in proposals:
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

    def list_suggestions(self, clip_id: UUID, *, state: str = "pending") -> AssistanceSuggestionListView:
        with self.database.read_connection() as connection:
            self._require_clip(connection, clip_id)
            rows = connection.execute(
                """SELECT * FROM assistance_suggestions WHERE clip_id=? AND review_state=?
                   ORDER BY view_start_frame,id""",
                (str(clip_id), state),
            ).fetchall()
            return AssistanceSuggestionListView(
                items=[self._suggestion_view(row) for row in rows], next_cursor=None
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
