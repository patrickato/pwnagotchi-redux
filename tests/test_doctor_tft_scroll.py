"""Doctor TFT scroll and navigation: measured-lookalike synthetic status, no devices."""
import time

import pytest

from redux.display import fbui, touch_nav


def report(now=None, num=9):
    now = time.time() if now is None else now
    return {"runtime": {"state": "running", "updated_utc": now},
            "doctor": {"overall": "degraded", "findings": [
                {"status": "degraded", "summary": f"Distinct issue {i:02d}"}
                for i in range(num)]}}


def evt(t, c, v):
    return touch_nav._EVENT.pack(0, 0, t, c, v)


def vertical_swipe():
    return (evt(touch_nav.EV_KEY, touch_nav.BTN_TOUCH, 1)
            + evt(touch_nav.EV_ABS, touch_nav.ABS_X, 180)
            + evt(touch_nav.EV_ABS, touch_nav.ABS_Y, 270)
            + evt(touch_nav.EV_SYN, 0, 0)
            + evt(touch_nav.EV_ABS, touch_nav.ABS_Y, 180)
            + evt(touch_nav.EV_KEY, touch_nav.BTN_TOUCH, 0))


def test_all_actionable_findings_accessible_in_bounded_windows():
    r = report(now=1000, num=9)
    view = fbui.status_view(r, now=1001)
    assert len(view["doctor_all"]) == 9
    assert len(view["doctor_rows"]) == 3
    first = fbui.doctor_window(view, 0)
    second = fbui.doctor_window(view, 3)
    third = fbui.doctor_window(view, 6)
    assert first["start"] == 0 and first["more"] == 6
    assert second["start"] == 3 and second["more"] == 3
    assert third["start"] == 6 and third["more"] == 0
    assert (first["rows"] + second["rows"] + third["rows"]
            == view["doctor_all"])


def test_scroll_clamps_to_last_complete_window_and_handles_empty_data():
    r = report(now=1000, num=5)
    v = fbui.status_view(r, now=1001)
    assert fbui.doctor_window(v, 999999999)["start"] == 2
    assert len(fbui.doctor_window(v, 999999999)["rows"]) == 3
    for bad in (-1, True, 0.5, "2", None):
        with pytest.raises(ValueError):
            fbui.doctor_window(v, bad)
    stale = fbui.status_view(r, now=2000)
    assert fbui.doctor_window(stale, 100)["rows"] == []
    assert fbui.doctor_window(stale, 100)["total"] == 0


def test_rendered_doctor_windows_change_actual_rgb_pixels():
    r = report(now=1000, num=8)
    first = fbui.render(r, page="doctor", now=1001, doctor_offset=0)
    second = fbui.render(r, page="doctor", now=1001, doctor_offset=3)
    third = fbui.render(r, page="doctor", now=1001, doctor_offset=6)
    assert first.size == second.size == third.size == (480, 320)
    assert len({first.tobytes(), second.tobytes(), third.tobytes()}) == 3
    assert fbui.render(r, page="doctor", now=2000,
                       doctor_offset=6).size == (480, 320)


def test_only_valid_current_doctor_findings_enable_paging():
    r = report(now=1000, num=80)
    r["doctor"]["findings"].extend([None, "bad", {}])
    v = fbui.status_view(r, now=1001)
    assert len(v["doctor_all"]) == 64
    assert fbui.doctor_window(v, 62)["start"] == 61
    assert len(fbui.doctor_window(v, 62)["rows"]) == 3
    r["runtime"]["updated_utc"] = 0
    assert fbui.status_view(r, now=1001)["doctor_all"] == []


def test_full_fbui_loop_scrolls_doctor_not_radio(monkeypatch):
    from redux.display import gesture_nav
    from redux.display.calibration import Calibration
    calibration = Calibration(False, False, False, 0, 479, 0, 319,
                              480, 320, 0)
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: report())
    pages = []
    renderer = fbui.render
    def record_renderer(status, *, page, **kwargs):
        pages.append((page, kwargs.get("doctor_offset", 0)))
        return renderer(status, page=page, **kwargs)
    monkeypatch.setattr(fbui, "render", record_renderer)
    writes = []
    def fake_write(_dev, _frame):
        writes.append(1)
        if len(writes) == 2:
            raise RuntimeError("stop after second UI frame")
    monkeypatch.setattr(fbui, "write_frame", fake_write)
    monkeypatch.setattr(touch_nav, "open_touch", lambda *_a: 123)
    monkeypatch.setattr(fbui.select, "select", lambda *_a: ([123], [], []))
    monkeypatch.setattr(fbui.os, "read", lambda *_a: vertical_swipe())
    closed = []
    monkeypatch.setattr(fbui.os, "close", closed.append)
    # Inject the qualified decoder as though calibration was accepted;
    # no file/dev/input interface or actual RF operation is opened.
    import redux.display.calibration_file as calibration_file
    monkeypatch.setattr(calibration_file, "load_calibration",
                        lambda *_a: calibration)
    with pytest.raises(RuntimeError, match="stop after second UI frame"):
        fbui.main(["--framebuffer", "/dev/fb1", "--run", "--page", "doctor",
                   "--touch-device", "/dev/input/event4",
                   "--calibration-file", "/tmp/synthetic-unused.json",
                   "--confirm-touch-calibration"])
    assert pages == [("doctor", 0), ("doctor", 3)]
    assert closed == [123]


def test_preview_exports_scrolled_doctor_pixels_and_validates_cli(tmp_path, capsys):
    import json
    from redux.display import preview
    r = report(now=1000, num=8)
    first = preview.build(report=r, pages=("doctor",), doctor_offset=0)["doctor"]
    second = preview.build(report=r, pages=("doctor",), doctor_offset=3)["doctor"]
    # Both renders from a historical sample are safely marked stale relative
    # to wall time, so inject actual current sample for visual pagination.
    current = report(num=8)
    a = preview.build(report=current, pages=("doctor",), doctor_offset=0)["doctor"]
    b = preview.build(report=current, pages=("doctor",), doctor_offset=3)["doctor"]
    assert a != b
    assert first == second  # stale findings cannot be exposed by a scroll
    for bad in (True, -1, 62, "4"):
        with pytest.raises(ValueError):
            preview.build(report=current, doctor_offset=bad)
    out = tmp_path / "doctor"
    out.mkdir()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(current))
    assert preview.main(["--output-dir", str(out), "--status-json", str(source),
                         "--page", "doctor", "--doctor-offset", "3"]) == 0
    assert (out / "redux-doctor.png").is_file()
    assert json.loads(capsys.readouterr().out)["files"] == ["redux-doctor.png"]
    with pytest.raises(SystemExit) as error:
        preview.main(["--output-dir", str(tmp_path),
                      "--doctor-offset", "3"])
    assert error.value.code == 2
