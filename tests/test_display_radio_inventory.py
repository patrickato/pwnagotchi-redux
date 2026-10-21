"""Live radio inventory is read-only, identity-pinned and tested with fake sysfs."""
import pytest

from redux.radio import display_inventory as di
from redux.radio.local_evidence import Unavailable


def add_net(root, *, iface, ifindex, phy, transport, driver, arphrd):
    root = root.resolve()
    net = root / iface
    net.mkdir(parents=True)
    phys = root.parent / "phys" / phy
    phys.mkdir(parents=True, exist_ok=True)
    (net / "phy80211").symlink_to(phys)
    device = root.parent / "devices" / (transport + "-" + phy)
    device.mkdir(parents=True, exist_ok=True)
    subsys = root.parent / "subsystems" / transport
    subsys.mkdir(parents=True, exist_ok=True)
    subsystem_link = device / "subsystem"
    if not subsystem_link.exists() and not subsystem_link.is_symlink():
        subsystem_link.symlink_to(subsys)
    drivers = root.parent / "drivers" / driver
    drivers.mkdir(parents=True, exist_ok=True)
    driver_link = device / "driver"
    if not driver_link.exists() and not driver_link.is_symlink():
        driver_link.symlink_to(drivers)
    (net / "device").symlink_to(device)
    (net / "ifindex").write_text(str(ifindex))
    (net / "type").write_text(str(arphrd))
    (net / "flags").write_text("0x1003")
    return {"iface": iface, "ifindex": ifindex, "phy": phy, "type": "monitor" if arphrd in (801, 803) else "managed"}


def iw_text(*rows):
    chunks = []
    for row in rows:
        chunks.append(
            row["phy"].replace("phy", "phy#") + "\n"
            + " Interface " + row["iface"] + "\n"
            + "  ifindex " + str(row["ifindex"]) + "\n"
            + "  type " + row["type"] + "\n")
    return "".join(chunks)


def test_collect_reports_stable_onboard_and_usb_radios_without_rx_claims(tmp_path):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    onboard = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                      transport="sdio", driver="brcmfmac", arphrd=1)
    usb = add_net(root, iface="wlan1", ifindex=7, phy="phy1",
                  transport="usb", driver="mt76x2u", arphrd=803)
    result = di.collect(sys_root=root, run=lambda args: iw_text(onboard, usb),
                        wall_clock=lambda: 1000.0)
    assert result["schema"] == 1 and result["sampled_utc"] == 1000.0
    assert result["radios"] == [
        {"transport": "onboard", "identity_stable": True, "iface": "wlan0",
         "driver": "brcmfmac", "kernel_mode": "managed", "arphrd": 1},
        {"transport": "usb", "identity_stable": True, "iface": "wlan1",
         "driver": "mt76x2u", "kernel_mode": "monitor", "arphrd": 803},
    ]
    assert "decoded_reception" not in result["radios"][1]
    assert "owner_verified" not in result["radios"][1]

    from redux.display import fbui, radio_panel
    report = {"runtime": {"state": "running", "iface": "wlan0",
                          "capture_link_type": 1, "updated_utc": 1000.0},
              "doctor": {"overall": "ok", "findings": []},
              "radio_inventory": result}
    model = radio_panel.model(report, now=1001.0)
    assert model["inventory"] == "CURRENT"
    assert model["onboard"]["iface"] == "wlan0"
    assert model["usb"]["iface"] == "wlan1"
    assert model["onboard"]["reception"] == model["usb"]["reception"] == "UNVERIFIED"
    assert fbui.render(report, page="radios", now=1001.0).size == (480, 320)


def test_same_physical_radio_collapses_to_current_monitor_vif(tmp_path):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    managed = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                      transport="sdio", driver="brcmfmac", arphrd=1)
    monitor = add_net(root, iface="wlan0mon", ifindex=9, phy="phy0",
                      transport="sdio", driver="brcmfmac", arphrd=803)
    result = di.collect(sys_root=root, run=lambda args: iw_text(managed, monitor),
                        wall_clock=lambda: 1000.0)
    assert len(result["radios"]) == 1
    assert result["radios"][0]["iface"] == "wlan0mon"
    assert result["radios"][0]["kernel_mode"] == "monitor"
    assert result["radios"][0]["arphrd"] == 803


def test_unknown_transport_duplicate_usb_and_missing_iw_identity_fail_closed(tmp_path):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    unknown = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                      transport="platform", driver="brcmfmac", arphrd=1)
    with pytest.raises(Unavailable, match="transport_unknown"):
        di.collect(sys_root=root, run=lambda args: iw_text(unknown),
                   wall_clock=lambda: 1000.0)

    root2 = tmp_path / "other" / "sys" / "class" / "net"
    root2.mkdir(parents=True)
    first = add_net(root2, iface="wlan1", ifindex=7, phy="phy1",
                    transport="usb", driver="mt76x2u", arphrd=803)
    second = add_net(root2, iface="wlan2", ifindex=8, phy="phy2",
                     transport="usb", driver="mt76x2u", arphrd=803)
    with pytest.raises(Unavailable, match="multiple_physical_radios_same_transport"):
        di.collect(sys_root=root2, run=lambda args: iw_text(first, second),
                   wall_clock=lambda: 1000.0)
    with pytest.raises(Unavailable, match="radio_identity_changed"):
        di.collect(sys_root=root2, run=lambda args: "",
                   wall_clock=lambda: 1000.0)


def test_driver_rebind_during_sample_is_rejected(tmp_path):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    row = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                  transport="sdio", driver="brcmfmac", arphrd=1)
    driver_link = root / "wlan0" / "device" / "driver"
    replacement = tmp_path / "drivers" / "replacement"
    replacement.mkdir(parents=True)
    def rebind(_args):
        driver_link.unlink()
        driver_link.symlink_to(replacement)
        return iw_text(row)
    with pytest.raises(Unavailable, match="radio_driver_changed"):
        di.collect(sys_root=root, run=rebind, wall_clock=lambda: 1000.0)


def test_physical_adapter_replacement_during_sample_is_rejected(tmp_path):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    row = add_net(root, iface="wlan1", ifindex=7, phy="phy1",
                  transport="usb", driver="mt76x2u", arphrd=803)
    device_link = root / "wlan1" / "device"
    replacement = tmp_path / "sys" / "class" / "devices" / "usb-replacement"
    replacement.mkdir(parents=True)
    subsystem = tmp_path / "sys" / "class" / "subsystems" / "usb"
    driver = tmp_path / "sys" / "class" / "drivers" / "mt76x2u"
    (replacement / "subsystem").symlink_to(subsystem)
    (replacement / "driver").symlink_to(driver)

    def swap(_args):
        device_link.unlink()
        device_link.symlink_to(replacement)
        return iw_text(row)

    with pytest.raises(Unavailable, match="radio_identity_changed"):
        di.collect(sys_root=root, run=swap, wall_clock=lambda: 1000.0)


def test_sysfs_identity_change_during_sample_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    row = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                  transport="sdio", driver="brcmfmac", arphrd=1)
    actual = di.inventory(root)
    calls = []
    def changed(_root):
        calls.append(1)
        if len(calls) == 1:
            return actual
        mutated = dict(actual)
        key = next(iter(mutated))
        identity = list(mutated[key])
        identity[2] += 1
        mutated[key] = tuple(identity)
        return mutated
    monkeypatch.setattr(di, "inventory", changed)
    with pytest.raises(Unavailable, match="radio_identity_changed"):
        di.collect(sys_root=root, run=lambda args: iw_text(row),
                   wall_clock=lambda: 1000.0)


@pytest.mark.parametrize("bad_clock", [True, -1, float("nan"), 10**1000])
def test_invalid_inventory_timestamp_is_refused(tmp_path, bad_clock):
    root = tmp_path / "sys" / "class" / "net"
    root.mkdir(parents=True)
    row = add_net(root, iface="wlan0", ifindex=4, phy="phy0",
                  transport="sdio", driver="brcmfmac", arphrd=1)
    with pytest.raises(Unavailable, match="sample_clock_invalid"):
        di.collect(sys_root=root, run=lambda args: iw_text(row),
                   wall_clock=lambda: bad_clock)


@pytest.mark.parametrize("change", [
    lambda value: value.update(schema=2),
    lambda value: value.update(sampled_utc=float("nan")),
    lambda value: value.update(sampled_utc=10**1000),
    lambda value: value.update(radios=[]),
    lambda value: value["radios"].append(dict(value["radios"][0], transport="usb")),
    lambda value: value["radios"][0].update(identity_stable=False),
    lambda value: value["radios"][0].update(arphrd=True),
    lambda value: value["radios"][0].update(iface=".."),
])
def test_normalized_provider_sample_is_bounded_owned_and_fail_closed(change):
    sample = {"schema": 1, "sampled_utc": 1000, "radios": [{
        "transport": "onboard", "identity_stable": True, "iface": "wlan0",
        "driver": "brcmfmac", "kernel_mode": "managed", "arphrd": 1}]}
    change(sample)
    with pytest.raises(Unavailable, match="radio_sample"):
        di.normalize_sample(sample)


def test_normalized_sample_cannot_be_changed_through_provider_alias():
    row = {"transport": "usb", "identity_stable": True, "iface": "wlan1",
           "driver": "mt76x2u", "kernel_mode": "monitor", "arphrd": 803}
    row["extra"] = {"unbounded": [1] * 100}
    sample = {"schema": 1, "sampled_utc": 1000, "radios": [row],
              "provider_private": [1] * 100}
    clean = di.normalize_sample(sample)
    row["iface"] = "replacement"
    assert clean["radios"][0]["iface"] == "wlan1"
    assert "extra" not in clean["radios"][0] and "provider_private" not in clean
