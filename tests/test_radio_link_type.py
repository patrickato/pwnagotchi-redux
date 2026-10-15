"""Never infer native RF evidence from engine-running or monitor labels."""
import pytest

from redux.radio.link_type import link_assessment, read_link_type


@pytest.mark.parametrize("raw,expected", [
    ("1\n", 1), ("801\n", 801), ("803\n", 803),
    ("65535\n", 65535), ("0\n", 0),
    ("65536\n", None), ("-1\n", None), ("\n", None),
    ("monitor\n", None), ("1; echo unsafe\n", None),
])
def test_read_link_type_only_accepts_bounded_numeric_arphrd(tmp_path, raw, expected):
    root = tmp_path / "net"
    iface = root / "wlan0"
    iface.mkdir(parents=True)
    (iface / "type").write_text(raw)
    assert read_link_type("wlan0", sys_root=root) == expected


@pytest.mark.parametrize("name", [
    "../passwd", "wlan0/type", "wlan0;command", "wlan0\n", "", None,
    "not_valid_name_12345",
])
def test_refuse_non_interface_names_without_filesystem_access(tmp_path, name):
    assert read_link_type(name, sys_root=tmp_path / "missing") is None


def test_link_type_no_file_and_redirected_sysfs_file_are_unknown(tmp_path):
    root = tmp_path / "sys"
    (root / "wlan0").mkdir(parents=True)
    assert read_link_type("wlan0", sys_root=root) is None
    data = tmp_path / "private"
    data.write_text("803\n")
    (root / "wlan0" / "type").symlink_to(data)
    assert read_link_type("wlan0", sys_root=root) is None


def test_native_link_framing_is_only_prerequisite_not_received_packets():
    assert link_assessment(1) == "ethernet_link_type"
    for arphrd in (801, 803):
        assert link_assessment(arphrd) == "80211_link_type"
    assert link_assessment(42) == "other_link_type"
    assert link_assessment(True) == "unverified"
    assert link_assessment(None) == "unverified"


def test_live_runtime_snapshot_surfaces_observed_link_type_and_doctor(tmp_path, monkeypatch):
    import redux.core.live_runtime as live
    from redux.core.live_runtime import LiveConfig, LiveRuntime

    config = LiveConfig(state_dir=tmp_path / "state",
                        active_dir=tmp_path / "active",
                        capture_dir=tmp_path / "incoming", enable_web=False)
    runtime = LiveRuntime(config, radio_probe=lambda: [])
    try:
        # No RF operation: force only the read-only sysfs function's result.
        monkeypatch.setattr(live, "read_link_type", lambda _iface: 1)
        runtime.iface = "wlan0"
        runtime.state = "running"
        runtime._checkpoint()
        snap = runtime._snapshot
        assert snap["runtime"]["capture_link_type"] == 1
        assert snap["runtime"]["capture_link_assessment"] == "ethernet_link_type"
        finding = next(f for f in snap["doctor"]["findings"]
                       if f["area"] == "native RF link type")
        assert finding["status"] == "degraded"
        assert "Ethernet" in finding["summary"]
        assert snap["doctor"]["overall"] in ("degraded", "action")
    finally:
        runtime.close()
