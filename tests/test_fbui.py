"""Native TFT render and safety contract, without touching a real display."""
import json
from pathlib import Path
from types import SimpleNamespace
import stat
import time

import pytest
from PIL import Image
from redux.display import fbui


def sysfs(tmp_path, *, size="480,320", bpp="16", stride="960"):
    node = tmp_path / "fb1"
    node.mkdir()
    for key, value in {"name": "fb_ili9486", "virtual_size": size,
                       "bits_per_pixel": bpp, "stride": stride}.items():
        (node / key).write_text(value)
    return tmp_path


def char_device(_path):
    return SimpleNamespace(st_mode=stat.S_IFCHR | 0o600)


def valid_report(now=100):
    return {
        "runtime": {
            "state": "running", "iface": "wlan0",
            "events_seen": 12, "handed_off": 3,
            "free_bytes": 67108864, "updated_utc": now,
        },
        "doctor": {"overall": "ok", "findings": []},
        "capture_processing": {
            "available": True, "by_status": {"ready": 2, "error": 0},
        },
    }


def test_fb_preflight_requires_explicit_device_and_measured_geometry(tmp_path):
    sysfs(tmp_path)
    accepted = fbui.framebuffer_info(
        "/dev/fb1", sys_root=tmp_path, statter=char_device,
    )
    assert accepted["ok"] is True
    assert accepted["name"] == "fb_ili9486"
    assert (accepted["width"], accepted["height"], accepted["stride"]) == (480, 320, 960)
    for bad in ("/dev/fb0;sh", "/dev/null", "fb1", "/tmp/fb1", "/dev/fb111"):
        assert not fbui.framebuffer_info(
            bad, sys_root=tmp_path, statter=char_device)["ok"]


@pytest.mark.parametrize(("size","bpp","stride"), [
    ("320,480", "16", "640"), ("480,320", "32", "1920"),
    ("480,320", "16", "512"), ("480,320", "16", "16384"),
])
def test_fb_preflight_refuses_incompatible_hardware(tmp_path, size, bpp, stride):
    sysfs(tmp_path, size=size, bpp=bpp, stride=stride)
    assert not fbui.framebuffer_info(
        "/dev/fb1", sys_root=tmp_path, statter=char_device)["ok"]


def test_status_truth_and_staleness():
    current = fbui.status_view(valid_report(), now=101)
    assert current["health"] == "ok" and current["engine"] == "ACTIVE"
    assert current["events"] == 12 and current["handoffs"] == 3
    assert current["storage_mb"] == 64 and current["processed"] == 2
    stale = fbui.status_view(valid_report(), now=130)
    assert stale["health"] == "unknown" and stale["engine"] == "STALE"
    assert stale["stale"] is True
    offline = fbui.status_view(None, now=130)
    assert offline["state"] == "OFFLINE"
    assert offline["events"] is None and offline["storage_mb"] is None


@pytest.mark.parametrize("updated", [
    float("nan"), float("inf"), float("-inf"), True, "101",
    None, {}, -1, 10 ** 1000,
])
def test_tft_never_calls_invalid_runtime_timestamp_live(updated):
    state = valid_report(now=101)
    state["runtime"]["updated_utc"] = updated
    view = fbui.status_view(state, now=101)
    assert view["health"] == "unknown"
    assert view["state"] == "STALE"
    assert view["engine"] != "ACTIVE"
    assert view["stale"] is True


@pytest.mark.parametrize("now", [
    float("nan"), float("inf"), float("-inf"), -1, True, "101", 10 ** 1000,
])
def test_tft_invalid_local_clock_never_fakes_live_reading(now):
    view = fbui.status_view(valid_report(now=101), now=now)
    assert view["health"] == "unknown"
    assert view["engine"] != "ACTIVE"
    assert view["stale"] is True


@pytest.mark.parametrize("runtime", [None, [], "running", True, 42])
def test_tft_malformed_runtime_is_unknown_not_an_exception(runtime):
    frame = valid_report(now=101)
    frame["runtime"] = runtime
    assert fbui.status_view(frame, now=101)["engine"] != "ACTIVE"
    assert fbui.status_view(frame, now=101)["health"] == "unknown"
    assert fbui.render(frame, now=101).size == (480, 320)


def test_doctor_live_findings_and_missing_pipeline_data():
    status = valid_report()
    status["doctor"] = {
        "overall": "action", "findings": [{
            "status": "action", "summary": "Capture storage missing",
            "remediation": "Restore REDUXCAP before restarting",
        }],
    }
    status["capture_processing"] = {"available": False}
    data = fbui.status_view(status, now=101)
    assert data["health"] == "action"
    assert "Capture storage missing" in data["finding"]
    assert "Restore REDUXCAP" in data["detail"]
    assert data["pipeline_available"] is False
    assert data["errors"] is None


def test_native_tft_shows_rf_link_type_not_just_running_engine():
    frame = valid_report()
    frame["runtime"]["capture_link_type"] = 1
    current = fbui.status_view(frame, now=101)
    assert current["engine"] == "ACTIVE"
    assert current["rf_link_type"] == 1
    assert current["rf_label"] == "ETHERNET 1 / NO NATIVE RF"
    assert fbui.render(frame, now=101).size == (480, 320)
    frame["runtime"]["capture_link_type"] = 803
    observed = fbui.status_view(frame, now=101)
    assert "802.11 LINK 803" == observed["rf_label"]
    frame["runtime"]["capture_link_type"] = None
    assert fbui.status_view(frame, now=101)["rf_label"] == "RF UNVERIFIED"
    frame["runtime"]["capture_link_type"] = True
    assert fbui.status_view(frame, now=101)["rf_label"] == "RF UNVERIFIED"


def test_native_tft_renders_all_pages_from_real_report():
    for page in ("overview", "captures", "doctor"):
        image = fbui.render(valid_report(), page=page, now=101)
        assert image.size == (480, 320)
        assert image.mode == "RGB"
        assert image.getpixel((30, 15)) != image.getpixel((480 - 1, 319))
    stale = fbui.render(None, now=130)
    assert stale.size == (480, 320)
    with pytest.raises(ValueError):
        fbui.render(None, page="made_up", now=130)


def test_565_packing_and_padding_no_touching_other_rows():
    image = Image.new("RGB", (480, 320), (255, 0, 0))
    front = fbui.rgb565_bytes(image, stride=964, order="rgb565")
    back = fbui.rgb565_bytes(image, stride=964, order="bgr565")
    assert len(front) == len(back) == 964 * 320
    assert front[:2] == b"\x00\xf8"
    assert back[:2] == b"\x1f\x00"
    assert front[960:964] == b"\x00" * 4
    assert front[964:966] == b"\x00\xf8"
    with pytest.raises(ValueError):
        fbui.rgb565_bytes(image, stride=100)


def test_writer_only_opens_explicit_character_device_and_handles_short_writes():
    files = []
    sent = []
    closed = []
    def open_dev(name, flags):
        files.append((name, flags))
        return 17
    def write(fd, data):
        assert fd == 17
        sent.append(data[:3])
        return min(3, len(data))
    fbui.write_frame(
        "/dev/fb1", b"abcdefg", opener=open_dev, writer=write,
        checker=lambda _: char_device("ignored"), closer=closed.append,
    )
    assert files[0][0] == "/dev/fb1"
    assert b"".join(sent) == b"abcdefg"
    assert closed == [17]
    with pytest.raises(ValueError):
        fbui.write_frame("/tmp/fb1", b"x", opener=open_dev)
    assert len(files) == 1


def test_http_reader_only_probes_loopback_and_requires_bounded_redux_json():
    requests = []
    class Response:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit):
            assert limit == 65537
            return self.body[:limit]
    def opener(url, timeout):
        requests.append((url, timeout))
        return Response(json.dumps(valid_report()).encode())
    assert fbui.read_status(8090, opener=opener)["doctor"]["overall"] == "ok"
    assert requests == [("http://127.0.0.1:8090/api/status", 2)]
    assert fbui.read_status(8090, opener=lambda *args, **kwargs: Response(b"[]")) is None
    assert fbui.read_status(8090, opener=lambda *args, **kwargs: Response(b"x"*65537)) is None
    with pytest.raises(ValueError):
        fbui.read_status(0)


def test_cli_check_wont_paint_when_device_missing(monkeypatch, capsys):
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda _dev: {"ok": False, "reason": "missing driver"})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: None)
    monkeypatch.setattr(fbui, "write_frame", lambda *_a, **_k: pytest.fail("unexpected write"))
    assert fbui.main(["--framebuffer", "/dev/fb1", "--check"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["physical_display_verified"] is False
    assert result["touch_verified"] is False
    assert fbui.main(["--framebuffer", "/dev/fb1", "--once"]) == 2


def test_cli_once_writes_exact_frame_and_never_autoruns(monkeypatch):
    written = []
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda _dev: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: valid_report(now=time.time()))
    monkeypatch.setattr(fbui, "write_frame", lambda dev, data:
                        written.append((dev, data)))
    assert fbui.main(["--framebuffer", "/dev/fb1", "--once"]) == 0
    assert len(written) == 1
    assert written[0][0] == "/dev/fb1"
    assert len(written[0][1]) == 960 * 320
