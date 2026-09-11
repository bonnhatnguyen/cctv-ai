from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "generate-annotation-types.py"


def _generator():
    spec = importlib.util.spec_from_file_location("annotation_type_generator", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_contracts_are_deterministic_and_strict():
    generator = _generator()
    first_schema, first_types = generator.generated_outputs()
    second_schema, second_types = generator.generated_outputs()
    assert (first_schema, first_types) == (second_schema, second_types)
    assert "export interface ClipView" in first_types
    assert "export interface ActionWorkspaceView" in first_types
    for label in ("hand_in", "hand_out", "take_out", "put_in", "unclear"):
        assert label in first_types
    assert "any" not in first_types


def test_unknown_json_schema_construct_fails_closed():
    generator = _generator()
    with pytest.raises(ValueError, match="unsupported"):
        generator.schema_type({"not": {"type": "string"}})
