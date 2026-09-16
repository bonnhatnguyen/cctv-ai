from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator


class InferenceCancelled(RuntimeError):
    pass


def default_inference_lock_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return (base / "CCTV-AI" / "inference.lock").resolve()


class InferenceLease:
    def __init__(self, path: Path | None = None, *, poll_seconds: float = 0.05) -> None:
        self.path = (path or default_inference_lock_path()).resolve()
        self.poll_seconds = poll_seconds

    @contextmanager
    def acquire(
        self, cancel_requested: Callable[[], bool] | None = None
    ) -> Iterator[None]:
        cancelled = cancel_requested or (lambda: False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        locked = False
        try:
            while not locked:
                if cancelled():
                    raise InferenceCancelled("inference lease wait was cancelled")
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError:
                    time.sleep(self.poll_seconds)
            yield
        finally:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
