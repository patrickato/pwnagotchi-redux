"""Offline historical acceptance-to-TFT bridge; no physical device access."""
import copy
import json
import stat

import pytest
from PIL import Image

from redux.display import acceptance_snapshot, fbui, preview, radio_panel


def acceptance():
    return {
        "format": "redux-readonly-acceptance-v1",
        "hardware_changes": False,
        "inventory_status": "fresh",
        "gates": {
            "ethernet_return_path": "PASS", "lease_eligible": "FAIL",
            "usb_wifi_netdev_present": "PASS"
        },
        "radios": [
            {"radio": 1, "transport": "onboard", "kernel_mode": "monitor",
             "arphrd": 1, "driver": "brcmfmac", "identity_stable": True,
             "authenticated_owner": "UNKNOWN", "decoded_reception": "UNKNOWN",
             "lease_eligible": False, "nm_managed": True},
            {"radio": 2, "transport": "usb", "kernel_mode": "monitor",
             "arphrd": 803, "driver": "mt76x2u", "identity_stable": True,
             "authenticated_owner": "UNKNOWN", "decoded_reception": "UNKNOWN",
             "lease_eligible": False, "nm_managed": True}
        ],
        "pan": {"paired": "UNKNOWN"},
        "usb_bus": {"device_count_including_hubs": 5, "inventory_stable": True}
    }


def test_actual_sanitized_acceptance_produces_historical_qualified_model():
    saved = acceptance_snapshot.snapshot(acceptance())
    assert saved == {
        "schema": 1, "source": "historical_readonly_acceptance",
        "radios": [
            {"transport": "onboard", "driver": "brcmfmac",
             "arphrd": 1, "mode": "MONITOR"},
            {"transport": "usb", "driver": "mt76x2u",
             "arphrd": 803, "mode": "MONITOR"}
        ]
    }
    view = radio_panel.model({"radio_acceptance_snapshot": saved}, now=1000)
    assert view["inventory"] == "SAVED SNAPSHOT"
    assert view["usb"]["state"] == "SAVED"
    assert view["usb"]["reception"] == "UNVERIFIED"
    assert view["capture"]["iface"] == "UNKNOWN"
    assert view["capture"]["state"] == "NOT CURRENT"
    assert view["onboard"]["link"] == 1


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(format="legacy"),
    lambda p: p.update(hardware_changes=True),
    lambda p: p.update(inventory_status="stale"),
    lambda p: p["gates"].update(ethernet_return_path="UNKNOWN"),
    lambda p: p["gates"].update(lease_eligible="PASS"),
    lambda p: p["radios"][0].update(authenticated_owner="verified"),
    lambda p: p["radios"][1].update(decoded_reception=1),
    lambda p: p["radios"][1].update(lease_eligible=True),
    lambda p: p["radios"][0].update(identity_stable=False),
    lambda p: p["radios"][0].update(transport="usb"),
    lambda p: p["radios"][1].update(driver="../../evil"),
    lambda p: p["radios"][1].update(arphrd=True),
    lambda p: p["radios"][1].update(interface="wlan2"),
    lambda p: p.update(remote_address="private-data"),
    lambda p: p.update(radios=[]),
])
def test_unqualified_private_or_malformed_acceptance_rejected(mutate):
    payload = acceptance()
    mutate(payload)
    with pytest.raises(ValueError):
        acceptance_snapshot.snapshot(payload)


def test_acceptance_is_not_treated_as_fresh_runtime_even_after_elapsed_time():
    model = {"radio_acceptance_snapshot":
             acceptance_snapshot.snapshot(acceptance())}
    assert radio_panel.model(model, now=100)["inventory"] == "SAVED SNAPSHOT"
    assert radio_panel.model(model, now=999999999)["inventory"] == "SAVED SNAPSHOT"
    assert radio_panel.model(model, now=999999999)["usb"]["reception"] == "UNVERIFIED"


def test_untrusted_injected_snapshot_gets_no_fake_current_inventory():
    fake = {"radio_acceptance_snapshot": {
        "schema": 1, "source": "historical_readonly_acceptance",
        "radios": [{"transport": "usb", "driver": "../secret",
                    "mode": "MONITOR", "arphrd": 803}]}}
    assert radio_panel.model(fake, now=100)["inventory"] == "NOT REPORTED"


def test_actual_preview_cli_generates_single_private_real_screen(tmp_path, capsys, monkeypatch):
    inp = tmp_path / "acceptance.json"
    inp.write_text(json.dumps(acceptance()))
    out = tmp_path / "screens"
    out.mkdir(mode=0o700)
    monkeypatch.setattr(fbui, "read_status", lambda *_a: pytest.fail("network used"))
    monkeypatch.setattr(fbui, "framebuffer_info", lambda *_a: pytest.fail("fb touched"))
    monkeypatch.setattr(fbui, "write_frame", lambda *_a, **_k: pytest.fail("fb written"))
    assert preview.main(["--output-dir", str(out), "--acceptance-json", str(inp)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["source"] == "historical_readonly_acceptance"
    assert response["files"] == ["redux-radios.png"]
    assert response["physical_display_verified"] is False
    assert response["touch_verified"] is False
    png = out / "redux-radios.png"
    assert stat.S_IMODE(png.stat().st_mode) == 0o600
    with Image.open(png) as image:
        assert image.size == (480, 320)
        image.verify()


def test_historical_preview_rejects_unrelated_pages_without_creating_files(tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps(acceptance()))
    with pytest.raises(SystemExit) as ex:
        preview.main(["--acceptance-json", str(f), "--output-dir", str(tmp_path),
                      "--page", "captures"])
    assert ex.value.code == 2
    assert not (tmp_path / "redux-captures.png").exists()


def test_historical_acceptance_loader_rejects_symlink_and_oversize(tmp_path):
    f = tmp_path / "report.json"
    f.write_text(json.dumps(acceptance()))
    assert acceptance_snapshot.load(str(f))["radios"][1]["driver"] == "mt76x2u"
    alias = tmp_path / "alias.json"
    alias.symlink_to(f)
    with pytest.raises(OSError):
        acceptance_snapshot.load(str(alias))
    f.write_bytes(b"x" * 65537)
    with pytest.raises(ValueError):
        acceptance_snapshot.load(str(f))
