"""Offline acceptance for immutable boot regulatory policy and rfkill safety."""
from __future__ import annotations

from pathlib import Path
import importlib
import subprocess
import pytest

mod = importlib.import_module("redux.core.regulatory")
from redux.core import live_runtime as live


def fake_result(argv, code=0, stdout=""):
    return subprocess.CompletedProcess(argv, code, stdout, "")


WIFI_BLOCKED = ("0: phy0: Wireless LAN\n"
                "    Soft blocked: yes\n"
                "    Hard blocked: no\n")
WIFI_READY = ("0: phy0: Wireless LAN\n"
              "    Soft blocked: no\n"
              "    Hard blocked: no\n"
              "1: hci0: Bluetooth\n"
              "    Soft blocked: no\n"
              "    Hard blocked: no\n")


def test_boot_sets_and_verifies_country_before_unblocking_wifi():
    calls = []
    domains = iter(["global\ncountry 00: DFS-UNSET\n",
                    "global\ncountry US: DFS-FCC\n"])
    def command(args, **kw):
        calls.append(args)
        if args == ["iw", "reg", "set", "US"]: return fake_result(args)
        if args == ["iw", "reg", "get"]: return fake_result(args, stdout=next(domains))
        if args == ["rfkill", "list"]:
            return fake_result(args, stdout=WIFI_READY if ["rfkill", "unblock", "wifi"] in calls else WIFI_BLOCKED)
        if args == ["rfkill", "unblock", "wifi"]: return fake_result(args)
        raise AssertionError(args)
    ticks = []
    assert mod.configure("US", runner=command, wait=lambda n: ticks.append(n)) == "US"
    assert ticks == [1]
    assert calls.index(["iw", "reg", "get"]) < calls.index(["rfkill", "unblock", "wifi"])
    assert calls[-1] == ["rfkill", "list"]


def test_boot_does_not_unblock_if_regulatory_country_mismatch_or_error():
    calls = []
    def command(args, **kw):
        calls.append(args)
        if args == ["iw", "reg", "set", "CA"]: return fake_result(args)
        if args == ["iw", "reg", "get"]:
            return fake_result(args, stdout="global\ncountry 00: DFS-UNSET\nphy#0\ncountry CA: DFS-FCC\n")
        raise AssertionError("rfkill must never be touched without global approval")
    with pytest.raises(mod.RegulatoryError, match="did not confirm"):
        mod.configure("CA", runner=command, wait=lambda _: None, attempts=2)
    assert not any(a[0] == "rfkill" for a in calls)


@pytest.mark.parametrize("bad", ["", "00", "ZZ", "AA", "us", "U", "USA", "US; reboot"])
def test_rejects_invalid_country(bad):
    with pytest.raises(mod.RegulatoryError):
        mod.configure(bad, runner=lambda *a, **k: pytest.fail("should reject before RF call"))


def test_country_file_must_be_simple_safe_unlinked(tmp_path):
    path = tmp_path / "country"
    path.write_text("US\n")
    assert mod.read_country(path) == "US"
    path.chmod(0o666)
    with pytest.raises(mod.RegulatoryError, match="permissions"):
        mod.read_country(path)
    path.chmod(0o644)
    linked = tmp_path / "link"
    linked.symlink_to(path)
    with pytest.raises(mod.RegulatoryError, match="symlink"):
        mod.read_country(linked)
    path.write_text("us")
    with pytest.raises(mod.RegulatoryError, match="two-letter"):
        mod.read_country(path)


def test_verify_country_is_readonly_and_detects_soft_block():
    seen = []
    def command(args, **kw):
        seen.append(args)
        return fake_result(args, stdout=("global\ncountry US: DFS-FCC\n"
                                        if args == ["iw", "reg", "get"]
                                        else WIFI_BLOCKED))
    with pytest.raises(mod.RegulatoryError, match="blocked"):
        mod.verify_country("US", runner=command)
    assert seen == [["iw", "reg", "get"], ["rfkill", "list"]]
    def good(args, **kw):
        return fake_result(args, stdout=("global\ncountry US: DFS-FCC\n"
                                        if args == ["iw", "reg", "get"]
                                        else WIFI_READY))
    mod.verify_country("US", runner=good)


def test_live_service_country_fail_closed_before_radio_mode_change(tmp_path, monkeypatch):
    from redux.radio import Radio
    cfg = live.LiveConfig(
        state_dir=tmp_path/"state", capture_dir=tmp_path/"incoming",
        active_dir=tmp_path/"active", enable_web=False,
        require_regulatory_country=True, retry_seconds=3,
    )
    ops = []
    def executor(argv, **kw):
        ops.append(argv)
        return fake_result(argv, stdout="type monitor\n")
    radio = Radio(iface="wlan0", monitor=True, onboard=True,
                  bands=frozenset({"2.4", "5"}), driver="brcmfmac")
    spawned = []
    monkeypatch.setattr(live, "read_country",
                        lambda path: (_ for _ in ()).throw(mod.RegulatoryError("missing operator country")))
    engine = live.LiveRuntime(cfg, radio_probe=lambda: [radio],
                              executor=executor,
                              spawn=lambda *a, **k: spawned.append(a))
    try:
        assert engine.tick() == "degraded"
        assert "missing operator country" in engine.last_error
        assert not spawned
        assert not any(x[:3] == ["ip", "link", "set"] for x in ops)
        assert engine._snapshot["doctor"]["overall"] == "degraded"
    finally:
        engine.close()


def test_boot_contracts_are_image_staged():
    root = Path(__file__).resolve().parents[1]
    unit = (root/"image/stage-redux/00-redux/files/redux-regdom.service").read_text()
    liveunit = (root/"image/stage-redux/00-redux/files/redux-live.service").read_text()
    builder = (root/"build.sh").read_text()
    stage = (root/"image/stage-redux/00-redux/00-run.sh").read_text()
    helper = (root/"scripts/build-field-image.sh").read_text()
    assert "ConditionPathExists=/etc/redux/regulatory-country" in unit
    assert "Before=redux-live.service" in unit
    assert "CapabilityBoundingSet=CAP_NET_ADMIN" in unit
    assert "Wants=redux-regdom.service" in liveunit
    assert "After=local-fs.target redux.service redux-regdom.service" in liveunit
    assert "REDUX_REGULATORY_COUNTRY" in builder
    assert "REDUX_REGULATORY_COUNTRY=\"$country\"" in helper
    assert "systemctl enable redux-regdom.service" in stage
    assert "install -m 0644 files/regulatory-country" in stage


def test_hardware_block_is_never_overridden():
    hard = WIFI_READY.replace("Hard blocked: no", "Hard blocked: yes", 1)
    assert not mod.wireless_unblocked(hard)
    assert not mod.wireless_unblocked("1: hci0: Bluetooth\n    Soft blocked: no\n    Hard blocked: no")
