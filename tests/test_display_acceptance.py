"""Hardware-free display acceptance tests: real code, synthetic kernel events."""
import os
from pathlib import Path
import stat
import struct
import subprocess
from types import SimpleNamespace

import pytest

from redux.display import acceptance


def event(kind, code, value):
    return acceptance._FORMAT.pack(0, 0, kind, code, value)


def tap(x=110, y=220):
    return b"".join([
        event(1, 330, 1), event(3, 0, x), event(3, 1, y),
        event(0, 0, 0), event(1, 330, 0), event(0, 0, 0),
    ])


def test_inventory_keeps_framebuffer_and_event_identity_explicit(tmp_path):
    graphics = tmp_path / "graphics"
    graphics.mkdir()
    for name, size in (("fb0", "1920,1080"), ("fb1", "480,320")):
        node = graphics / name
        node.mkdir()
        (node / "name").write_text("graphics-driver")
        (node / "virtual_size").write_text(size)
    (graphics / "not-fb1").mkdir()
    inputs = tmp_path / "inputs"
    (inputs / "event4/device/capabilities").mkdir(parents=True)
    (inputs / "event4/device/name").write_text("ADS7846 Touchscreen")
    (inputs / "event4/device/capabilities/abs").write_text("3")
    listing = acceptance.inventory(graphics=graphics, inputs=inputs)
    assert [item["device"] for item in listing["framebuffers"]] == ["/dev/fb0", "/dev/fb1"]
    assert listing["input_devices"] == [{
        "device": "/dev/input/event4", "name": "ADS7846 Touchscreen",
        "abs_bits": "3", "key_bits": None, "properties": None,
    }]


def test_spi_inventory_exposes_controller_evidence_without_claiming_pixels(tmp_path):
    graphics = tmp_path / "graphics"
    graphics.mkdir()
    (graphics / "fb0").mkdir()
    (graphics / "fb0" / "name").write_text("vc4drmfb")
    (graphics / "fb0" / "virtual_size").write_text("1920,1080")
    inputs = tmp_path / "input"
    inputs.mkdir()
    spi = tmp_path / "spi"
    device = spi / "spi0.0"
    (device / "of_node").mkdir(parents=True)
    (device / "modalias").write_text("spi:ili9486\n")
    (device / "of_node" / "compatible").write_bytes(b"ilitek,ili9486\x00")
    driver = tmp_path / "drivers" / "fb_ili9486"
    driver.mkdir(parents=True)
    (device / "driver").symlink_to(driver, target_is_directory=True)
    (spi / "spi_bad").mkdir()
    item = acceptance.inventory(graphics=graphics, inputs=inputs, spi=spi)
    assert item["spi_devices"] == [{
        "sysfs_device": "spi0.0", "modalias": "spi:ili9486",
        "compatible": "ilitek,ili9486", "driver": "fb_ili9486",
    }]
    state = acceptance.report(graphics=graphics, inputs=inputs, spi=spi,
                              environ={}, uid=1000)
    assert state["spi_candidates"] == item["spi_devices"]
    assert state["framebuffer_candidates"][0]["virtual_size"] == "1920,1080"
    assert state["physical_display_verified"] is False
    assert state["touch_verified"] is False


def test_display_sysfs_reader_is_bounded_before_decode(tmp_path):
    file = tmp_path / "attribute"
    file.write_bytes(b"x" * 4096)
    assert acceptance._read(file, 128) == "x" * 128


def test_raw_single_touch_parser_across_partial_reads_and_normalization():
    decoder = acceptance.TapDecoder({"min": 10, "max": 210}, {"min": 20, "max": 420})
    data = tap(110, 220)
    for chunk in (data[:3], data[3:14], data[14:40], data[40:]):
        decoder.feed(chunk)
    assert decoder.taps == [{"raw_x": 110, "raw_y": 220,
                             "x_percent": 50.0, "y_percent": 50.0}]
    assert decoder.partial == b""
    assert not decoder.desynced


def test_touch_decoder_does_not_reuse_previous_contacts_coordinates():
    decoder = acceptance.TapDecoder()
    decoder.feed(tap(100, 200))
    assert len(decoder.taps) == 1
    # A press/release pair without any current-contact ABS readings must
    # never fabricate another tap from the previous contact's coordinates.
    decoder.feed(event(1, 330, 1) + event(1, 330, 0))
    assert len(decoder.taps) == 1
    # A half-sampled contact must not steal the old X coordinate either.
    decoder.feed(event(1, 330, 1) + event(3, 1, 300) + event(1, 330, 0))
    assert len(decoder.taps) == 1
    # Some controllers send initial coordinates immediately before BTN_TOUCH;
    # preserving that ordering still produces a legitimate second tap.
    decoder.feed(event(3, 0, 310) + event(3, 1, 410)
                 + event(1, 330, 1) + event(1, 330, 0))
    assert len(decoder.taps) == 2
    assert decoder.taps[-1]["raw_x"] == 310
    assert decoder.taps[-1]["raw_y"] == 410


def test_missing_axes_unsynced_events_and_stray_release_not_counted():
    decoder = acceptance.TapDecoder()
    decoder.feed(event(1, 330, 0))
    decoder.feed(event(1, 330, 1) + event(3, 0, 100) + event(1, 330, 0))
    assert decoder.taps == []
    decoder.feed(event(1, 330, 1) + event(0, 3, 0) + tap(10, 20))
    assert decoder.taps == []  # SYN_DROPPED discards until SYN_REPORT
    decoder.feed(tap(25, 30))
    assert decoder.taps[-1]["raw_x"] == 25
    assert decoder.dropped == 1


def test_parser_bounds_buffer_to_reject_corrupt_input():
    decoder = acceptance.TapDecoder()
    with pytest.raises(ValueError, match="buffer"):
        decoder.feed(b"x" * 16385)
    assert acceptance._percentage(-1, {"min": 0, "max": 100}) is None
    assert acceptance._percentage(30, None) is None


def test_abs_bounds_queries_linux_input_absinfo_and_rejects_invalid():
    calls = []
    def ioctl(fd, request, data, mutate):
        calls.append((fd, request, mutate))
        data[:] = struct.pack("=6i", 0, 100, 4100, 0, 0, 0)
    assert acceptance.abs_bounds(7, 0, ioctl=ioctl) == {"min": 100, "max": 4100}
    assert calls == [(7, 0x80184540, True)]
    def bad(_fd, _req, data, _mutate):
        data[:] = struct.pack("=6i", 0, 100, 100, 0, 0, 0)
    assert acceptance.abs_bounds(7, 1, ioctl=bad) is None


def test_opt_in_touch_event_reader_does_not_grab_or_write(monkeypatch):
    source = tap(25, 30)
    calls = []
    times = iter([0.0, 0.1, 0.2, 0.3, 0.4, 5.0])
    def open_readonly(device, flags):
        calls.append((device, flags))
        return os.open(os.devnull, os.O_RDONLY)
    def selector(readers, writers, exceptional, delay):
        assert not writers and not exceptional
        return (readers if source else [], [], [])
    def reader(_fd, _size):
        nonlocal source
        result, source = source, b""
        return result
    result = acceptance.sample_touch("/dev/input/event12", seconds=2,
        opener=open_readonly, reader=reader, selector=selector,
        clock=lambda: next(times), max_taps=1)
    assert result["status"] == "observed"
    assert len(result["taps"]) == 1
    assert result["physical_touch_verified"] is False
    assert calls[0][0] == "/dev/input/event12"
    assert calls[0][1] & os.O_RDONLY == 0  # O_RDONLY itself is 0
    assert calls[0][1] & os.O_NONBLOCK
    assert calls[0][1] & os.O_NOFOLLOW


@pytest.mark.parametrize("bad", ["event4", "../event4", "/dev/input/event1/../event2",
                                    "/dev/input/mouse0", "/dev/input/event1;echo"])
def test_touch_reader_rejects_untrusted_device_paths(bad):
    with pytest.raises(ValueError, match="explicit"):
        acceptance.sample_touch(bad)


def test_touch_reader_is_honest_on_missing_and_inaccessible_node():
    result = acceptance.sample_touch("/dev/input/event999", seconds=1)
    assert result["status"] == "error"
    assert not result["physical_touch_verified"]
    assert result["taps"] == []


def test_xinput_requires_local_display_and_bounded_authenticated_call():
    assert not acceptance.xinput_probe("remote:0")["ok"]
    assert not acceptance.xinput_probe(":0", which=lambda _: None)["ok"]
    calls = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "ADS7846 Touchscreen id=7\n", "")
    result = acceptance.xinput_probe(":0", which=lambda _: "/usr/bin/xinput", runner=runner)
    assert result["ok"] and "ADS7846" in result["devices"][0]
    assert calls[0][0] == ["/usr/bin/xinput", "--display", ":0", "list", "--short"]
    assert calls[0][1]["timeout"] == 3
    assert not acceptance.xinput_probe(":0", which=lambda _: "/usr/bin/xinput",
        runner=lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "auth failed"))["ok"]


def test_report_does_not_promote_x11_geometry_to_physical_acceptance(tmp_path, monkeypatch):
    graphics = tmp_path / "graphics"
    (graphics / "fb1").mkdir(parents=True)
    (graphics / "fb1/name").write_text("fb_ili9486")
    (graphics / "fb1/virtual_size").write_text("480,320")
    inputs = tmp_path / "input"
    inputs.mkdir()
    monkeypatch.setattr(acceptance.kiosk, "framebuffer_probe", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(acceptance.kiosk, "x11_probe", lambda display: {"ok": True})
    monkeypatch.setattr(acceptance.kiosk, "x11_geometry_probe",
                        lambda display: {"ok": True, "resolution": [480, 320]})
    monkeypatch.setattr(acceptance, "xinput_probe", lambda display: {"ok": True})
    snapshot = acceptance.report(framebuffer="/dev/fb1", graphics=graphics,
                                 inputs=inputs, environ={"DISPLAY": ":0"}, uid=1000)
    assert snapshot["selected_framebuffer"]["ok"]
    assert snapshot["x11_geometry"]["ok"]
    assert snapshot["physical_display_verified"] is False
    assert snapshot["touch_verified"] is False
    assert snapshot["hardware_acceptance"] == "pending_operator_physical_tests"


def test_root_session_does_not_attempt_untrusted_x11(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance.kiosk, "x11_probe", lambda display: pytest.fail("root X11"))
    snapshot = acceptance.report(graphics=tmp_path, inputs=tmp_path,
                                 environ={"DISPLAY": ":0"}, uid=0)
    assert snapshot["x11"] is None
    assert snapshot["touch_verified"] is False


def test_cli_validation_does_not_open_devices(monkeypatch):
    monkeypatch.setattr(acceptance, "report", lambda **kw: pytest.fail("unexpected probe"))
    with pytest.raises(SystemExit):
        acceptance.main(["--touch-device", "/dev/null"])
    with pytest.raises(SystemExit):
        acceptance.main(["--framebuffer", "fb1"])
    with pytest.raises(SystemExit):
        acceptance.main(["--seconds", "100"])
