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
capture_radio: wlan0
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
    assert parsed["regulatory_country"] == "US"
    assert parsed["monitor_mode"] is True
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
    assert statuses["Observed radio events"] == "UNKNOWN"
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
    assert statuses["Observed radio events"] == "UNKNOWN"
    assert statuses["Closed capture handoff"] == "UNKNOWN"
