from __future__ import annotations

import sys

from app.owned_process import OwnedProcess


def test_closing_owned_process_terminates_a_running_child():
    owned = OwnedProcess([sys.executable, "-c", "import time; time.sleep(30)"])
    with owned as process:
        assert process.poll() is None

    assert process.poll() is not None
