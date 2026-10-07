"""No-hardware boot-service lifecycle checks."""
import logging
import signal
import subprocess
import sys
from threading import Event, Thread

import pytest

from redux.core.boot import run
from redux.core.boot import atomic_checkpoint, previous_checkpoint, watchdog_interval, notify


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


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux directory fsync')
def test_checkpoint_distinguishes_clean_unclean_and_invalid_state(tmp_path):
    path = tmp_path / 'boot.json'
    assert previous_checkpoint(path)[0] == 'missing'
    atomic_checkpoint(path, {'schema': 1, 'clean_shutdown': False})
    assert previous_checkpoint(path)[0] == 'unclean'
    assert 'without RF replay' in previous_checkpoint(path)[1]
    atomic_checkpoint(path, {'schema': 1, 'clean_shutdown': True})
    assert previous_checkpoint(path)[0] == 'clean'
    path.write_text('{truncated')
    assert previous_checkpoint(path)[0] == 'invalid'
    path.write_text('{"schema": true, "clean_shutdown": 0}')
    assert previous_checkpoint(path)[0] == 'invalid'


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux directory fsync')
def test_shutdown_commits_record_and_notification_after_loop_progress(tmp_path):
    import json
    path = tmp_path / 'boot.json'
    stop = Event()
    messages = []
    def send(message):
        messages.append(message)
        if message.startswith('WATCHDOG='):
            stop.set()
    run(stop, logging.getLogger('test'), path, send, 0.001)
    assert messages[0].startswith('READY=1')
    assert any(m.startswith('WATCHDOG=1') for m in messages)
    assert messages[-1].startswith('STOPPING=1')
    assert json.loads(path.read_text())['clean_shutdown'] is True
    assert not list(tmp_path.glob('.boot-*'))


@pytest.mark.skipif(sys.platform == 'win32', reason='Unix notification socket')
def test_notify_uses_actual_datagram_and_refuses_wrong_watchdog_pid(tmp_path):
    import socket
    address = str(tmp_path / 'notify')
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
        listener.bind(address)
        listener.settimeout(1)
        assert notify('READY=1', address)
        assert listener.recv(4096) == b'READY=1'
    assert watchdog_interval({'WATCHDOG_USEC': '15000000', 'WATCHDOG_PID': '42'}, 42) == 1
    with pytest.raises(ValueError, match='different process'):
        watchdog_interval({'WATCHDOG_USEC': '15000000', 'WATCHDOG_PID': '41'}, 42)
    with pytest.raises(ValueError):
        watchdog_interval({'WATCHDOG_USEC': '0'}, 42)
    with pytest.raises(ValueError):
        notify('READY=1', 'relative/path')


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX symlinks')
def test_checkpoint_does_not_overwrite_symlink_target(tmp_path):
    target = tmp_path / 'keep'
    target.write_text('preserve')
    path = tmp_path / 'boot.json'
    path.symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        atomic_checkpoint(path, {'schema': 1})
    assert target.read_text() == 'preserve'


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux directory fsync')
def test_failed_checkpoint_replace_preserves_previous_record(tmp_path, monkeypatch):
    import os
    path = tmp_path / 'boot.json'
    atomic_checkpoint(path, {'schema': 1, 'clean_shutdown': True})
    before = path.read_bytes()
    def fail_replace(*args):
        raise OSError('simulated interrupted rename')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError, match='interrupted'):
        atomic_checkpoint(path, {'schema': 1, 'clean_shutdown': False})
    assert path.read_bytes() == before
    assert not list(tmp_path.glob('.boot-*'))
