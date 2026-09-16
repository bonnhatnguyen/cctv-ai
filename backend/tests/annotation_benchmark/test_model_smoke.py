from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.annotation_benchmark.contracts import ModelAsset, RunConfig
from app.annotation_benchmark.media import CropTransform, SampledFrame
from app.annotation_benchmark.models import load_detector


def _asset_from_option(pytestconfig: pytest.Config) -> tuple[Path, ModelAsset]:
    option = pytestconfig.getoption("--model-root")
    if not option:
        pytest.skip("requires --model-root with a verified private asset manifest")
    model_root = Path(option).resolve()
    manifest_path = model_root / "asset.json"
    if not manifest_path.is_file():
        pytest.skip(f"asset manifest is unavailable: {manifest_path}")
    asset = ModelAsset.model_validate(json.loads(manifest_path.read_text("utf-8")))
    return model_root, asset


def _blank_frame() -> SampledFrame:
    return SampledFrame(
        source_index=0,
        timestamp_ms=0,
        rgb=np.zeros((128, 128, 3), dtype=np.uint8),
        transform=CropTransform(0, 0, 128, 128, 128, 128, 128, 128),
    )


def test_mediapipe_real_model_load_infer_and_close(pytestconfig: pytest.Config) -> None:
    model_root, asset = _asset_from_option(pytestconfig)
    if asset.model_id != "mediapipe-hand-landmarker":
        pytest.skip("the supplied asset manifest is not MediaPipe")

    detector = load_detector("mediapipe", model_root, asset, RunConfig())
    try:
        observations = detector.detect(_blank_frame())
        assert isinstance(observations, list)
    finally:
        detector.close()


def test_dino_real_model_load_infer_and_close(pytestconfig: pytest.Config) -> None:
    model_root, asset = _asset_from_option(pytestconfig)
    if asset.model_id != "IDEA-Research/grounding-dino-tiny":
        pytest.skip("the supplied asset manifest is not Grounding DINO")

    detector = load_detector("dino", model_root, asset, RunConfig(device="cuda:0"))
    try:
        observations = detector.detect(_blank_frame())
        assert isinstance(observations, list)
    finally:
        detector.close()
