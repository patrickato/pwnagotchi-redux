"""Native UI gesture integration tests: synthetic input, zero real device access."""
import json
from types import SimpleNamespace

import pytest
from redux.display import fbui, touch_nav


def event(kind, code, value):
    return touch_nav._EVENT.pack(0, 0, kind, code, value)


def tap(x, y):
    return (event(touch_nav.EV_KEY, touch_nav.BTN_TOUCH, 1) +
            event(touch_nav.EV_ABS, touch_nav.ABS_X, x) +
            event(touch_nav.EV_ABS, touch_nav.ABS_Y, y) +
            event(touch_nav.EV_KEY, touch_nav.BTN_TOUCH, 0))


def calibration_file(tmp_path):
    points = []
    for x, y in [(0, 0), (479, 0), (0, 319), (479, 319), (239.5, 159.5)]:
        points.append(dict(raw_x=x, raw_y=y, pixel_x=x, pixel_y=y))
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"schema": 1, "points": points}))
    return path


def test_footer_is_rendered_in_shared_four_tab_geometry():
    from redux.display.gesture_nav import TAB_TOP, TAB_WIDTH
    from redux.display import fbui
    image = fbui.render(None, page="captures")
    assert image.size == (480, 320)
    assert TAB_TOP == 276 and TAB_WIDTH * 5 == 480
    # Selected tab has a distinct filled background.
    assert image.getpixel((140, 300)) != image.getpixel((410, 300))


def test_calibrated_tap_redraws_selected_page_without_opening_radio(tmp_path, monkeypatch):
    path = calibration_file(tmp_path)
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: None)
    pages = []
    monkeypatch.setattr(fbui, "render", lambda *_a, page, **_kw: pages.append(page) or object())
    monkeypatch.setattr(fbui, "rgb565_bytes", lambda *_a, **_k: pages[-1].encode())
    writes = []
    def writer(_device, _frame):
        writes.append(1)
        if len(writes) == 2:
            raise RuntimeError("synthetic test stop")
    monkeypatch.setattr(fbui, "write_frame", writer)
    monkeypatch.setattr(touch_nav, "open_touch", lambda *_a: 123)
    monkeypatch.setattr(fbui.select, "select",
                        lambda *_a: ([123], [], []))
    monkeypatch.setattr(fbui.os, "read",
                        lambda *_a: tap(390, 308))
    closed = []
    monkeypatch.setattr(fbui.os, "close", closed.append)
    with pytest.raises(RuntimeError, match="synthetic test stop"):
        fbui.main(["--framebuffer", "/dev/fb1", "--run",
                   "--touch-device", "/dev/input/event4",
                   "--calibration-file", str(path),
                   "--confirm-touch-calibration"])
    assert pages == ["overview", "trends"]
    assert closed == [123]


def test_unverified_calibration_flag_cannot_enable_taps(tmp_path, monkeypatch):
    path = calibration_file(tmp_path)
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: pytest.fail("should not probe display"))
    with pytest.raises(SystemExit) as failure:
        fbui.main(["--framebuffer", "/dev/fb1", "--run",
                   "--touch-device", "/dev/input/event4",
                   "--calibration-file", str(path)])
    assert failure.value.code == 2


@pytest.mark.parametrize('read_error', [False, True])
def test_input_disconnect_keeps_ui_running_and_closes_once(monkeypatch, read_error):
    monkeypatch.setattr(fbui, 'framebuffer_info', lambda *_: {'ok': True, 'stride': 960})
    monkeypatch.setattr(fbui, 'read_status', lambda *_: None)
    monkeypatch.setattr(fbui, 'render', lambda *a, **k: object())
    frames = []
    monkeypatch.setattr(fbui, 'rgb565_bytes', lambda *a, **k: str(len(frames)).encode())
    def write(*args):
        frames.append(args)
        if len(frames) == 2:
            raise RuntimeError('display survived')
    monkeypatch.setattr(fbui, 'write_frame', write)
    monkeypatch.setattr(touch_nav, 'open_touch', lambda *_: 123)
    monkeypatch.setattr(fbui.select, 'select', lambda *_: ([123], [], []))
    def read(*_):
        if read_error:
            raise OSError('device disappeared')
        return b''
    monkeypatch.setattr(fbui.os, 'read', read)
    closed = []
    monkeypatch.setattr(fbui.os, 'close', closed.append)
    with pytest.raises(RuntimeError, match='display survived'):
        fbui.main(['--framebuffer', '/dev/fb1', '--run', '--touch-device', '/dev/input/event4'])
    assert closed == [123]
    assert len(frames) == 2


def test_identical_frames_skip_writes_with_bounded_previous_state(monkeypatch):
    monkeypatch.setattr(fbui, 'framebuffer_info', lambda *_: {'ok': True, 'stride': 960})
    reads = []
    def read(*_):
        reads.append(1)
        if len(reads) == 3:
            raise RuntimeError('two polls completed')
        return None
    monkeypatch.setattr(fbui, 'read_status', read)
    monkeypatch.setattr(fbui, 'render', lambda *a, **k: object())
    monkeypatch.setattr(fbui, 'rgb565_bytes', lambda *a, **k: b'identical frame')
    clock = iter(range(0, 100, 3))
    monkeypatch.setattr(fbui, 'time', SimpleNamespace(time=lambda: 100,
                        monotonic=lambda: next(clock), sleep=lambda _: None))
    writes = []
    monkeypatch.setattr(fbui, 'write_frame', lambda *args: writes.append(args))
    with pytest.raises(RuntimeError, match='two polls completed'):
        fbui.main(['--framebuffer', '/dev/fb1', '--run'])
    assert len(reads) == 3 and len(writes) == 1
