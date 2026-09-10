from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


def test_v1_batch_launcher_delegates_without_replacing_legacy_launcher():
    launcher = (ROOT / "start-v1.bat").read_text(encoding="utf-8")

    assert "scripts\\start-v1.ps1" in launcher
    assert (ROOT / "start-local.bat").is_file()


def test_launcher_preflight_resolves_project_runtime_and_dependencies():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is required only for the Windows launcher")

    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "start-v1.ps1"),
            "-CheckOnly",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert str(ROOT / ".venv" / "Scripts" / "python.exe") in completed.stdout
    assert "V1 startup preflight passed" in completed.stdout


def test_frontend_identity_document_is_exact_and_private():
    identity_path = ROOT / "frontend" / "public" / "v1-service-identity.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))

    assert identity == {
        "service": "v1-person-tracking-ui",
        "version": "1",
        "network": "loopback-only",
    }


def test_vite_configuration_has_no_legacy_live_proxy():
    vite_config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert "V1_BACKEND_PORT" in vite_config
    assert "V1_TRACKING_INSTANCE_ID" in vite_config
    assert '"/__v1_identity"' in vite_config
    assert '"http://127.0.0.1:8000"' in vite_config
    assert '"/live"' not in vite_config
