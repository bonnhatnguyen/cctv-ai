from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path

from .contracts import FrozenManifest, ModelAsset, RunConfig
from .runner import _canonical_bytes, _failure_reason, _perform_inference


def _block_python_network() -> None:
    def blocked(*args, **kwargs):
        raise OSError("network access is disabled for benchmark inference")

    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--model", required=True, choices=("mediapipe", "dino"))
    args = parser.parse_args(argv)
    stage = args.stage.resolve(strict=True)
    try:
        _block_python_network()
        manifest = FrozenManifest.model_validate_json((stage / "manifest.json").read_text())
        config = RunConfig.model_validate_json((stage / "config.json").read_text())
        asset = ModelAsset.model_validate_json((stage / "asset.json").read_text())
        result = _perform_inference(
            stage,
            manifest,
            config,
            args.model,
            asset,
            args.source_root.resolve(strict=True),
            args.model_root.resolve(strict=True),
        )
        (stage / "worker-result.json").write_bytes(_canonical_bytes(result))
        return 0
    except BaseException as exc:
        error = {"reason": _failure_reason(exc), "detail": str(exc)}
        try:
            (stage / "worker-error.json").write_bytes(_canonical_bytes(error))
        except OSError:
            pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
