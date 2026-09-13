from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess
import time
import re
from contextlib import closing
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "start-v1.ps1"
# start-v1.bat invokes Windows PowerShell, so lifecycle coverage must exercise
# that exact compatibility surface rather than a newer PowerShell Core runtime.
POWERSHELL = shutil.which("powershell.exe")


def _free_port_block(count: int = 12) -> int:
    for base in range(18000, 45000, count):
        sockets: list[socket.socket] = []
        try:
            for port in range(base, base + count):
                sock = socket.socket()
                sock.bind(("127.0.0.1", port))
                sockets.append(sock)
            return base
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("no free loopback port block")


def _run_launcher(
    state_dir: Path,
    backend_port: int,
    frontend_port: int,
    *extra: str,
    timeout: int = 90,
    data_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    stdout_path = state_dir.parent / f"launcher-{time.time_ns()}.stdout.txt"
    stderr_path = state_dir.parent / f"launcher-{time.time_ns()}.stderr.txt"
    args = _launcher_args(state_dir, backend_port, frontend_port, *extra, data_dir=data_dir)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            args,
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout,
        )
    return subprocess.CompletedProcess(
        args,
        completed.returncode,
        stdout_path.read_text(encoding="utf-8", errors="replace"),
        stderr_path.read_text(encoding="utf-8", errors="replace"),
    )


def _launcher_args(
    state_dir: Path, backend_port: int, frontend_port: int, *extra: str,
    data_dir: Path | None = None,
) -> list[str]:
    assert POWERSHELL
    return [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-LauncherDirectory",
        str(state_dir),
        "-DataDirectory",
        str(data_dir or (state_dir.parent / f"{state_dir.name}-backend-data")),
        "-BackendPort",
        str(backend_port),
        "-FrontendPort",
        str(frontend_port),
        "-NoBrowser",
        *extra,
    ]


def _wait_port_closed(port: int, timeout: float = 10) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with closing(socket.socket()) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.1)
    return False


pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="PowerShell is required only for the Windows launcher"
)


@pytest.mark.parametrize("dead", [("backend",), ("frontend",), ("backend", "frontend")])
def test_restart_recovers_fully_dead_recorded_components(tmp_path, dead):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    first = _run_launcher(state_dir, base, base + 1)
    try:
        assert first.returncode == 0, first.stdout + first.stderr
        state_path = state_dir / "launcher-state.json"
        before = json.loads(state_path.read_text("utf-8-sig"))
        for kind in dead:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(before[kind]["launcher"]["pid"]), "/T", "/F"],
                capture_output=True,
            )
            assert result.returncode == 0
            assert _wait_port_closed(before[kind]["port"])
        restarted = _run_launcher(state_dir, base, base + 1)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        after = json.loads(state_path.read_text("utf-8-sig"))
        for kind in ("backend", "frontend"):
            assert (after[kind]["generation"] != before[kind]["generation"]) == (kind in dead)
        identity = httpx.get(f"http://127.0.0.1:{base + 1}/__v1_identity").json()
        assert identity["backend_url"] == f"http://127.0.0.1:{base}"
        assert httpx.get(f"http://127.0.0.1:{base + 1}/api/v1/health").json()["instance_id"] == after["instance_id"]
    finally:
        stopped = _run_launcher(state_dir, base, base + 1, "-Stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


@pytest.mark.parametrize("kind", ["backend", "frontend"])
def test_dead_record_with_unrelated_live_listener_is_not_cleared(tmp_path, kind):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    first = _run_launcher(state_dir, base, base + 1)
    occupied = socket.socket()
    try:
        assert first.returncode == 0, first.stdout + first.stderr
        path = state_dir / "launcher-state.json"
        before_bytes = path.read_bytes()
        state = json.loads(before_bytes.decode("utf-8-sig"))
        subprocess.run(
            ["taskkill.exe", "/PID", str(state[kind]["launcher"]["pid"]), "/T", "/F"],
            capture_output=True, check=True,
        )
        assert _wait_port_closed(state[kind]["port"])
        occupied.bind(("127.0.0.1", state[kind]["port"]))
        occupied.listen()
        refused = _run_launcher(state_dir, base, base + 1)
        assert refused.returncode != 0
        assert path.read_bytes() == before_bytes
        assert occupied.getsockname()[1] == state[kind]["port"]
    finally:
        occupied.close()
        stopped = _run_launcher(state_dir, base, base + 1, "-Stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr


def test_launcher_preflight_resolves_project_runtime_and_dependencies(tmp_path):
    assert Path(POWERSHELL or "").name.lower() == "powershell.exe"
    assert "powershell.exe" in (ROOT / "start-v1.bat").read_text(
        encoding="utf-8"
    ).lower()
    base = _free_port_block()
    completed = _run_launcher(
        tmp_path / "state", base, base + 1, "-CheckOnly", timeout=60
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert str(ROOT / ".venv" / "Scripts" / "python.exe") in completed.stdout
    assert "V1 startup preflight passed" in completed.stdout
    assert re.search(
        r"Assisted labeling: (available \(.+\)|unavailable; manual labeling remains available)",
        completed.stdout,
    )


def test_repeat_launch_reuses_only_owned_services_with_matching_proxy_target(tmp_path):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    first = _run_launcher(state_dir, base, base + 1)
    try:
        assert first.returncode == 0, first.stdout + first.stderr
        state = json.loads((state_dir / "launcher-state.json").read_text("utf-8-sig"))
        backend_url = f"http://127.0.0.1:{base}"
        frontend_url = f"http://127.0.0.1:{base + 1}"

        identity = httpx.get(f"{frontend_url}/__v1_identity", timeout=3).json()
        direct_health = httpx.get(f"{backend_url}/api/v1/health", timeout=3).json()
        proxied_health = httpx.get(f"{frontend_url}/api/v1/health", timeout=3).json()
        repeat = _run_launcher(state_dir, base, base + 1)

        assert identity["backend_url"] == backend_url
        assert identity["backend_port"] == base
        assert proxied_health["service"] == direct_health["service"] == "v1-person-tracking"
        assert proxied_health["instance_id"] == direct_health["instance_id"]
        assert state["frontend"]["backend_url"] == backend_url
        assert repeat.returncode == 0, repeat.stdout + repeat.stderr
        assert "Reusing verified V1 backend" in repeat.stdout
        assert "Reusing verified V1 frontend" in repeat.stdout
        assert not list(state_dir.glob("launcher-state.*.tmp"))
        assert not list(state_dir.glob("launcher-state.*.backup"))
    finally:
        stopped = _run_launcher(state_dir, base, base + 1, "-Stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert _wait_port_closed(base)
        assert _wait_port_closed(base + 1)
        assert not (state_dir / "launcher-state.json").exists()


def test_owned_frontend_with_stale_backend_target_is_safely_replaced(tmp_path):
    base = _free_port_block(24)
    backend_a, frontend_a = base, base + 1
    backend_b, frontend_b = base + 12, base + 13
    state_a = tmp_path / "state-a"
    state_b = tmp_path / "state-b"
    state_c = tmp_path / "state-c"
    backend_b_data = state_b.parent / f"{state_b.name}-backend-data"
    first_a = _run_launcher(state_a, backend_a, frontend_a)
    first_b = _run_launcher(state_b, backend_b, frontend_b)
    try:
        assert first_a.returncode == 0, first_a.stdout + first_a.stderr
        assert first_b.returncode == 0, first_b.stdout + first_b.stderr
        data_a = json.loads((state_a / "launcher-state.json").read_text("utf-8-sig"))
        data_b = json.loads((state_b / "launcher-state.json").read_text("utf-8-sig"))
        killed = subprocess.run(
            ["taskkill.exe", "/PID", str(data_b["frontend"]["launcher"]["pid"]), "/T", "/F"],
            capture_output=True,
        )
        assert killed.returncode == 0
        assert _wait_port_closed(frontend_b)
        state_c.mkdir()
        composite = {
            "schema_version": 2,
            "project_root": str(ROOT),
            "instance_id": data_a["instance_id"],
            "backend": data_b["backend"],
            "frontend": data_a["frontend"],
            "updated_at": data_a["updated_at"],
        }
        (state_c / "launcher-state.json").write_text(json.dumps(composite), encoding="utf-8")
        replaced = _run_launcher(state_c, backend_b, frontend_a, data_dir=backend_b_data)
        assert replaced.returncode == 0, replaced.stdout + replaced.stderr
        assert "backend target is stale" in replaced.stdout
        identity = httpx.get(f"http://127.0.0.1:{frontend_a}/__v1_identity", timeout=3).json()
        proxied = httpx.get(f"http://127.0.0.1:{frontend_a}/api/v1/health", timeout=3).json()
        assert identity["backend_url"] == f"http://127.0.0.1:{backend_b}"
        assert proxied["instance_id"] == data_b["instance_id"]
    finally:
        if (state_c / "launcher-state.json").exists():
            assert _run_launcher(
                state_c, backend_b, frontend_a, "-Stop", data_dir=backend_b_data
            ).returncode == 0
        if (state_a / "launcher-state.json").exists():
            assert _run_launcher(state_a, backend_a, frontend_a, "-Stop").returncode == 0
        if (state_b / "launcher-state.json").exists():
            assert _run_launcher(state_b, backend_b, frontend_b, "-Stop").returncode == 0
        for port in (backend_a, frontend_a, backend_b, frontend_b):
            assert _wait_port_closed(port)


def test_stop_refuses_unrelated_process_that_reuses_recorded_vite_pid(tmp_path):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    unrelated = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-c", "import time; time.sleep(60)", "vite"],
        cwd=tmp_path,
    )
    try:
        state = {
            "schema_version": 2,
            "project_root": str(ROOT),
            "instance_id": hashlib.sha256(str(ROOT).lower().encode()).digest()[:8].hex(),
            "backend": None,
            "frontend_pid": unrelated.pid,
            "frontend_port": base + 1,
            "backend_port": base,
            "frontend": {
                "kind": "frontend",
                "phase": "ready",
                "generation": "recorded-before-pid-reuse",
                "project_root": str(ROOT),
                "port": base + 1,
                "url": f"http://127.0.0.1:{base + 1}",
                "backend_url": f"http://127.0.0.1:{base}",
                "launcher": {
                    "pid": unrelated.pid,
                    "parent_pid": 1,
                    "created_utc": "2000-01-01T00:00:00.0000000Z",
                    "executable": "C:\\Windows\\System32\\cmd.exe",
                    "command": (
                        f"cmd.exe /c pnpm exec vite {ROOT / 'frontend'} "
                        f"--config {ROOT / 'frontend' / 'vite.config.ts'} "
                        f"--host 127.0.0.1 --port {base + 1} --strictPort"
                    ),
                },
                "listener": {
                    "pid": unrelated.pid,
                    "parent_pid": 1,
                    "created_utc": "2000-01-01T00:00:00.0000000Z",
                    "executable": "C:\\fake\\node.exe",
                    "command": f"node.exe {ROOT / 'frontend' / 'fake-vite.js'}",
                },
            },
        }
        (state_dir / "launcher-state.json").write_text(json.dumps(state), encoding="utf-8")

        stopped = _run_launcher(state_dir, base, base + 1, "-Stop")

        assert stopped.returncode != 0
        assert unrelated.poll() is None
        assert (state_dir / "launcher-state.json").is_file()
        assert "Refusing to stop" in stopped.stderr
        assert "different process generation" in stopped.stderr
    finally:
        if unrelated.poll() is None:
            unrelated.terminate()
            unrelated.wait(timeout=5)


def test_partial_frontend_failure_cleans_only_backend_started_by_this_run(tmp_path):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    occupied: list[socket.socket] = []
    try:
        for port in range(base + 1, base + 12):
            sock = socket.socket()
            sock.bind(("127.0.0.1", port))
            sock.listen()
            occupied.append(sock)

        completed = _run_launcher(
            state_dir,
            base,
            base + 1,
            "-AlternatePortCount",
            "10",
        )

        assert completed.returncode != 0
        assert "Started verified V1 backend" in completed.stdout
        assert "No safe loopback port is available for the V1 frontend" in completed.stderr
        assert "Diagnostics:" in completed.stderr
        assert _wait_port_closed(base)
        assert not (state_dir / "launcher-state.json").exists()
    finally:
        for sock in occupied:
            sock.close()


def test_starting_child_ownership_is_persisted_before_readiness_failure(tmp_path):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    bad_data_path = tmp_path / "state-backend-data"
    bad_data_path.write_text("a file cannot be the backend data directory", encoding="utf-8")
    stdout_path = tmp_path / "pending.stdout.txt"
    stderr_path = tmp_path / "pending.stderr.txt"
    args = _launcher_args(
        state_dir,
        base,
        base + 1,
        "-ReadyTimeoutSeconds",
        "3",
    )
    observed_backend: dict | None = None
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        launcher = subprocess.Popen(args, cwd=ROOT, stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 7
        while launcher.poll() is None and time.monotonic() < deadline:
            state_path = state_dir / "launcher-state.json"
            if state_path.exists():
                candidate = json.loads(state_path.read_text("utf-8-sig"))
                if candidate.get("backend"):
                    observed_backend = candidate["backend"]
                    break
            time.sleep(0.05)
        returncode = launcher.wait(timeout=10)

    assert observed_backend is not None
    assert observed_backend["phase"] == "starting"
    assert returncode != 0
    assert "Timed out waiting for the V1 backend" in stderr_path.read_text(
        encoding="utf-8", errors="replace"
    )
    assert _wait_port_closed(base)
    assert not (state_dir / "launcher-state.json").exists()


def test_frontend_failure_preserves_previously_owned_backend(tmp_path):
    base = _free_port_block()
    state_dir = tmp_path / "state"
    first = _run_launcher(state_dir, base, base + 1)
    occupied: list[socket.socket] = []
    try:
        assert first.returncode == 0, first.stdout + first.stderr
        state_path = state_dir / "launcher-state.json"
        state = json.loads(state_path.read_text("utf-8-sig"))
        original_generation = state["backend"]["generation"]

        killed = subprocess.run(
            ["taskkill.exe", "/PID", str(state["frontend"]["launcher"]["pid"]), "/T", "/F"],
            capture_output=True,
        )
        assert killed.returncode == 0
        assert _wait_port_closed(base + 1)
        state["frontend"] = None
        replacement = state_path.with_suffix(".replacement")
        replacement.write_text(json.dumps(state), encoding="utf-8")
        replacement.replace(state_path)

        for port in range(base + 1, base + 12):
            sock = socket.socket()
            sock.bind(("127.0.0.1", port))
            sock.listen()
            occupied.append(sock)

        failed = _run_launcher(
            state_dir, base, base + 1, "-AlternatePortCount", "10"
        )

        preserved = json.loads(state_path.read_text("utf-8-sig"))
        health = httpx.get(f"http://127.0.0.1:{base}/api/v1/health", timeout=3).json()
        assert failed.returncode != 0
        assert "Reusing verified V1 backend" in failed.stdout
        assert preserved["backend"]["generation"] == original_generation
        assert preserved["frontend"] is None
        assert health["service"] == "v1-person-tracking"
    finally:
        for sock in occupied:
            sock.close()
        stopped = _run_launcher(state_dir, base, base + 1, "-Stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert _wait_port_closed(base)
