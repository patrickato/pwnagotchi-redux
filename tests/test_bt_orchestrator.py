"""Bluetooth adapter orchestration: onboard carries PAN, a dongle unlocks BLE recon."""
from redux.connectivity.bt_orchestrator import (
    BtAdapter, BtRole, plan_bluetooth_roles, parse_adapters,
    select_pan_adapter, select_ble_scan_adapter)

HCI0 = BtAdapter("/org/bluez/hci0", address="AA:00", onboard=True)
HCI1 = BtAdapter("/org/bluez/hci1", address="BB:11", onboard=False)


def test_onboard_only_carries_pan_and_flags_ble_timeshare():
    a = plan_bluetooth_roles([HCI0])
    assert a.roles["/org/bluez/hci0"] is BtRole.PAN_MANAGEMENT
    assert any("time-share" in w for w in a.warnings)          # a dongle would help
    assert select_ble_scan_adapter(a) is None


def test_dongle_becomes_dedicated_ble_scan_alongside_pan():
    a = plan_bluetooth_roles([HCI0, HCI1])
    assert a.roles["/org/bluez/hci0"] is BtRole.PAN_MANAGEMENT   # onboard keeps the phone link
    assert a.roles["/org/bluez/hci1"] is BtRole.BLE_SCAN          # dongle does BLE recon
    assert a.warnings == []                                       # nothing time-shares now
    assert select_pan_adapter(a) == "/org/bluez/hci0"
    assert select_ble_scan_adapter(a) == "/org/bluez/hci1"


def test_powered_off_adapter_is_idle_and_pan_falls_to_a_powered_one():
    off_onboard = BtAdapter("/org/bluez/hci0", onboard=True, powered=False)
    a = plan_bluetooth_roles([off_onboard, HCI1])
    assert a.roles["/org/bluez/hci0"] is BtRole.IDLE
    assert a.roles["/org/bluez/hci1"] is BtRole.PAN_MANAGEMENT    # only powered one
    assert "powered off" in a.reasons["/org/bluez/hci0"]


def test_no_powered_adapter_warns_not_crashes():
    a = plan_bluetooth_roles([BtAdapter("/org/bluez/hci0", onboard=True, powered=False)])
    assert all(r is BtRole.IDLE for r in a.roles.values())
    assert any("no powered Bluetooth adapter" in w for w in a.warnings)


def test_want_ble_scan_false_leaves_the_dongle_idle():
    a = plan_bluetooth_roles([HCI0, HCI1], want_ble_scan=False)
    assert a.roles["/org/bluez/hci1"] is BtRole.IDLE


def test_three_adapters_one_scan_rest_spare():
    hci2 = BtAdapter("/org/bluez/hci2")
    a = plan_bluetooth_roles([HCI0, HCI1, hci2])
    roles = a.roles
    assert roles["/org/bluez/hci0"] is BtRole.PAN_MANAGEMENT
    assert sum(r is BtRole.BLE_SCAN for r in roles.values()) == 1
    assert roles["/org/bluez/hci2"] is BtRole.IDLE


def test_parse_adapters_reads_bluez_managed_objects():
    managed = {
        "/org/bluez/hci1": {"org.bluez.Adapter1": {"Address": "BB:11", "Powered": True}},
        "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": "AA:00", "Powered": True}},
        "/org/bluez/hci0/dev_xx": {"org.bluez.Device1": {}},   # not an adapter, ignored
        "/org/bluez": {"org.freedesktop.DBus.ObjectManager": {}},
    }
    adapters = parse_adapters(managed)
    assert [x.path for x in adapters] == ["/org/bluez/hci0", "/org/bluez/hci1"]
    assert adapters[0].onboard is True and adapters[1].onboard is False
    # end to end: parsed -> planned -> onboard PAN, dongle BLE
    a = plan_bluetooth_roles(adapters)
    assert a.roles["/org/bluez/hci0"] is BtRole.PAN_MANAGEMENT
    assert a.roles["/org/bluez/hci1"] is BtRole.BLE_SCAN
