"""BLE GATT looting — decode UUIDs, extract loot, gated live path."""
from redux.bt.gatt import (
    short_uuid, describe_uuid, Characteristic, Service, extract_loot,
    GattLooter, to_identify_signals,
)


def test_short_uuid_normalizes_sig_base():
    assert short_uuid("0000180a-0000-1000-8000-00805f9b34fb") == "180a"
    assert short_uuid("2A29") == "2a29"
    assert short_uuid("12345678-1234-5678-9abc-def012345678") == "12345678-1234-5678-9abc-def012345678"


def test_describe_known_and_vendor_uuids():
    assert describe_uuid("180a", kind="service") == "Device Information"
    assert describe_uuid("2a29") == "Manufacturer Name"
    assert "vendor-specific" in describe_uuid("abcd1234-0000-1000-8000-00805f9b34fb")


def _device_info_service():
    return Service(uuid="180a", characteristics=(
        Characteristic("2a29", ("read",), "Fitbit Inc."),
        Characteristic("2a24", ("read",), "Charge 5"),
        Characteristic("2a25", ("read",), "FB-SN-0099"),
        Characteristic("2a26", ("read",), "1.2.3"),
    ))


def test_extract_loot_pulls_device_info():
    loot = extract_loot("aa:bb:cc:dd:ee:ff", [
        Service("1800", (Characteristic("2a00", ("read",), "Charge 5"),)),
        _device_info_service(),
        Service("180f", (Characteristic("2a19", ("read", "notify"), "87"),)),
    ])
    assert loot.device_name == "Charge 5" and loot.manufacturer == "Fitbit Inc."
    assert loot.model == "Charge 5" and loot.serial == "FB-SN-0099"
    assert loot.firmware == "1.2.3" and loot.battery == "87"
    assert "looted" in loot.note


def test_extract_loot_flags_vendor_services():
    loot = extract_loot("aa:bb:cc:dd:ee:ff", [
        Service("fee0", ()),                                 # Anhui Huami (known)
        Service("adaf0100-c332-42a8-93bd-25e905756cb8", ()),  # custom
    ])
    assert any("756cb8" in c for c in loot.custom_services)


def test_loot_refuses_in_field_without_authorization():
    loot, reason = GattLooter().loot("aa:bb:cc:dd:ee:ff", enumerator=lambda m: [])
    assert loot is None and "refused" in reason


def test_loot_runs_in_lab_with_injected_enumerator():
    looter = GattLooter()
    loot, reason = looter.loot("aa:bb:cc:dd:ee:ff", lab=True,
                               enumerator=lambda m: [_device_info_service()])
    assert loot is not None and loot.manufacturer == "Fitbit Inc."


def test_loot_scope_gates_when_scope_supplied():
    class _Scope:
        def authorize(self, *, bssid=None, now=None):
            return (False, "not armed")
    loot, reason = GattLooter().loot("aa:bb:cc:dd:ee:ff", scope=_Scope(),
                                     enumerator=lambda m: [])
    assert loot is None and "refused" in reason


def test_gatt_loot_feeds_device_identification():
    from redux.intel.device_id import identify
    loot = extract_loot("aa:bb:cc:dd:ee:ff", [_device_info_service(),
                        Service("1800", (Characteristic("2a00", ("read",), "Charge 5-ABC"),))])
    g = identify(to_identify_signals(loot))
    assert g.category == "wearable" or "Fitbit" in (g.vendor + g.product)


def test_plan_is_glassbox():
    plan = GattLooter().plan("aa:bb:cc:dd:ee:ff")
    assert any("gatttool" in c and "--primary" in c for c in plan)


def _writable_services():
    return [
        Service("1800", (Characteristic("2a00", ("read",), "Light"),)),
        Service("fff0", (Characteristic("fff3", ("write", "write-without-response")),
                         Characteristic("fff4", ("read", "notify")))),
    ]


def test_writable_surface_finds_control_points():
    from redux.bt.gatt import writable_surface
    surface = writable_surface(_writable_services())
    uuids = {c["uuid"] for c in surface}
    assert uuids == {"fff3"}                             # only the writable char
    assert surface[0]["vendor_specific"]                 # fff0 is a vendor service


def test_write_refuses_without_per_action_confirm():
    from redux.bt.gatt import GattWriter
    ok, reason = GattWriter().write("aa:bb:cc:dd:ee:ff", "fff3", "01",
                                    lab=True, confirmed=False, writer=lambda *a: True)
    assert not ok and "confirm" in reason                # power-tier: confirm mandatory even in LAB


def test_write_refuses_in_field_without_authorization():
    from redux.bt.gatt import GattWriter
    ok, reason = GattWriter().write("aa:bb:cc:dd:ee:ff", "fff3", "01",
                                    confirmed=True, writer=lambda *a: True)
    assert not ok and "refused" in reason


def test_write_runs_when_lab_and_confirmed():
    from redux.bt.gatt import GattWriter
    calls = []
    ok, reason = GattWriter().write("aa:bb:cc:dd:ee:ff", "fff3", "01", lab=True,
                                    confirmed=True, writer=lambda m, u, v: calls.append((m, u, v)) or True)
    assert ok and calls == [("aa:bb:cc:dd:ee:ff", "fff3", "01")]


def test_write_scope_gates():
    from redux.bt.gatt import GattWriter
    class _Scope:
        def authorize(self, *, bssid=None, now=None):
            return (False, "not armed")
    ok, reason = GattWriter().write("aa:bb:cc:dd:ee:ff", "fff3", "01", scope=_Scope(),
                                    confirmed=True, writer=lambda *a: True)
    assert not ok and "refused" in reason
