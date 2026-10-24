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


def test_render_draws_mode_badge_without_error():
    assert fbui.render(valid_report(), now=101, mode="lab").size == fbui.RESOLUTION
    assert fbui.render(valid_report(), now=101, mode="field",
                       mode_arming=True).size == fbui.RESOLUTION
    assert fbui.render(valid_report(), now=101).size == fbui.RESOLUTION   # no mode -> no badge


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


def test_native_tft_rejects_unbounded_counters_and_nonstring_labels():
    report = valid_report(now=100)
    overflow = 10 ** 5000
    report["runtime"].update({
        "events_seen": overflow, "handed_off": overflow, "free_bytes": overflow,
        "iface": overflow,
    })
    report["capture_processing"]["by_status"] = {
        "ready": overflow, "error": overflow,
    }
    report["doctor"]["findings"] = [{
        "status": "attention",
        "summary": overflow,
        "remediation": {"nested": "not a display label"},
    }]
    view = fbui.status_view(report, now=101)
    assert view["events"] is None
    assert view["handoffs"] is None
    assert view["storage_mb"] is None
    assert view["processed"] is None and view["errors"] is None
    assert view["iface"] == "UNKNOWN"
    assert view["finding"] == "UNKNOWN"
    assert view["detail"] == "UNKNOWN"
    assert fbui.render(report, now=101).size == (480, 320)


def test_native_tft_ignores_negative_boolean_and_fractional_metrics():
    report = valid_report(now=100)
    report["runtime"].update({
        "events_seen": True, "handed_off": -1, "free_bytes": 1.5,
    })
    report["capture_processing"]["by_status"] = {
        "ready": True, "error": -2,
    }
    view = fbui.status_view(report, now=101)
    assert all(view[key] is None for key in (
        "events", "handoffs", "storage_mb", "processed", "errors",
    ))


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
    assert "802.11 803 / RX UNVERIFIED" == observed["rf_label"]
    frame["runtime"]["capture_link_type"] = None
    assert fbui.status_view(frame, now=101)["rf_label"] == "RF UNVERIFIED"
    frame["runtime"]["capture_link_type"] = True
    assert fbui.status_view(frame, now=101)["rf_label"] == "RF UNVERIFIED"


@pytest.mark.parametrize(("state", "link", "error", "minimum_health"), [
    ("running", 1, "", "degraded"),
    ("degraded", None, "", "degraded"),
    ("storage_paused", None, "", "action"),
    ("starting", None, "", "attention"),
    ("rotating", None, "", "attention"),
    ("stopped", None, "", "attention"),
    ("running", None, "current engine failure", "degraded"),
])
def test_tft_measured_runtime_faults_cannot_be_overruled_by_green_doctor(
        state, link, error, minimum_health):
    report = valid_report(now=100)
    report["runtime"].update(state=state, capture_link_type=link, last_error=error)
    report["doctor"]["overall"] = "ok"
    current = fbui.status_view(report, now=101)
    rank = {"unknown": -1, "ok": 0, "attention": 1, "degraded": 2, "action": 3}
    assert rank[current["health"]] >= rank[minimum_health]
    assert fbui.render(report, now=101).size == (480, 320)


def test_tft_does_not_claim_reception_from_link_type_or_generic_events():
    report = valid_report(now=100)
    report["runtime"].update(
        capture_link_type=803, events_seen=10000, handed_off=0,
    )
    view = fbui.status_view(report, now=101)
    assert view["rf_link_type"] == 803
    assert view["rf_label"] == "802.11 803 / RX UNVERIFIED"
    assert view["events"] == 10000
    assert "RX UNVERIFIED" in view["rf_label"]
    # Doctor can be OK for a measured header type; actual RF receive is
    # still an independent hardware-acceptance gate.
    assert view["health"] == "ok"


def test_tft_stale_report_does_not_display_old_measurement_as_current_fault():
    report = valid_report(now=100)
    report["runtime"].update(capture_link_type=1, last_error="historical failure")
    report["doctor"]["overall"] = "ok"
    view = fbui.status_view(report, now=131)
    assert view["health"] == "unknown"
    assert view["rf_label"] == "RF NOT CURRENT"
    assert view["engine"] == "STALE"


def test_tft_failure_streak_is_not_ignored_on_a_running_engine():
    report = valid_report(now=100)
    report["runtime"]["failure_streak"] = 3
    assert fbui.status_view(report, now=101)["health"] == "degraded"
    report["runtime"]["failure_streak"] = 0
    assert fbui.status_view(report, now=101)["health"] == "ok"


def test_native_tft_never_displays_historical_rf_as_current():
    report = valid_report(now=100)
    report["runtime"]["capture_link_type"] = 803
    fresh = fbui.status_view(report, now=101)
    assert fresh["rf_label"] == "802.11 803 / RX UNVERIFIED"
    assert fresh["rf_link_type"] == 803
    expired = fbui.status_view(report, now=130)
    assert expired["rf_label"] == "RF NOT CURRENT"
    assert expired["rf_link_type"] is None
    assert expired["engine"] == "STALE"
    # Ethernet framing is also a historical observation, not a current one.
    report["runtime"]["capture_link_type"] = 1
    assert fbui.status_view(report, now=130)["rf_label"] == "RF NOT CURRENT"


@pytest.mark.parametrize("link", [-1, 65536, 10 ** 1000, "803", 803.0, True, None])
def test_native_tft_bounds_bad_rf_link_values_and_cannot_crash(link):
    report = valid_report(now=100)
    report["runtime"]["capture_link_type"] = link
    status = fbui.status_view(report, now=101)
    assert status["rf_label"] == "RF UNVERIFIED"
    assert status["rf_link_type"] is None
    assert fbui.render(report, now=101).size == (480, 320)


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


def test_overview_runtime_diagnostics_are_current_bounded_and_visible(monkeypatch):
    import PIL.ImageDraw as image_draw
    report = valid_report(now=100)
    report["runtime"].update(
        retry_in_seconds=12.6, failure_streak=2, engine_attempts=9,
        capture_mount=False)
    view = fbui.status_view(report, now=101)
    assert view["retry_seconds"] == 13
    assert view["failure_streak"] == 2
    assert view["engine_attempts"] == 9
    assert view["capture_mount"] is False
    stale = fbui.status_view(report, now=130)
    assert stale["retry_seconds"] is None
    assert stale["failure_streak"] is None
    assert stale["engine_attempts"] is None
    assert stale["capture_mount"] is None

    labels = []
    real_draw = image_draw.Draw
    class Recorder:
        def __init__(self, image):
            self.inner = real_draw(image)
        def text(self, xy, label, *args, **kwargs):
            labels.append(label)
            return self.inner.text(xy, label, *args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.inner, name)
    monkeypatch.setattr(image_draw, "Draw", Recorder)
    image = fbui.render(report, now=101)
    assert image.size == (480, 320)
    assert "RETRY 13s / FAILURES 2 / STARTS 9 / REDUXCAP MISSING" in labels


@pytest.mark.parametrize("bad", [True, -1, float("nan"), 10**1000, "12", 86401])
def test_overview_retry_duration_rejects_malformed_values(bad):
    report = valid_report(now=100)
    report["runtime"]["retry_in_seconds"] = bad
    assert fbui.status_view(report, now=101)["retry_seconds"] is None


# --- touch node auto-resolution (opt-in by controller name, never a guess) ----

def _inv(*names):
    return {"input_devices": [
        {"device": f"/dev/input/event{i}", "name": n}
        for i, n in enumerate(names)]}


def test_resolve_touch_device_picks_single_controller_by_name():
    dev, reason = fbui.resolve_touch_device(
        inventory=_inv("gpio-keys", "ADS7846 Touchscreen"))
    assert dev == "/dev/input/event1"
    assert "controller name" in reason


def test_resolve_touch_device_refuses_zero_or_ambiguous():
    none0, reason0 = fbui.resolve_touch_device(inventory=_inv("gpio-keys"))
    assert none0 is None and "no XPT2046" in reason0
    none2, reason2 = fbui.resolve_touch_device(
        inventory=_inv("ADS7846 Touchscreen", "xpt2046 ts"))
    assert none2 is None and "multiple" in reason2


def test_resolve_touch_device_ignores_malformed_rows():
    dev, _ = fbui.resolve_touch_device(
        inventory={"input_devices": [{"device": "nope", "name": "xpt2046"},
                                     {"name": "xpt2046"},  # no device key
                                     "not-a-dict"]})
    assert dev is None


def test_touch_auto_requires_run_and_excludes_explicit_device():
    with pytest.raises(SystemExit):
        fbui.main(["--framebuffer", "/dev/fb1", "--check", "--touch-auto"])
    with pytest.raises(SystemExit):
        fbui.main(["--framebuffer", "/dev/fb1", "--once", "--touch-auto"])
    with pytest.raises(SystemExit):
        fbui.main(["--framebuffer", "/dev/fb1", "--run", "--touch-auto",
                   "--touch-device", "/dev/input/event1"])


def test_touch_auto_miss_runs_display_without_opening_touch(monkeypatch):
    from redux.display import touch_nav
    monkeypatch.setattr(fbui, "resolve_touch_device",
                        lambda **_k: (None, "no XPT2046/ADS7846 touch node present"))
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: valid_report(now=time.time()))
    monkeypatch.setattr(touch_nav, "open_touch",
                        lambda *_a, **_k: pytest.fail("auto-miss must not open any touch node"))
    writes = []
    def writer(_device, _frame):
        writes.append(1)
        raise RuntimeError("synthetic stop")
    monkeypatch.setattr(fbui, "write_frame", writer)
    # A run with --touch-auto and no touch node present still paints, never reads touch.
    with pytest.raises(RuntimeError, match="synthetic stop"):
        fbui.main(["--framebuffer", "/dev/fb1", "--run", "--touch-auto"])
    assert writes == [1]


def test_touch_auto_hit_opens_resolved_node(monkeypatch):
    from redux.display import touch_nav
    monkeypatch.setattr(fbui, "resolve_touch_device",
                        lambda **_k: ("/dev/input/event7", "resolved by controller name"))
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda *_a: {"ok": True, "stride": 960})
    monkeypatch.setattr(fbui, "read_status", lambda *_a: valid_report(now=time.time()))
    opened = []
    monkeypatch.setattr(touch_nav, "open_touch", lambda dev: opened.append(dev) or 55)
    monkeypatch.setattr(touch_nav, "touch_identity", lambda _fd: (b"id", b"phys"))
    monkeypatch.setattr(fbui.select, "select", lambda *_a: ([], [], []))
    writes = []
    def writer(_device, _frame):
        writes.append(1)
        raise RuntimeError("synthetic stop")
    monkeypatch.setattr(fbui, "write_frame", writer)
    monkeypatch.setattr(fbui.os, "close", lambda *_a: None)
    with pytest.raises(RuntimeError, match="synthetic stop"):
        fbui.main(["--framebuffer", "/dev/fb1", "--run", "--touch-auto"])
    # The node chosen by name — not guessed — is the one opened.
    assert opened == ["/dev/input/event7"]
