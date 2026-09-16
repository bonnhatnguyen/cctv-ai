from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import ValidationError

from .contracts import (
    EffortRecord,
    FrozenManifest,
    ModelAsset,
    Proposal,
    RunConfig,
    SelectionItem,
)
from .metrics import evaluate
from .models import verify_asset
from .runner import BenchmarkRunError, _check_capacity, _sha256_file, run_benchmark
from .snapshot import SnapshotError, read_snapshot


class CliError(RuntimeError):
    pass


def _local_path(value: str) -> Path:
    if value.startswith("\\\\"):
        raise argparse.ArgumentTypeError("only local non-UNC filesystem paths are accepted")
    parsed = urlparse(value)
    if parsed.scheme and not (len(parsed.scheme) == 1 and value[1:3] in {":\\", ":/"}):
        raise argparse.ArgumentTypeError("only local filesystem paths are accepted")
    return Path(value)


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_checksummed(path: Path, payload: object) -> None:
    encoded = _canonical_bytes(payload)
    path.write_bytes(encoded)
    path.with_suffix(".sha256").write_text(
        hashlib.sha256(encoded).hexdigest() + "\n", encoding="ascii"
    )


def _load_json(path: Path, *, checksum: bool = False) -> object:
    resolved = path.resolve(strict=True)
    raw = resolved.read_bytes()
    if checksum:
        digest_path = resolved.with_suffix(".sha256")
        try:
            expected = digest_path.read_text(encoding="ascii").strip()
        except OSError as exc:
            raise CliError("manifest checksum file is missing") from exc
        actual = hashlib.sha256(raw).hexdigest()
        if expected != actual:
            raise CliError("manifest checksum mismatch")
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"invalid JSON: {resolved.name}") from exc


def _asset_directory(model_root: Path, model: str) -> Path:
    root = model_root.resolve(strict=True)
    child = root / (
        "mediapipe-hand-landmarker" if model == "mediapipe" else "grounding-dino-tiny"
    )
    if (root / "asset.json").is_file():
        return root
    if (child / "asset.json").is_file():
        return child.resolve(strict=True)
    raise CliError(f"asset.json is missing for {model}")


def _load_inputs(manifest_path: Path, config_path: Path) -> tuple[FrozenManifest, RunConfig]:
    return (
        FrozenManifest.model_validate(_load_json(manifest_path, checksum=True)),
        RunConfig.model_validate(_load_json(config_path)),
    )


def _load_asset(model_root: Path, model: str) -> tuple[Path, ModelAsset]:
    directory = _asset_directory(model_root, model)
    asset = ModelAsset.model_validate(_load_json(directory / "asset.json"))
    expected = {
        "mediapipe": "mediapipe-hand-landmarker",
        "dino": "IDEA-Research/grounding-dino-tiny",
    }[model]
    if asset.model_id != expected:
        raise CliError("asset model_id does not match --model")
    verify_asset(directory, asset)
    return directory, asset


def _freeze(args: argparse.Namespace) -> dict[str, object]:
    payload = _load_json(args.selection)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CliError("selection schema_version must be 1")
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise CliError("selection segments must be a list")
    selection = [SelectionItem.model_validate(item) for item in segments]
    manifest = read_snapshot(args.source_root, args.annotation_root, selection)
    annotation_root = args.annotation_root.resolve(strict=True)
    output_root = annotation_root / "benchmarks" / "manifests"
    output_root.mkdir(parents=True, exist_ok=True)
    if not output_root.resolve(strict=True).is_relative_to(annotation_root):
        raise CliError("manifest output escapes the private annotation root")
    output = output_root / f"{manifest.manifest_id}.json"
    if output.exists() or output.with_suffix(".sha256").exists():
        raise CliError("manifest output collision")
    _write_checksummed(output, manifest.model_dump(mode="json"))
    return {
        "manifest": str(output.resolve()),
        "reference_state": manifest.reference_state,
        "missing_scenarios": list(manifest.missing_scenarios),
    }


def _validate(args: argparse.Namespace) -> dict[str, object]:
    manifest, _ = _load_inputs(args.manifest, args.config)
    directory, asset = _load_asset(args.model_root, args.model)
    return {
        "valid": True,
        "manifest_id": str(manifest.manifest_id),
        "model_id": asset.model_id,
        "model_directory": str(directory),
        "reference_state": manifest.reference_state,
    }


def _run(args: argparse.Namespace) -> dict[str, object]:
    manifest, config = _load_inputs(args.manifest, args.config)
    directory, asset = _load_asset(args.model_root, args.model)
    output = run_benchmark(
        manifest,
        config,
        args.model,
        asset,
        args.source_root,
        args.annotation_root,
        directory,
    )
    return {"run_id": output.name, "output": str(output)}


def _verify_completed_run(run: Path) -> tuple[dict[str, object], Path]:
    artifact_payload = _load_json(run / "artifacts.sha256.json")
    if not isinstance(artifact_payload, dict) or artifact_payload.get("schema_version") != 1:
        raise CliError("run artifact manifest is invalid")
    files = artifact_payload.get("files")
    if not isinstance(files, dict):
        raise CliError("run artifact hashes are invalid")
    required = {"status.json", "manifest.json", "config.json", "proposals.json", "run.json"}
    if not required.issubset(files):
        raise CliError("run artifact manifest is incomplete")
    for relative_name, expected in files.items():
        if not isinstance(relative_name, str) or not isinstance(expected, str):
            raise CliError("run artifact hash entry is invalid")
        candidate = (run / relative_name).resolve(strict=True)
        if not candidate.is_relative_to(run) or not candidate.is_file():
            raise CliError("run artifact escapes its completed directory")
        if _sha256_file(candidate) != expected:
            raise CliError(f"run artifact checksum mismatch: {relative_name}")
    run_payload = _load_json(run / "run.json")
    if not isinstance(run_payload, dict) or run_payload.get("run_id") != run.name:
        raise CliError("run identity is invalid")
    binding = run_payload.get("binding")
    if not isinstance(binding, dict):
        raise CliError("run private-root binding is missing")
    annotation_root = Path(str(binding.get("annotation_root"))).resolve(strict=True)
    expected = (annotation_root / "benchmarks" / "runs" / run.name).resolve(strict=True)
    if run != expected:
        raise CliError("run directory is outside its bound private annotation root")
    return run_payload, annotation_root / "benchmarks"


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    run = args.run.resolve(strict=True)
    run_payload, benchmark_root = _verify_completed_run(run)
    if json.loads((run / "status.json").read_text(encoding="utf-8"))["state"] != "completed":
        raise CliError("only completed runs can be evaluated")
    manifest = FrozenManifest.model_validate(_load_json(run / "manifest.json"))
    proposal_payload = _load_json(run / "proposals.json")
    if not isinstance(proposal_payload, list):
        raise CliError("run proposals must be a list")
    proposals = [Proposal.model_validate(item) for item in proposal_payload]
    effort: list[EffortRecord] = []
    if args.effort is not None:
        effort_payload = _load_json(args.effort)
        records = effort_payload.get("records") if isinstance(effort_payload, dict) else effort_payload
        if not isinstance(records, list):
            raise CliError("effort input must be a list or {records: [...]} object")
        effort = [EffortRecord.model_validate(item) for item in records]
        expected_run_id = run_payload["run_id"]
        if any(
            record.mode == "assisted" and str(record.model_run_id) != expected_run_id
            for record in effort
        ):
            raise CliError("assisted effort model_run_id does not match the evaluated run")
    config = RunConfig.model_validate(_load_json(run / "config.json"))
    _check_capacity(benchmark_root, config)
    reports = benchmark_root / "reports" / run.name
    reports.mkdir(parents=True, exist_ok=True)
    if not reports.resolve(strict=True).is_relative_to(benchmark_root):
        raise CliError("report output escapes benchmark root")
    output = reports / f"{uuid4()}.json"
    if output.exists() or output.with_suffix(".sha256").exists():
        raise CliError("report output collision")
    report = evaluate(manifest, proposals, effort)
    _write_checksummed(output, report)
    _check_capacity(benchmark_root, config)
    return {"report": str(output.resolve()), "reference_state": report["reference_state"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="annotation-benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--source-root", required=True, type=_local_path)
    freeze.add_argument("--annotation-root", required=True, type=_local_path)
    freeze.add_argument("--selection", required=True, type=_local_path)
    freeze.set_defaults(handler=_freeze)

    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=_local_path)
    validate.add_argument("--config", required=True, type=_local_path)
    validate.add_argument("--model-root", required=True, type=_local_path)
    validate.add_argument("--model", required=True, choices=("mediapipe", "dino"))
    validate.set_defaults(handler=_validate)

    run = commands.add_parser("run")
    run.add_argument("--manifest", required=True, type=_local_path)
    run.add_argument("--config", required=True, type=_local_path)
    run.add_argument("--model", required=True, choices=("mediapipe", "dino"))
    run.add_argument("--source-root", required=True, type=_local_path)
    run.add_argument("--annotation-root", required=True, type=_local_path)
    run.add_argument("--model-root", required=True, type=_local_path)
    run.set_defaults(handler=_run)

    evaluate_command = commands.add_parser("evaluate")
    evaluate_command.add_argument("--run", required=True, type=_local_path)
    evaluate_command.add_argument("--effort", type=_local_path)
    evaluate_command.set_defaults(handler=_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = args.handler(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    except (
        CliError,
        SnapshotError,
        BenchmarkRunError,
        ValidationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
