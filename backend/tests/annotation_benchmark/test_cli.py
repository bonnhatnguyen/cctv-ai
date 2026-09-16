from __future__ import annotations

import json
from pathlib import Path

from app.annotation_benchmark.cli import main


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_freeze_writes_checksummed_manifest_under_private_root(
    private_fixture, tmp_path: Path, capsys
) -> None:
    selection = tmp_path / "selection.json"
    _write_json(
        selection,
        {
            "schema_version": 1,
            "segments": [item.model_dump(mode="json") for item in private_fixture.selection],
        },
    )

    exit_code = main(
        [
            "freeze",
            "--source-root",
            str(private_fixture.source_root),
            "--annotation-root",
            str(private_fixture.annotation_root),
            "--selection",
            str(selection),
        ]
    )

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    manifest = Path(result["manifest"])
    assert manifest.parent == private_fixture.annotation_root / "benchmarks" / "manifests"
    assert manifest.is_file()
    assert manifest.with_suffix(".sha256").is_file()


def test_validate_rejects_tampered_manifest(private_fixture, tmp_path: Path, capsys) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    manifest.with_suffix(".sha256").write_text("0" * 64, encoding="ascii")
    config = tmp_path / "config.json"
    _write_json(config, {"schema_version": 1})
    model_root = tmp_path / "models"
    model_root.mkdir()

    assert main(
        [
            "validate",
            "--manifest",
            str(manifest),
            "--config",
            str(config),
            "--model-root",
            str(model_root),
            "--model",
            "mediapipe",
        ]
    ) == 2
    assert "checksum" in capsys.readouterr().err


def test_cli_rejects_url_inputs(capsys) -> None:
    assert main(
        [
            "freeze",
            "--source-root",
            "https://example.invalid/source",
            "--annotation-root",
            ".",
            "--selection",
            "selection.json",
        ]
    ) == 2
    assert "local" in capsys.readouterr().err
