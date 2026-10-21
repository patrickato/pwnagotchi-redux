"""Synthetic evdev lifecycle; no devices opened or physical acceptance claimed."""
import pytest
from redux.display.touch_nav import TouchReconnect


def test_disconnect_absence_identity_mismatch_and_qualified_return():
    clock, opened, closed = [0], [], []
    identities = {10: ('controller', 'physical-port'), 12: ('other', 'port'), 13: ('controller', 'physical-port')}
    results = [OSError('absent'), 12, 13]
    def open_node(path):
        assert path == '/dev/input/event4'
        opened.append(clock[0])
        result = results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
    connection = TouchReconnect('/dev/input/event4', 10, identity=identities.__getitem__,
        opener=open_node, closer=closed.append, clock=lambda: clock[0])
    connection.disconnect()
    assert closed == [10]
    for _ in range(100):
        assert connection.poll() is None
    assert opened == []
    clock[0] = 5
    assert connection.poll() is None
    clock[0] = 10
    assert connection.poll() is None
    assert 'mismatch' in connection.state
    assert closed == [10, 12]
    clock[0] = 15
    assert connection.poll() == 13
    assert connection.state == 'connected'
    assert connection.poll() is None
    connection.close()
    connection.close()
    assert closed == [10, 12, 13]


def test_unverified_identity_never_reopens_unknown_replacement():
    def unavailable(fd):
        raise OSError('unverified')
    connection = TouchReconnect('/dev/input/event4', 10, identity=unavailable,
        opener=lambda _: pytest.fail('unverified controller reopened'), closer=lambda _: None, clock=lambda: 100)
    connection.disconnect()
    connection.next_try = 0
    assert connection.poll() is None


def test_native_loop_reconnects_same_controller_and_requires_new_contact(monkeypatch):
    from types import SimpleNamespace
    from redux.display import fbui, touch_nav
    from test_fbui_gesture_integration import tap
    from redux.display.calibration import Calibration
    from redux.display import calibration_file
    clock, opened, closed, pages = [0.0], [], [], []
    def open_node(path):
        fd = 123 if not opened else 456
        opened.append(fd)
        return fd
    real_connection = touch_nav.TouchReconnect
    monkeypatch.setattr(touch_nav, 'open_touch', open_node)
    monkeypatch.setattr(touch_nav, 'TouchReconnect', lambda *a, **kw: real_connection(*a,
        identity=lambda fd: ('controller', 'same-port'), clock=lambda: clock[0], **kw))
    monkeypatch.setattr(fbui, 'framebuffer_info', lambda *_: {'ok': True, 'stride': 960})
    monkeypatch.setattr(calibration_file, 'load_calibration', lambda _: Calibration(False, False, False, 0, 479, 0, 319, 480, 320, 0))
    def status(*_):
        clock[0] += 6
        return None
    monkeypatch.setattr(fbui, 'read_status', status)
    monkeypatch.setattr(fbui, 'render', lambda *a, page, **kw: pages.append(page) or object())
    monkeypatch.setattr(fbui, 'rgb565_bytes', lambda *a, **kw: pages[-1].encode())
    writes = []
    def write(*_):
        writes.append(pages[-1])
        if pages[-1] == 'trends':
            raise RuntimeError('navigation recovered')
    monkeypatch.setattr(fbui, 'write_frame', write)
    monkeypatch.setattr(fbui.select, 'select', lambda fds, *a: (fds, [], []))
    monkeypatch.setattr(fbui.os, 'read', lambda fd, _: b'' if fd == 123 else tap(390, 308))
    monkeypatch.setattr(fbui.os, 'close', closed.append)
    def sleep(seconds):
        clock[0] += max(seconds, .01)
    monkeypatch.setattr(fbui, 'time', SimpleNamespace(time=lambda: 1000,
        monotonic=lambda: clock[0], sleep=sleep))
    with pytest.raises(RuntimeError, match='navigation recovered'):
        fbui.main(['--framebuffer', '/dev/fb1', '--run', '--touch-device', '/dev/input/event4',
                    '--calibration-file', '/tmp/synthetic-calibration', '--confirm-touch-calibration'])
    assert opened == [123, 456] and closed == [123, 456]
    assert writes == ['overview', 'trends']
    assert pages == ['overview', 'overview', 'overview', 'trends']
