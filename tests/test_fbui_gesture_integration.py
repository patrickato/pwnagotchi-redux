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
    assert TAB_TOP == 296 and TAB_WIDTH * 4 == 480
    # Selected tab has a distinct filled background.
    assert image.getpixel((250, 305)) != image.getpixel((410, 305))


def test_calibrated_tap_redraws_selected_page_without_opening_radio(tmp_path, monkeypatch):
    path = calibration_file(tmp_path)
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: None)
    pages = []
    monkeypatch.setattr(fbui, "render", lambda *_a, page: pages.append(page) or object())
    monkeypatch.setattr(fbui, "rgb565_bytes", lambda *_a, **_k: b"frame")
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
    assert pages == ["overview", "radios"]
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
