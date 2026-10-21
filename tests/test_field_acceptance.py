"""Single-command field gate is bounded, credential-free and honest."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

SPEC = importlib.util.spec_from_file_location(
    "redux_field_acceptance",
    Path(__file__).resolve().parents[1] / "scripts/field-acceptance.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

SAMPLE = """REDUX FIELD REPORT (passive, no wireless changes)
2026-10-12T11:43:04+00:00
captures_ext4: True
redux-capture-ingest.timer: active
redux-live.service: active
redux.service: active
live_state: running
checkpoint_freshness: fresh
capture_radio: wlan0
capture_link_type: 803
last_error:
dashboard: True
bettercap_api_probe: HTTP 401 (authentication required; API responding)
events_seen: 17
handed_off: 2
== RF kill ==
0: phy0: Wireless LAN
 Soft blocked: no
 Hard blocked: no
== Regulatory domain ==
global
country US: DFS-FCC
== Interfaces ==
phy#0
 Interface wlan0
  type monitor
  channel 6 (2437 MHz)
== Bettercap last output (sanitized; no caplet) ==
warning: synthetic log line
"""


def test_valid_live_telemetry_meets_measured_field_gates():
    parsed = gate.parse(SAMPLE)
    assert parsed["wifi_soft_blocked"] == "no"
    assert parsed["wifi_hard_blocked"] == "no"
    assert parsed["regulatory_country"] == "US"
    assert parsed["monitor_ifaces"] == {"wlan0"}
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert all(state == "PASS" for state in statuses.values())
    # This is only the declared field signal gate, never a valid handshake claim.
    assert "handshake" not in statuses


def test_starting_engine_does_not_claim_complete_radio_capture():
    text = SAMPLE.replace("live_state: running", "live_state: starting_engine")
    text = text.replace("events_seen: 17", "events_seen: 0")
    text = text.replace("handed_off: 2", "handed_off: 0")
    text = text.replace("bettercap_api_probe: HTTP 401", "bettercap_api_probe: HTTP 400")
    parsed = gate.parse(text)
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Live capture supervisor"] == "FAIL"
    assert statuses["Bettercap REST auth"] == "FAIL"
    assert statuses["Observed runtime events"] == "UNKNOWN"
    assert statuses["Closed capture handoff"] == "UNKNOWN"


def test_wrong_country_or_blocked_wifi_fails():
    text = SAMPLE.replace("Soft blocked: no", "Soft blocked: yes")
    parsed = gate.parse(text)
    checks = {name: state for name, state, _ in gate.verdicts(parsed, "CA")}
    assert checks["Wi-Fi software block"] == "FAIL"
    assert checks["Regulatory country"] == "FAIL"


def test_strict_known_host_key_no_password_and_no_device_mutations(tmp_path, capsys):
    private = tmp_path / "field-key"
    private.write_text("synthetic key file; never read or sent to assistant")
    received = []
    def runner(argv, **kwargs):
        received.append(argv)
        assert "-o" in argv
        assert "StrictHostKeyChecking=yes" in argv
        assert "BatchMode=yes" in argv
        assert "sudo" in argv and "-n" in argv
        assert "redux-report" in argv and "--full" in argv
        assert not any("rfkill" in v or "reboot" in v or "set" == v for v in argv)
        return subprocess.CompletedProcess(argv, 0, SAMPLE, "")
    status = gate.main(["--host", "192.168.12.115", "--country", "US",
                        "--key", str(private)], runner=runner)
    assert status == 0
    assert len(received) == 1
    assert "Field signals observed" in capsys.readouterr().out


def test_reject_public_host_before_ssh(tmp_path):
    key = tmp_path / "id"
    key.touch()
    def refused(argv, **kwargs):
        raise AssertionError("must reject bad target locally")
    for bad in ("8.8.8.8", "127.0.0.1", "169.254.1.1"):
        assert gate.main(["--host", bad, "--country", "US", "--key", str(key)],
                         runner=refused) == 2


def test_unreported_capture_is_unknown_not_success():
    parsed = gate.parse(SAMPLE.replace("events_seen: 17", "")
                        .replace("handed_off: 2", ""))
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Observed runtime events"] == "UNKNOWN"
    assert statuses["Closed capture handoff"] == "UNKNOWN"


def test_unrelated_monitor_interface_never_qualifies_capture_radio():
    text = SAMPLE.replace("  type monitor", "  type managed\n Interface wlan1\n  type monitor")
    parsed = gate.parse(text)
    assert parsed["monitor_ifaces"] == {"wlan1"}
    assert {name: state for name, state, _ in
            gate.verdicts(parsed, "US")}["Monitor radio"] == "FAIL"


def test_monitor_mode_with_ethernet_packets_cannot_pass_link_gate():
    parsed = gate.parse(SAMPLE.replace("capture_link_type: 803", "capture_link_type: 1"))
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Monitor radio"] == "PASS"
    assert statuses["Monitor packet link type"] == "FAIL"


def test_missing_link_measurement_stays_unknown_even_with_spoofed_log_field():
    text = SAMPLE.replace("capture_link_type: 803\n", "")
    text += "\ncapture_link_type: 803\n"
    parsed = gate.parse(text)
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Monitor packet link type"] == "UNKNOWN"


def test_raw_80211_link_type_is_accepted():
    parsed = gate.parse(SAMPLE.replace("capture_link_type: 803", "capture_link_type: 801"))
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Monitor packet link type"] == "PASS"


def test_running_label_cannot_hide_a_stale_or_future_checkpoint():
    for value in ("stale", "future", "invalid"):
        parsed = gate.parse(SAMPLE.replace("checkpoint_freshness: fresh", "checkpoint_freshness: " + value))
        statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
        assert statuses["Live capture supervisor"] == "PASS"
        assert statuses["Live checkpoint freshness"] == "FAIL"


def test_older_helper_without_checkpoint_measurement_is_unknown():
    parsed = gate.parse(SAMPLE.replace("checkpoint_freshness: fresh\n", ""))
    statuses = {name: state for name, state, _ in gate.verdicts(parsed, "US")}
    assert statuses["Live checkpoint freshness"] == "UNKNOWN"


def test_any_wifi_hardware_or_software_block_is_a_failure():
    # Multiple Wi-Fi devices: a blocked second adapter must not be ignored.
    text = SAMPLE.replace("== Regulatory domain ==",
                          "1: phy1: Wireless LAN\n Soft blocked: yes\n"
                          " Hard blocked: no\n== Regulatory domain ==")
    statuses = {name: state for name, state, _ in gate.verdicts(gate.parse(text), "US")}
    assert statuses["Wi-Fi software block"] == "FAIL"
    assert statuses["Wi-Fi hardware block"] == "PASS"

    text = SAMPLE.replace("Hard blocked: no", "Hard blocked: yes")
    statuses = {name: state for name, state, _ in gate.verdicts(gate.parse(text), "US")}
    assert statuses["Wi-Fi hardware block"] == "FAIL"


def test_missing_rfkill_details_are_unknown_not_pass():
    text = SAMPLE.replace("Hard blocked: no", "Hard blocked: unavailable")
    statuses = {name: state for name, state, _ in gate.verdicts(gate.parse(text), "US")}
    assert statuses["Wi-Fi hardware block"] == "UNKNOWN"
    assert statuses["Wi-Fi software block"] == "PASS"


def test_regulatory_domain_uses_global_only_and_ignores_log_tail():
    text = SAMPLE.replace("country US: DFS-FCC",
                          "country 00: DFS-UNSET\nphy#0\ncountry US: DFS-FCC")
    statuses = {name: state for name, state, _ in gate.verdicts(gate.parse(text), "US")}
    assert statuses["Regulatory country"] == "FAIL"

    text = SAMPLE.replace("country US: DFS-FCC", "unavailable: TimeoutExpired")
    text += "\ncountry US: synthetic diagnostic log line\n"
    statuses = {name: state for name, state, _ in gate.verdicts(gate.parse(text), "US")}
    assert statuses["Regulatory country"] == "UNKNOWN"
