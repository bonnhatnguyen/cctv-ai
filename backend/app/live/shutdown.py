"""System shutdown endpoint for clean background process termination."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter

from .webcam import webcam_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.post("/shutdown")
def shutdown_system():
    # 1. Stop webcam immediately
    webcam_service.stop()

    # 2. Worker thread to cleanly terminate server and recorded launcher children
    def _shutdown_worker():
        time.sleep(0.6)
        try:
            for candidate in [
                Path("data/v1-launcher/launcher-state.json"),
                Path("../data/v1-launcher/launcher-state.json"),
            ]:
                if candidate.is_file():
                    try:
                        data = json.loads(candidate.read_text("utf-8-sig"))
                        for key in ("frontend", "backend"):
                            record = data.get(key)
                            if record and record.get("listener") and record["listener"].get("pid"):
                                pid = record["listener"]["pid"]
                                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                    except Exception:
                        pass
                    try:
                        candidate.unlink(missing_ok=True)
                    except Exception:
                        pass
        except Exception:
            pass

        # Terminate current process
        os._exit(0)

    threading.Thread(target=_shutdown_worker, daemon=True).start()
    return {"status": "shutting_down", "message": "Hệ thống đang dừng hoàn toàn."}
