"""No-hardware boot-service lifecycle checks."""
import logging
import signal
import subprocess
import sys
from threading import Event, Thread

import pytest

from redux.core.boot import run


def test_boot_waits_for_shutdown_and_reports_actual_scope(caplog):
    stop = Event()
    with caplog.at_level(logging.INFO):
        worker = Thread(target=run, args=(stop, logging.getLogger("redux.boot")))
        worker.start()
        try:
            assert worker.is_alive()
        finally:
            stop.set()
            worker.join(timeout=2)
    assert not worker.is_alive()
    assert "no radio operations are started" in caplog.text
    assert "shutdown requested" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX service signal gate")
def test_service_entrypoint_exits_cleanly_on_sigterm():
    process = subprocess.Popen(
        [sys.executable, "-m", "redux.core.boot"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        # Startup logging happens after both signal handlers are installed.
        assert "bootstrap active" in process.stderr.readline()
        process.send_signal(signal.SIGTERM)
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0
        assert "shutdown requested" in stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
