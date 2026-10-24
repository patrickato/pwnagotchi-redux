"""Peripheral presence detection + capability-graph/Doctor wiring."""
from redux.core import peripherals as p
from redux.core import Augur
from redux.radio import Radio
from redux.core.capabilities import Cap


def _mkusb(tmp_path, vid, pid):
    root = tmp_path / "usb"
    root.mkdir()
    dev = root / "1-1"
    dev.mkdir()
    (dev / "idVendor").write_text(vid + "\n")
    (dev / "idProduct").write_text(pid + "\n")
    return root


def test_sdr_detected_by_usb_id(tmp_path):
    present, reason = p.sdr_present(usb_root=_mkusb(tmp_path, "0bda", "2838"))
    assert present and "RTL-SDR" in reason


def test_sdr_absent_for_other_usb_and_missing_tree(tmp_path):
    assert p.sdr_present(usb_root=_mkusb(tmp_path, "1234", "5678"))[0] is False
    assert p.sdr_present(usb_root=tmp_path / "nope")[0] is False


def test_bluetooth_presence(tmp_path):
    bt = tmp_path / "bt"; bt.mkdir(); (bt / "hci0").mkdir()
    assert p.bluetooth_present(bt_class=bt)[0] is True
    empty = tmp_path / "bt2"; empty.mkdir()
    assert p.bluetooth_present(bt_class=empty)[0] is False


def test_gps_presence():
    assert p.gps_present(env={"REDUX_GPS": "/dev/ttyUSB0"}, exists=lambda x: True)[0] is True
    assert p.gps_present(env={}, exists=lambda x: True)[0] is False
    assert p.gps_present(env={"REDUX_GPS": "/dev/x"}, exists=lambda x: False)[0] is False


def test_ups_presence(tmp_path):
    bus = tmp_path / "i2c-1"; bus.write_text("")
    ok = p.ups_present(i2c_bus=bus, probe=lambda addr: 0x399f)
    assert ok[0] and "INA219" in ok[1]

    def boom(addr):
        raise OSError("no ack")
    assert p.ups_present(i2c_bus=bus, probe=boom)[0] is False          # no chip answers
    assert p.ups_present(i2c_bus=bus, probe=None)[0] is False          # bus but no reader
    assert p.ups_present(i2c_bus=tmp_path / "noi2c", probe=lambda a: 1)[0] is False  # no bus


def test_storage_presence():
    assert p.storage_present(mounted=True)[0] is True
    assert p.storage_present(mounted=False)[0] is False
    assert p.storage_present(mounted=None)[0] is False   # not probed -> honest absent


def test_capability_graph_and_doctor_register_peripherals():
    probe = lambda: {"sdr": (True, "RTL-SDR 0bda:2838"), "bluetooth": (False, "no adapter"),
                     "ups": (True, "INA219 at 0x42"), "storage": (True, "/captures mounted")}
    a = Augur([Radio("wlan1", bands=frozenset({"2.4"}), monitor=True, inject=True)],
              peripheral_probe=probe)
    g = a.capability_graph()
    assert g.explain(Cap.SDR_RX)["available"] is True
    assert g.explain(Cap.BLE_SCAN)["available"] is False
    assert g.explain(Cap.POWER_BATTERY)["available"] is True
    assert g.explain(Cap.STORAGE_PERSIST)["available"] is True

    areas = {f["area"]: f for f in a.doctor_report()["findings"]}
    assert areas["SDR"]["status"] == "ok"
    assert areas["bluetooth"]["status"] == "attention"      # absent, not required -> hint, not crash
    assert "Bluetooth adapter" in areas["bluetooth"]["remediation"] or "adapter" in areas["bluetooth"]["remediation"]
    assert areas["UPS/battery"]["status"] == "ok"
    assert areas["persistent storage"]["status"] == "ok"


def test_doctor_reports_absent_peripherals_without_a_graph_gap_hole():
    # No peripheral probe -> every peripheral reads absent WITH a reason (never invisible).
    a = Augur([Radio("wlan1", bands=frozenset({"2.4"}), monitor=True, inject=True)])
    areas = {f["area"]: f for f in a.doctor_report()["findings"]}
    for area in ("SDR", "bluetooth", "UPS/battery"):
        assert areas[area]["status"] == "attention"
    assert areas["persistent storage"]["status"] == "action"   # storage is required
