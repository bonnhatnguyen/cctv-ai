from __future__ import annotations

import threading
import time

from app.inference_lease import InferenceLease


def test_second_inference_waits_until_first_releases(tmp_path):
    path = tmp_path / "inference.lock"
    first = InferenceLease(path)
    second = InferenceLease(path)
    entered = threading.Event()

    def acquire_second():
        with second.acquire():
            entered.set()

    with first.acquire():
        thread = threading.Thread(target=acquire_second)
        thread.start()
        time.sleep(0.15)
        assert not entered.is_set()
    thread.join(timeout=2)
    assert entered.is_set()
