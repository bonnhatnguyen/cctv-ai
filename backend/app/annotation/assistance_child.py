from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.annotation_benchmark.contracts import (
    FrameSpan,
    FrozenSegment,
    ModelAsset,
    RunConfig,
    SelectionItem,
)
from app.annotation_benchmark.media import iter_source_frames
from app.annotation_benchmark.models import load_detector
from app.annotation_benchmark.proposals import build_proposals

from .assistance_protocol import AssistanceChildRequest, AssistanceChildResult


def _config(request: AssistanceChildRequest) -> RunConfig:
    return RunConfig(stride=request.stride, device=request.device)


def _segment(request: AssistanceChildRequest) -> FrozenSegment:
    return FrozenSegment(
        selection=SelectionItem(
            segment_id=request.run_id,
            clip_id=request.clip_id,
            span=FrameSpan(start_frame=request.start_frame, end_frame=request.end_frame),
            partition="exploratory",
            provenance_confirmed=False,
            scenario_tags=("assisted_review",),
        ),
        source_job_id=request.source_job_id,
        source_sha256=request.source_sha256,
        clip_revision=0,
        roi_revision_id=request.roi_revision_id,
        polygon=tuple(request.polygon),
        frame_count=request.frame_count,
        width=request.width,
        height=request.height,
        fps_num=request.fps_num,
        fps_den=request.fps_den,
        sar_num=request.sar_num,
        sar_den=request.sar_den,
    )


def _load_detector(request: AssistanceChildRequest):
    asset = ModelAsset.model_validate_json(
        (request.model_root / "asset.json").read_bytes()
    )
    return load_detector(request.model, request.model_root, asset, _config(request))


def _iter_frames(request: AssistanceChildRequest):
    return iter_source_frames(request.source_path, _segment(request), _config(request))


def _write_progress(path: Path | None, processed_frames: int) -> None:
    if path is None:
        return
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps({"schema_version": 1, "processed_frames": processed_frames}, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_child(
    request_path: Path, result_path: Path, progress_path: Path | None = None
) -> int:
    request = AssistanceChildRequest.model_validate_json(request_path.read_bytes())
    detector = _load_detector(request)
    observations = []
    observed_frames: list[int] = []
    try:
        for frame in _iter_frames(request):
            detected = detector.detect(frame)
            observations.append((frame.source_index, detected))
            observed_frames.append(frame.source_index)
            _write_progress(progress_path, len(observed_frames))
    finally:
        detector.close()
    scheduled = list(range(request.start_frame, request.end_frame + 1, request.stride))
    result = AssistanceChildResult(
        run_id=request.run_id,
        scheduled_frames=scheduled,
        observed_frames=observed_frames,
        proposals=build_proposals(_segment(request), observations, _config(request)),
    )
    temporary = result_path.with_name(result_path.name + ".tmp")
    temporary.write_text(
        json.dumps(result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(result_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path)
    args = parser.parse_args()
    try:
        return run_child(args.request, args.result, args.progress)
    except BaseException as exc:
        print(f"assistance_child_failed:{type(exc).__name__}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
