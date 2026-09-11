from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from app.annotation.contracts import MediaView, Point
from app.annotation.database import canonical_root
from app.annotation.geometry import validate_polygon

from .contracts import (
    FrameSpan,
    FrozenManifest,
    FrozenSegment,
    ReferenceEvent,
    SelectionItem,
)


_REQUIRED_SCENARIOS = {
    "entry",
    "exit",
    "stationary",
    "near_pass",
    "boundary_jitter",
    "multiple_hands",
    "occlusion",
    "no_action",
}


class SnapshotError(RuntimeError):
    pass


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise SnapshotError(f"database is missing: {resolved.name}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise SnapshotError("source video is unavailable") from exc
    return digest.hexdigest()


def _parse_sar(value: str) -> tuple[int, int]:
    try:
        numerator, denominator = (int(part) for part in value.split(":"))
    except (TypeError, ValueError) as exc:
        raise SnapshotError("clip has invalid sample aspect ratio") from exc
    if numerator <= 0 or denominator <= 0:
        raise SnapshotError("clip has invalid sample aspect ratio")
    return numerator, denominator


def _clipped_span(start: int, end: int, selected: FrameSpan) -> FrameSpan | None:
    clipped_start = max(start, selected.start_frame)
    clipped_end = min(end, selected.end_frame)
    if clipped_end < clipped_start:
        return None
    return FrameSpan(start_frame=clipped_start, end_frame=clipped_end)


def _source_path(source_root: Path, source_job_id: str, raw_path: str) -> Path:
    jobs_root = (source_root / "jobs").resolve()
    expected = (jobs_root / source_job_id / "source.mp4").resolve()
    candidate = Path(raw_path).resolve()
    try:
        candidate.relative_to(jobs_root)
    except ValueError as exc:
        raise SnapshotError("source path is outside the owned job directory") from exc
    if candidate != expected:
        raise SnapshotError("source path is outside the owned job directory")
    return candidate


def _schema_version(connection: sqlite3.Connection) -> int:
    try:
        row = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()
    except sqlite3.Error as exc:
        raise SnapshotError("annotation database schema is unavailable") from exc
    return int(row["version"] or 0)


def _validate_selection(selection: list[SelectionItem]) -> None:
    segment_ids = [item.segment_id for item in selection]
    if len(segment_ids) != len(set(segment_ids)):
        raise SnapshotError("selection contains a duplicate segment id")
    for item in selection:
        if item.partition != "exploratory" and not item.provenance_confirmed:
            raise SnapshotError("tuning and evaluation require confirmed provenance")
    ordered = sorted(
        selection,
        key=lambda item: (str(item.clip_id), item.span.start_frame, item.span.end_frame),
    )
    for previous, current in zip(ordered, ordered[1:]):
        if (
            previous.clip_id == current.clip_id
            and current.span.start_frame <= previous.span.end_frame
        ):
            raise SnapshotError("selected segments from the same clip overlap")


def _share_recording(first: FrozenSegment, second: FrozenSegment) -> bool:
    return any(
        (
            first.source_sha256 == second.source_sha256,
            first.selection.parent_recording_id is not None
            and first.selection.parent_recording_id == second.selection.parent_recording_id,
            bool(
                set(first.selection.recording_days)
                & set(second.selection.recording_days)
            ),
        )
    )


def _validate_partition_groups(segments: tuple[FrozenSegment, ...]) -> None:
    parents = list(range(len(segments)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(segments)):
        for right in range(left + 1, len(segments)):
            if _share_recording(segments[left], segments[right]):
                union(left, right)
    partitions: dict[int, set[str]] = {}
    for index, segment in enumerate(segments):
        partition = segment.selection.partition
        if partition in {"tuning", "evaluation"}:
            partitions.setdefault(find(index), set()).add(partition)
    if any(values == {"tuning", "evaluation"} for values in partitions.values()):
        raise SnapshotError("selected recording group has tuning/evaluation partition leakage")


def _covers_selection(spans: tuple[FrameSpan, ...], selected: FrameSpan) -> bool:
    cursor = selected.start_frame
    for span in sorted(spans, key=lambda item: (item.start_frame, item.end_frame)):
        if span.end_frame < cursor:
            continue
        if span.start_frame > cursor:
            return False
        cursor = max(cursor, span.end_frame + 1)
        if cursor > selected.end_frame:
            return True
    return cursor > selected.end_frame


def _read_segment(
    annotations: sqlite3.Connection,
    jobs: sqlite3.Connection,
    source_root: Path,
    selection: SelectionItem,
) -> FrozenSegment:
    clip = annotations.execute(
        "SELECT * FROM annotation_clips WHERE id=?", (str(selection.clip_id),)
    ).fetchone()
    if clip is None:
        raise SnapshotError("selected annotation clip was not found")
    if clip["source_state"] != "available" or not clip["source_sha256"]:
        raise SnapshotError("selected clip source is unavailable")
    if clip["roi_revision_id"] is None or clip["media_json"] is None:
        raise SnapshotError("selected clip has no current ROI or media")
    roi = annotations.execute(
        "SELECT * FROM roi_revisions WHERE id=? AND clip_id=? AND kind='clip'",
        (clip["roi_revision_id"], clip["id"]),
    ).fetchone()
    if roi is None:
        raise SnapshotError("selected clip current ROI is unavailable")
    try:
        media = MediaView.model_validate_json(clip["media_json"])
        points = [Point.model_validate(item) for item in json.loads(roi["polygon_json"])]
        validate_polygon(points)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SnapshotError("selected clip metadata or ROI is invalid") from exc
    if selection.span.end_frame >= media.frame_count:
        raise SnapshotError("selected segment exceeds source frame count")

    job = jobs.execute(
        "SELECT id,source_path,metadata_json FROM v1_tracking_jobs WHERE id=?",
        (clip["source_job_id"],),
    ).fetchone()
    if job is None:
        raise SnapshotError("source job was not found")
    source = _source_path(source_root, job["id"], job["source_path"])
    try:
        source_metadata = json.loads(job["metadata_json"])
        expected_metadata = (
            media.width,
            media.height,
            media.fps_num,
            media.fps_den,
            media.frame_count,
            media.sample_aspect_ratio,
        )
        actual_metadata = (
            int(source_metadata["width"]),
            int(source_metadata["height"]),
            int(source_metadata["fps_num"]),
            int(source_metadata["fps_den"]),
            int(source_metadata["frame_count_estimate"]),
            source_metadata.get("sample_aspect_ratio", "1:1"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SnapshotError("source job metadata is invalid") from exc
    if actual_metadata != expected_metadata:
        raise SnapshotError("source job and annotation clip metadata disagree")
    if _hash_file(source) != clip["source_sha256"]:
        raise SnapshotError("source video hash no longer matches the clip")

    events: list[ReferenceEvent] = []
    ignored: dict[str, list[FrameSpan]] = {"hand_in": [], "hand_out": []}
    action_rows = annotations.execute(
        """SELECT * FROM action_annotations
        WHERE clip_id=? AND deleted=0 AND end_frame>=? AND start_frame<=?
        ORDER BY start_frame,end_frame,id""",
        (str(selection.clip_id), selection.span.start_frame, selection.span.end_frame),
    ).fetchall()
    for row in action_rows:
        label = row["label"]
        classes: set[str]
        if label in {"hand_in", "hand_out"}:
            classes = {label}
        elif label == "unclear":
            classes = set(json.loads(row["uncertain_labels_json"])) & {
                "hand_in",
                "hand_out",
            }
        else:
            classes = set()
        clipped = _clipped_span(row["start_frame"], row["end_frame"], selection.span)
        if clipped is None:
            continue
        is_current_confirmed = (
            row["review_state"] == "confirmed"
            and row["roi_revision_id"] == clip["roi_revision_id"]
            and row["guideline_version"] == 1
            and row["start_frame"] >= selection.span.start_frame
            and row["end_frame"] <= selection.span.end_frame
        )
        if label in {"hand_in", "hand_out"} and is_current_confirmed:
            events.append(
                ReferenceEvent(
                    event_id=UUID(row["id"]),
                    label=label,
                    span=clipped,
                    crossing_frame=row["crossing_frame"],
                    revision=row["revision"],
                )
            )
        else:
            for action_class in classes:
                ignored[action_class].append(clipped)

    coverage: dict[str, list[FrameSpan]] = {"hand_in": [], "hand_out": []}
    coverage_rows = annotations.execute(
        """SELECT * FROM review_coverage
        WHERE clip_id=? AND active=1 AND roi_revision_id=? AND guideline_version=1
          AND end_frame>=? AND start_frame<=?
        ORDER BY start_frame,end_frame,id""",
        (
            str(selection.clip_id),
            clip["roi_revision_id"],
            selection.span.start_frame,
            selection.span.end_frame,
        ),
    ).fetchall()
    for row in coverage_rows:
        span = _clipped_span(row["start_frame"], row["end_frame"], selection.span)
        if span is None:
            continue
        for label in set(json.loads(row["reviewed_labels_json"])) & {
            "hand_in",
            "hand_out",
        }:
            coverage[label].append(span)

    sar_num, sar_den = _parse_sar(media.sample_aspect_ratio)
    return FrozenSegment(
        selection=selection,
        source_job_id=UUID(clip["source_job_id"]),
        source_sha256=clip["source_sha256"],
        clip_revision=clip["revision"],
        roi_revision_id=UUID(clip["roi_revision_id"]),
        polygon=tuple((point.x, point.y) for point in points),
        guideline_version=1,
        frame_count=media.frame_count,
        width=media.width,
        height=media.height,
        fps_num=media.fps_num,
        fps_den=media.fps_den,
        sar_num=sar_num,
        sar_den=sar_den,
        events=tuple(events),
        coverage={label: tuple(spans) for label, spans in coverage.items()},
        ignored={label: tuple(spans) for label, spans in ignored.items()},
    )


def read_snapshot(
    source_root: Path,
    annotation_root: Path,
    selection: list[SelectionItem],
) -> FrozenManifest:
    source_root = source_root.resolve()
    annotation_root = annotation_root.resolve()
    _validate_selection(selection)
    annotations = _open_read_only(annotation_root / "annotations.db")
    jobs: sqlite3.Connection | None = None
    try:
        if _schema_version(annotations) != 2:
            raise SnapshotError("unsupported annotation database schema version")
        binding = annotations.execute(
            "SELECT source_data_root FROM annotation_binding WHERE singleton=1"
        ).fetchone()
        if binding is None or binding["source_data_root"] != canonical_root(source_root):
            raise SnapshotError("annotation database belongs to a different private source root")
        jobs = _open_read_only(source_root / "jobs.db")
        segments = tuple(
            _read_segment(annotations, jobs, source_root, item) for item in selection
        )
        _validate_partition_groups(segments)
    except sqlite3.Error as exc:
        raise SnapshotError("private snapshot could not be read") from exc
    finally:
        if jobs is not None:
            jobs.close()
        annotations.close()

    seen_tags = {tag for item in selection for tag in item.scenario_tags}
    missing_scenarios = tuple(sorted(_REQUIRED_SCENARIOS - seen_tags))
    labels = {event.label for segment in segments for event in segment.events}
    coverage_ready = bool(segments) and all(
        _covers_selection(segment.coverage[label], segment.selection.span)
        and not segment.ignored[label]
        for segment in segments
        for label in ("hand_in", "hand_out")
    )
    reference_state = "ready" if (
        labels == {"hand_in", "hand_out"}
        and not missing_scenarios
        and coverage_ready
    ) else "pending_data"
    return FrozenManifest(
        schema_version=1,
        manifest_id=uuid4(),
        frozen_at=datetime.now(timezone.utc),
        segments=segments,
        reference_state=reference_state,
        missing_scenarios=missing_scenarios,
    )
