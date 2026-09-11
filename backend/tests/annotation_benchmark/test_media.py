from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from app.annotation_benchmark.contracts import (
    FrameSpan,
    FrozenSegment,
    RunConfig,
    SelectionItem,
)
from app.annotation_benchmark.media import (
    CropTransform,
    SourceDecodeError,
    SourceMediaChanged,
    iter_source_frames,
    model_timestamp_ms,
)


def _make_video(path: Path, colors: list[tuple[int, int, int]]) -> Path:
    width, height = 64, 48
    raw = b"".join(
        np.full((height, width, 3), color, dtype=np.uint8).tobytes()
        for color in colors
    )
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            "25",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "0",
            "-pix_fmt",
            "yuv444p",
            "-y",
            str(path),
        ],
        input=raw,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.decode(errors="replace"))
    return path


def _segment(source: Path, *, sar: tuple[int, int] = (1, 1)) -> FrozenSegment:
    return FrozenSegment(
        selection=SelectionItem(
            segment_id=uuid4(),
            clip_id=uuid4(),
            span=FrameSpan(start_frame=1, end_frame=4),
            partition="exploratory",
            provenance_confirmed=False,
            scenario_tags=("entry",),
        ),
        source_job_id=uuid4(),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        clip_revision=1,
        roi_revision_id=uuid4(),
        polygon=((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)),
        frame_count=6,
        width=64,
        height=48,
        fps_num=25,
        fps_den=1,
        sar_num=sar[0],
        sar_den=sar[1],
    )


def test_source_timestamps_are_derived_from_frame_index_and_monotonic() -> None:
    assert model_timestamp_ms(30, 30_000, 1001, None) == 1001
    assert model_timestamp_ms(1, 2000, 1, 0) == 1
    assert model_timestamp_ms(0, 25, 1, None) == 0


def test_crop_transform_maps_model_coordinates_back_to_source_raster() -> None:
    transform = CropTransform(
        left=100,
        top=50,
        right=300,
        bottom=150,
        input_width=400,
        input_height=100,
        source_width=1000,
        source_height=500,
    )
    assert transform.source_xy(0.5, 0.5) == (0.2, 0.2)
    assert transform.source_xy(0.0, 1.0) == (0.1, 0.3)


def test_run_config_rejects_invalid_sampling_and_thresholds() -> None:
    with pytest.raises(ValidationError):
        RunConfig(stride=0)
    with pytest.raises(ValidationError):
        RunConfig(margin_ratio=0)
    with pytest.raises(ValidationError):
        RunConfig(dino_box_threshold=1.1)


def test_sequential_decode_yields_only_scheduled_source_frames(tmp_path: Path) -> None:
    colors = [(10, 20, 30), (40, 50, 60), (70, 80, 90),
              (100, 110, 120), (130, 140, 150), (160, 170, 180)]
    source = _make_video(tmp_path / "numbered.mp4", colors)

    frames = list(
        iter_source_frames(
            source,
            _segment(source),
            RunConfig(stride=2, margin_ratio=0.25, max_input_side=128),
        )
    )

    assert [frame.source_index for frame in frames] == [1, 3]
    assert [frame.timestamp_ms for frame in frames] == [40, 120]
    means = [tuple(np.rint(frame.rgb.mean(axis=(0, 1))).astype(int)) for frame in frames]
    assert np.allclose(means, [(40, 50, 60), (100, 110, 120)], atol=1)


def test_sar_is_applied_to_model_input_without_changing_source_mapping(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "anamorphic.mp4", [(20, 30, 40)] * 6)
    frame = next(
        iter_source_frames(
            source,
            _segment(source, sar=(2, 1)),
            RunConfig(stride=4, margin_ratio=0.25, max_input_side=128),
        )
    )
    expected_ratio = (
        (frame.transform.right - frame.transform.left) * 2
        / (frame.transform.bottom - frame.transform.top)
    )
    assert frame.rgb.shape[1] / frame.rgb.shape[0] == pytest.approx(expected_ratio, abs=0.03)
    assert frame.transform.source_xy(0.5, 0.5) == pytest.approx((0.5, 0.5))


def test_decode_rejects_source_bytes_changed_after_freeze(tmp_path: Path) -> None:
    source = _make_video(tmp_path / "changed.mp4", [(20, 30, 40)] * 6)
    segment = _segment(source)
    source.write_bytes(b"not-the-frozen-video")

    with pytest.raises(SourceMediaChanged, match="hash"):
        list(iter_source_frames(source, segment, RunConfig()))


def test_decoder_stall_is_bounded_and_owned_process_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "frozen.mp4"
    source.write_bytes(b"frozen-fake-source")
    segment = _segment(source)

    class SlowStream:
        def read(self, _size: int = -1) -> bytes:
            time.sleep(1.5)
            return b""

        def close(self) -> None:
            pass

    class EmptyStream:
        def read(self, _size: int = -1) -> bytes:
            return b""

        def close(self) -> None:
            pass

    class FakeProcess:
        stdout = SlowStream()
        stderr = EmptyStream()
        returncode: int | None = None
        killed = False
        wait_calls = 0

        def poll(self):
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    started = time.monotonic()

    with pytest.raises(SourceDecodeError, match="stalled"):
        list(
            iter_source_frames(
                source,
                segment,
                RunConfig(decoder_stall_seconds=1, segment_deadline_seconds=5),
            )
        )

    assert time.monotonic() - started < 1.4
    assert process.killed
    assert process.wait_calls == 1
