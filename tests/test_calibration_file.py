"""Calibration file and framebuffer launch safety regressions (no hardware)."""
import json

import pytest

from redux.display import calibration_file, fbui


def valid_points():
    return [
        {"raw_x": 100+x*3500, "raw_y": 200+y*3500,
         "pixel_x": x*479, "pixel_y": y*319}
        for x,y in [(0,0),(1,0),(0,1),(1,1),(.5,.5)]
    ]


def test_valid_calibration_is_evaluated(tmp_path):
    f = tmp_path / "touch.json"
    f.write_text(json.dumps({"schema":1,"points":valid_points()}))
    result = calibration_file.load_calibration(str(f))
    assert result.width == 480 and result.height == 320
    assert result.maximum_error_px == 0


@pytest.mark.parametrize("payload", [
    {"schema": 2, "points": valid_points()},
    {"schema": 1, "points": valid_points()[:4]},
    {"schema": 1, "points": valid_points(), "trusted": True},
    {"schema": 1, "points": [dict(p, raw_x=None) if i==0 else p
                              for i,p in enumerate(valid_points())]},
])
def test_invalid_samples_fail_closed(tmp_path, payload):
    f = tmp_path / "touch.json"
    f.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        calibration_file.load_calibration(str(f))


def test_symlinks_and_oversized_files_refused(tmp_path):
    f = tmp_path / "real.json"
    f.write_text("X"*9000)
    with pytest.raises(ValueError):
        calibration_file.load_calibration(str(f))
    link = tmp_path / "link.json"
    link.symlink_to(f)
    with pytest.raises(OSError):
        calibration_file.load_calibration(str(link))


@pytest.mark.parametrize("args", [
    ["--calibration-file", "/tmp/touch.json", "--run"],
    ["--calibration-file", "/tmp/touch.json", "--touch-device", "/dev/input/event4", "--run"],
    ["--calibration-file", "/tmp/touch.json", "--touch-device", "/dev/input/event4",
     "--confirm-touch-calibration", "--check"],
    ["--confirm-touch-calibration", "--run"],
    ["--calibration-file", "/tmp/touch.json", "--touch-device", "/dev/input/event4",
     "--confirm-touch-calibration", "--swipe-reverse", "--run"],
])
def test_invalid_calibration_invocations_never_open_hardware(monkeypatch, args):
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a, **_k: pytest.fail("hardware probe"))
    with pytest.raises(SystemExit) as exc:
        fbui.main(["--framebuffer", "/dev/fb1"] + args)
    assert exc.value.code == 2


def test_invalid_calibration_prevents_touch_open_and_frame_writes(tmp_path, monkeypatch):
    f = tmp_path / "bad.json"
    f.write_text('{"schema":1,"points":[]}')
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a, **_k: {"ok":True,"stride":960})
    from redux.display import touch_nav
    monkeypatch.setattr(touch_nav, "open_touch",
                        lambda *_a, **_k: pytest.fail("touch opened"))
    monkeypatch.setattr(fbui, "write_frame",
                        lambda *_a, **_k: pytest.fail("frame painted"))
    assert fbui.main(["--framebuffer", "/dev/fb1", "--run",
                      "--touch-device", "/dev/input/event4",
                      "--calibration-file", str(f),
                      "--confirm-touch-calibration"]) == 2
