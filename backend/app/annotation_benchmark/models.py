from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Protocol

from .contracts import ModelAsset, Observation, RunConfig
from .media import SampledFrame


class Detector(Protocol):
    def detect(self, frame: SampledFrame) -> list[Observation]: ...

    def close(self) -> None: ...


def verify_asset(model_root: Path, asset: ModelAsset) -> dict[str, Path]:
    root = model_root.resolve(strict=True)
    verified: dict[str, Path] = {}

    for relative_name, expected_hash in asset.files_sha256.items():
        candidate = (root / relative_name).resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ValueError(f"model file is outside the model root: {relative_name}")

        digest = hashlib.sha256()
        with candidate.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(
                f"model file hash mismatch for {relative_name}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        verified[relative_name] = candidate

    return verified


def load_detector(
    name: Literal["mediapipe", "dino"],
    model_root: Path,
    asset: ModelAsset,
    config: RunConfig,
) -> Detector:
    expected_model = {
        "mediapipe": "mediapipe-hand-landmarker",
        "dino": "IDEA-Research/grounding-dino-tiny",
    }[name]
    if asset.model_id != expected_model:
        raise ValueError(f"asset {asset.model_id} does not match detector {name}")
    verified = verify_asset(model_root, asset)
    if name == "mediapipe":
        from .mediapipe_adapter import MediaPipeHandDetector

        try:
            model_path = verified["hand_landmarker.task"]
        except KeyError as exc:
            raise ValueError("MediaPipe asset must contain hand_landmarker.task") from exc
        return MediaPipeHandDetector(model_path, config)

    from .dino_adapter import GroundingDinoDetector

    return GroundingDinoDetector(model_root.resolve(strict=True), config)
