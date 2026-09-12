from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import Observation, RunConfig
from .media import SampledFrame


def _as_list(value: Any) -> list[Any]:
    if hasattr(value, "detach"):
        value = value.detach().cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return list(value)


class GroundingDinoDetector:
    def __init__(
        self,
        model_path: Path,
        config: RunConfig,
        *,
        processor: Any | None = None,
        model: Any | None = None,
        torch_module: Any | None = None,
    ) -> None:
        if torch_module is None:
            import torch as torch_module  # type: ignore[no-redef]
        if config.device.startswith("cuda") and (
            not hasattr(torch_module, "cuda") or not torch_module.cuda.is_available()
        ):
            raise RuntimeError("CUDA was requested but is unavailable; no CPU fallback")
        if processor is None or model is None:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

            processor = AutoProcessor.from_pretrained(
                model_path,
                local_files_only=True,
                trust_remote_code=False,
            )
            model = AutoModelForZeroShotObjectDetection.from_pretrained(
                model_path,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
            )
        self._torch = torch_module
        self._processor = processor
        self._model = model.eval().to(config.device)
        self._device = config.device
        self._box_threshold = config.dino_box_threshold
        self._text_threshold = config.dino_text_threshold
        self._closed = False

    def detect(self, frame: SampledFrame) -> list[Observation]:
        if self._closed:
            raise RuntimeError("Grounding DINO detector is closed")
        rgb = np.ascontiguousarray(frame.rgb, dtype=np.uint8)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("Grounding DINO input must be an RGB image")
        inputs = self._processor(images=rgb, text="hand.", return_tensors="pt")
        inputs = {
            key: value.to(self._device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        processed = self._processor.post_process_grounded_object_detection(
            outputs,
            input_ids=inputs.get("input_ids"),
            threshold=self._box_threshold,
            text_threshold=self._text_threshold,
            target_sizes=[(rgb.shape[0], rgb.shape[1])],
        )
        if len(processed) != 1:
            raise ValueError("Grounding DINO returned an unexpected batch size")
        boxes = _as_list(processed[0]["boxes"])
        scores = _as_list(processed[0]["scores"])
        if len(boxes) != len(scores):
            raise ValueError("Grounding DINO returned mismatched boxes and scores")
        observations: list[Observation] = []
        height, width = rgb.shape[:2]
        for box, raw_score in zip(boxes, scores, strict=True):
            if len(box) != 4:
                raise ValueError("Grounding DINO returned a malformed box")
            x1, y1, x2, y2 = (float(value) for value in box)
            score = float(raw_score)
            values = (x1, y1, x2, y2, score)
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Grounding DINO returned a non-finite detection")
            if x2 <= x1 or y2 <= y1:
                raise ValueError("Grounding DINO returned a malformed box")
            x1 = min(max(x1, 0.0), float(width))
            y1 = min(max(y1, 0.0), float(height))
            x2 = min(max(x2, 0.0), float(width))
            y2 = min(max(y2, 0.0), float(height))
            if x2 <= x1 or y2 <= y1:
                raise ValueError("Grounding DINO returned a box outside the image")
            source_min = frame.transform.source_xy(x1 / width, y1 / height)
            source_max = frame.transform.source_xy(x2 / width, y2 / height)
            observations.append(
                Observation(
                    frame_index=frame.source_index,
                    bbox=(*source_min, *source_max),
                    score=score,
                    score_kind="grounding_score",
                )
            )
        return observations

    def close(self) -> None:
        self._closed = True
        self._model = None
