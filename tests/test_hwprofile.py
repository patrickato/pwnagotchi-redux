"""Hardware auto-profiler — plug something in, learn what capability it unlocks."""
from redux.core.hwprofile import (
    UsbDevice, ProfileEntry, profile_usb, scan_usb, _KNOWN,
)
from redux.core import manifest as M


def test_known_device_maps_to_a_capability():
    devs = [UsbDevice("0bda", "2838", "RTL2838 DVB-T")]      # an RTL-SDR
    entries = profile_usb(devs)
    assert len(entries) == 1 and entries[0].known is True
    assert entries[0].feature == "sdr.rx"
    assert "unlocks" in entries[0].reason


def test_unknown_device_is_flagged_not_ignored():
    entries = profile_usb([UsbDevice("dead", "beef", "Mystery Gadget")])
    assert entries[0].known is False
    assert "not recognized" in entries[0].reason           # surfaced, never silent


def test_profile_resolves_live_manifest_state_when_injected():
    # HackRF present but its binary isn't -> the subghz feature's real readiness
    devs = [UsbDevice("1d50", "6089", "HackRF One")]
    entries = profile_usb(devs, manifest_lookup=M.get, readiness=M.readiness)
    e = entries[0]
    assert e.feature == "subghz.replay"
    assert e.state in {r.value for r in M.Readiness}        # a real readiness state
    # every mapped feature id actually exists in the manifest
    for (_vid, _pid), (_name, feature) in _KNOWN.items():
        assert M.get(feature) is not None, f"{feature} must be a real manifest feature"


def test_scan_usb_reads_sysfs_layout(tmp_path):
    root = tmp_path / "usb"
    for i, (vid, pid, prod) in enumerate([("0bda", "2838", "RTL2838"),
                                          ("8087", "0a2a", "Intel BT")]):
        d = root / f"1-{i}"
        d.mkdir(parents=True)
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        (d / "product").write_text(prod + "\n")
    # a non-device dir (no idVendor) is skipped, not fatal
    (root / "usb1").mkdir()
    devs = scan_usb(usb_root=root)
    ids = {d.id for d in devs}
    assert ids == {"0bda:2838", "8087:0a2a"}


def test_scan_usb_missing_root_is_empty_not_a_crash(tmp_path):
    assert scan_usb(usb_root=tmp_path / "nope") == []
