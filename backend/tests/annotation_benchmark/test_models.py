from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from app.annotation_benchmark.contracts import ModelAsset, Observation, RunConfig
from app.annotation_benchmark.dino_adapter import GroundingDinoDetector
from app.annotation_benchmark.media import CropTransform, SampledFrame
from app.annotation_benchmark.mediapipe_adapter import MediaPipeHandDetector
from app.annotation_benchmark.models import load_detector, verify_asset


def _asset(file_hash: str) -> ModelAsset:
    return ModelAsset(
        model_id="mediapipe-hand-landmarker",
        revision="hand_landmarker_full-float16-v1",
        files_sha256={"hand_landmarker.task": file_hash},
        license_source="https://ai.google.dev/edge/mediapipe",
        license_sha256="1" * 64,
        telemetry_policy="allowed_by_user",
    )


def test_asset_hash_mismatch_is_an_error_not_an_empty_detection(tmp_path: Path) -> None:
    model = tmp_path / "hand_landmarker.task"
    model.write_bytes(b"wrong-test-weights")

    with pytest.raises(ValueError, match="hash"):
        verify_asset(tmp_path, _asset("0" * 64))


def test_asset_verification_accepts_exact_local_file(tmp_path: Path) -> None:
    payload = b"verified-test-weights"
    (tmp_path / "hand_landmarker.task").write_bytes(payload)
    asset = _asset(hashlib.sha256(payload).hexdigest())

    verified = verify_asset(tmp_path, asset)

    assert verified == {"hand_landmarker.task": (tmp_path / "hand_landmarker.task").resolve()}


def test_asset_verification_streams_large_files(tmp_path: Path, monkeypatch) -> None:
    payload = b"streamed-test-weights"
    (tmp_path / "hand_landmarker.task").write_bytes(payload)
    monkeypatch.setattr(Path, "read_bytes", lambda _path: (_ for _ in ()).throw(AssertionError("read_bytes")))

    verified = verify_asset(tmp_path, _asset(hashlib.sha256(payload).hexdigest()))

    assert "hand_landmarker.task" in verified


def test_model_asset_rejects_floating_revision_and_path_escape() -> None:
    with pytest.raises(ValidationError, match="revision"):
        ModelAsset(
            model_id="IDEA-Research/grounding-dino-tiny",
            revision="main",
            files_sha256={"model.safetensors": "0" * 64},
            license_source="https://huggingface.co/IDEA-Research/grounding-dino-tiny",
            license_sha256="1" * 64,
            telemetry_policy="not_applicable",
        )
    with pytest.raises(ValidationError, match="relative"):
        _asset("0" * 64).model_copy(
            update={"files_sha256": {"../escape.bin": "0" * 64}}
        ).__class__.model_validate(
            {
                **_asset("0" * 64).model_dump(),
                "files_sha256": {"../escape.bin": "0" * 64},
            }
        )


def test_observation_rejects_malformed_box_and_score_semantics() -> None:
    with pytest.raises(ValidationError):
        Observation(
            frame_index=1,
            bbox=(0.7, 0.2, 0.6, 0.8),
            score=0.8,
            score_kind="grounding_score",
        )
    with pytest.raises(ValidationError, match="unavailable"):
        Observation(
            frame_index=1,
            bbox=(0.2, 0.2, 0.6, 0.8),
            score=0.8,
            score_kind="unavailable",
        )


def _frame() -> SampledFrame:
    return SampledFrame(
        source_index=7,
        timestamp_ms=233,
        rgb=np.zeros((50, 100, 3), dtype=np.uint8),
        transform=CropTransform(100, 50, 300, 150, 100, 50, 1000, 500),
    )


class _FakeMediaPipeSdk:
    class ImageFormat:
        SRGB = "srgb"

    class Image:
        def __init__(self, *, image_format, data):
            self.image_format = image_format
            self.data = data

    class tasks:
        class BaseOptions:
            def __init__(self, *, model_asset_path):
                self.model_asset_path = model_asset_path

        class vision:
            class RunningMode:
                VIDEO = "video"

            class HandLandmarkerOptions:
                def __init__(self, **kwargs):
                    self.kwargs = kwargs


class _FakeLandmarker:
    def __init__(self, result):
        self.result = result
        self.timestamps: list[int] = []
        self.closed = False

    def detect_for_video(self, image, timestamp_ms):
        assert image.image_format == "srgb"
        assert image.data.dtype == np.uint8
        assert image.data.flags.c_contiguous
        self.timestamps.append(timestamp_ms)
        return self.result

    def close(self):
        self.closed = True


def test_mediapipe_uses_tasks_result_shape_timestamp_and_handedness(tmp_path: Path) -> None:
    landmarks = [
        SimpleNamespace(x=0.2, y=0.1),
        SimpleNamespace(x=0.7, y=0.8),
    ]
    category = SimpleNamespace(score=0.91)
    landmarker = _FakeLandmarker(
        SimpleNamespace(hand_landmarks=[landmarks], handedness=[[category]])
    )
    detector = MediaPipeHandDetector(
        tmp_path / "hand_landmarker.task",
        RunConfig(),
        sdk=_FakeMediaPipeSdk,
        landmarker=landmarker,
    )

    observations = detector.detect(_frame())

    assert landmarker.timestamps == [233]
    assert observations == [
        Observation(
            frame_index=7,
            bbox=(0.14, 0.12, 0.24, 0.26),
            score=0.91,
            score_kind="handedness",
        )
    ]
    detector.close()
    assert landmarker.closed


def test_mediapipe_empty_and_missing_handedness_are_explicit(tmp_path: Path) -> None:
    empty = MediaPipeHandDetector(
        tmp_path / "hand_landmarker.task",
        RunConfig(),
        sdk=_FakeMediaPipeSdk,
        landmarker=_FakeLandmarker(
            SimpleNamespace(hand_landmarks=[], handedness=[])
        ),
    )
    assert empty.detect(_frame()) == []

    landmarks = [[SimpleNamespace(x=0.2, y=0.1), SimpleNamespace(x=0.7, y=0.8)]]
    detector = MediaPipeHandDetector(
        tmp_path / "hand_landmarker.task",
        RunConfig(),
        sdk=_FakeMediaPipeSdk,
        landmarker=_FakeLandmarker(
            SimpleNamespace(hand_landmarks=landmarks, handedness=[])
        ),
    )
    assert detector.detect(_frame())[0].score_kind == "unavailable"


def test_mediapipe_rejects_malformed_landmarks(tmp_path: Path) -> None:
    detector = MediaPipeHandDetector(
        tmp_path / "hand_landmarker.task",
        RunConfig(),
        sdk=_FakeMediaPipeSdk,
        landmarker=_FakeLandmarker(
            SimpleNamespace(
                hand_landmarks=[[SimpleNamespace(x=float("nan"), y=0.5)]],
                handedness=[],
            )
        ),
    )
    with pytest.raises(ValueError, match="landmark"):
        detector.detect(_frame())


class _NoGrad:
    def __enter__(self):
        return None

    def __exit__(self, *_args):
        return False


class _FakeTorch:
    uint8 = "uint8"

    @staticmethod
    def no_grad():
        return _NoGrad()


class _UnavailableCudaTorch(_FakeTorch):
    class cuda:
        @staticmethod
        def is_available():
            return False


class _FakeTensor:
    def __init__(self, value):
        self.value = value

    def to(self, _device):
        return self


class _FakeProcessor:
    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, *, images, text, return_tensors):
        assert images.dtype == np.uint8
        assert return_tensors == "pt"
        self.prompts.append(text)
        return {"pixel_values": _FakeTensor(images)}

    def post_process_grounded_object_detection(self, _outputs, **kwargs):
        assert kwargs["threshold"] == 0.25
        assert kwargs["text_threshold"] == 0.25
        assert kwargs["target_sizes"] == [(50, 100)]
        return [{"boxes": [[20.0, 5.0, 70.0, 40.0]], "scores": [0.83]}]


class _FakeModel:
    def __init__(self):
        self.closed = False

    def eval(self):
        return self

    def to(self, _device):
        return self

    def __call__(self, **_inputs):
        return object()


def test_dino_uses_fixed_prompt_and_maps_boxes_to_source() -> None:
    processor = _FakeProcessor()
    detector = GroundingDinoDetector(
        Path("unused"),
        RunConfig(),
        processor=processor,
        model=_FakeModel(),
        torch_module=_FakeTorch,
    )

    observations = detector.detect(_frame())

    assert processor.prompts == ["hand."]
    assert observations == [
        Observation(
            frame_index=7,
            bbox=(0.14, 0.12, 0.24, 0.26),
            score=0.83,
            score_kind="grounding_score",
        )
    ]


def test_dino_cuda_request_fails_preflight_without_fallback() -> None:
    with pytest.raises(RuntimeError, match="CUDA"):
        GroundingDinoDetector(
            Path("unused"),
            RunConfig(device="cuda:0"),
            processor=_FakeProcessor(),
            model=_FakeModel(),
            torch_module=_UnavailableCudaTorch,
        )


def test_dino_clips_valid_regression_box_to_image_bounds() -> None:
    class EdgeProcessor(_FakeProcessor):
        def post_process_grounded_object_detection(self, _outputs, **_kwargs):
            return [{"boxes": [[-0.02, -0.01, 100.02, 50.01]], "scores": [0.8]}]

    detector = GroundingDinoDetector(
        Path("unused"),
        RunConfig(),
        processor=EdgeProcessor(),
        model=_FakeModel(),
        torch_module=_FakeTorch,
    )

    assert detector.detect(_frame()) == [
        Observation(
            frame_index=7,
            bbox=(0.1, 0.1, 0.3, 0.3),
            score=0.8,
            score_kind="grounding_score",
        )
    ]


def test_load_detector_does_not_fallback_when_asset_identity_is_wrong(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not match"):
        load_detector("dino", tmp_path, _asset("0" * 64), RunConfig())
