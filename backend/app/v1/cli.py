from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import Progress, RunOptions
from .pipeline import process_video


def _default_model() -> Path:
    candidates = [Path("backend/weights/yolo26n.pt"), Path("backend/yolo26n.pt"), Path("yolo26n.pt")]
    return next((path for path in candidates if path.is_file()), candidates[0])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Track COCO person class in a local MP4 with YOLO26 and ByteTrack.")
    parser.add_argument("input", type=Path, help="local input MP4")
    parser.add_argument("output", type=Path, help="annotated H.264/yuv420p output MP4")
    parser.add_argument("--model", type=Path, default=_default_model(), help="YOLO26 weights (default: yolo26n.pt)")
    parser.add_argument("--device", default="auto", help="execution device: auto, cpu, or CUDA index (default: auto)")
    parser.add_argument("--imgsz", type=int, default=960, help="inference image size (default: 960)")
    parser.add_argument("--evidence", type=Path, help="private JSONL frame/ID evidence path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def report(progress: Progress) -> None:
        total = progress.total_frames_estimate
        print(json.dumps({"stage": progress.stage.value, "processed_frames": progress.processed_frames,
                          "total_frames_estimate": total}, ensure_ascii=False), flush=True)

    try:
        summary = process_video(args.input, args.output,
                                RunOptions(str(args.model), args.device, args.imgsz),
                                report, evidence_path=args.evidence)
    except Exception as exc:
        print(f"Không thể xử lý video: {exc}")
        return 2
    print(json.dumps({"actual_device": summary.actual_device, "device_name": summary.device_name,
                      "processed_frames": summary.processed_frames, "local_track_count": summary.local_track_count,
                      "inference_samples": summary.inference_samples, "mean_inference_ms": summary.mean_inference_ms,
                      "tracking_wall_ms_total": summary.tracking_wall_ms_total, "processing_seconds": summary.processing_seconds,
                      "effective_fps": summary.effective_fps, "output_duration_ms": summary.output_duration_ms}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
