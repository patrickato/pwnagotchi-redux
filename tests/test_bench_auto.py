"""Unit tests for the benchpass auto-executor (redux/bench_auto.py).

Every test drives the executor with injected fakes — a fake `run` (shell), fake
`probe_radios`, fake `confirm`/`ask`, fake clock — so the whole dispatch is
exercised with no hardware, no subprocess, and no network. The contract under
test is the honesty contract: PASS only on a real confirming probe, SKIP (never a
fake pass) when hardware/condition is absent, FAIL when a probe runs and the
criterion is not met, and operator fallback only where a machine cannot judge.
"""
import os
import tempfile

import pytest

from redux import bench_auto as ba
from redux.benchpass import GATES

GMAP = {g.id: g for g in GATES}


class FakeRadio:
    def __init__(self, iface, *, monitor=False, onboard=False, bands=("2.4", "5")):
        self.iface = iface
        self.monitor = monitor
        self.inject = monitor
        self.bands = list(bands)
        self.transport = "sdio" if onboard else "usb"
        self.driver = "brcmfmac" if onboard else "mt76"


def mk(*, run=None, probe=None, confirm=None, ask=None, handshake_dirs=None):
    """An executor with safe non-blocking defaults and a fixed redux prefix so
    argv assertions are predictable."""
    return ba.AutoExecutor(
        run=run or (lambda c, t=20: (0, "")),
        probe_radios=probe or (lambda: []),
        confirm=confirm or (lambda m: False),
        ask=ask or (lambda g: ("operator", "asked")),
        report=lambda *a, **k: None,
        sleep=lambda s: None,
        now=lambda: 1000.0,
        settle_s=0.0,
        redux_prefix=["redux"],
        handshake_dirs=handshake_dirs,
    )


# --------------------------------------------------------------------------- #
# pure parsers
# --------------------------------------------------------------------------- #

def test_parse_temp():
    assert ba._parse_temp("temp=47.2'C") == 47.2
    assert ba._parse_temp("garbage") is None


def test_post_verdict():
    assert ba._post_verdict("...\nPOST: DEGRADED — degraded: Capture engine") == "degraded"
    assert ba._post_verdict("POST: READY — all good") == "ready"
    assert ba._post_verdict("POST: UNKNOWN — no readings") == "unknown"
    assert ba._post_verdict("no verdict here") == ""


def test_redux_prefix_autodetect(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which", lambda n: None)
    ax = ba.AutoExecutor(report=lambda *a, **k: None)
    assert ax.redux_prefix[-2:] == ["-m", "redux.cli"]
    monkeypatch.setattr(ba.shutil, "which", lambda n: "/usr/bin/redux")
    ax2 = ba.AutoExecutor(report=lambda *a, **k: None)
    assert ax2.redux_prefix == ["/usr/bin/redux"]


# --------------------------------------------------------------------------- #
# 1.1 service
# --------------------------------------------------------------------------- #

def test_service_active_passes():
    def run(c, t=20):
        if c[:2] == ["systemctl", "is-active"]:
            return 0, "active\n"
        if c[:1] == ["cat"]:
            return 0, "Raspberry Pi 5 Model B\x00"
        return 0, ""
    status, detail = mk(run=run)(GMAP["1.1"])
    assert status == "pass"
    assert "Raspberry Pi 5" in detail


def test_service_inactive_defers_to_operator():
    run = lambda c, t=20: (3, "inactive\n") if c[:2] == ["systemctl", "is-active"] else (0, "")
    status, detail = mk(run=run, ask=lambda g: ("skip", "no systemd"))(GMAP["1.1"])
    assert status == "skip"           # operator verdict, not a fabricated pass


# --------------------------------------------------------------------------- #
# 1.2 radios
# --------------------------------------------------------------------------- #

def test_radios_none_skips_honestly():
    status, detail = mk(probe=lambda: [])(GMAP["1.2"])
    assert status == "skip" and "no radios" in detail.lower()


def test_radios_onboard_plus_external_notes_tandem():
    radios = [FakeRadio("wlan0", monitor=True, onboard=True),
              FakeRadio("wlan1", monitor=True)]
    status, detail = mk(probe=lambda: radios)(GMAP["1.2"])
    assert status == "pass"
    assert "tandem" in detail.lower()
    assert "wlan0" in detail and "wlan1" in detail


def test_radios_onboard_only_labeled_basics():
    status, detail = mk(probe=lambda: [FakeRadio("wlan0", monitor=False, onboard=True)])(GMAP["1.2"])
    assert status == "pass" and "basics" in detail.lower()


# --------------------------------------------------------------------------- #
# 1.3 hotplug (hybrid)
# --------------------------------------------------------------------------- #

def test_hotplug_pass_on_drop_then_recover():
    seq = [
        [FakeRadio("wlan0", onboard=True), FakeRadio("wlan1")],  # before
        [FakeRadio("wlan0", onboard=True)],                       # unplugged
        [FakeRadio("wlan0", onboard=True), FakeRadio("wlan1")],  # replugged
    ]
    calls = {"n": 0}

    def probe():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return seq[i]

    status, detail = mk(probe=probe, confirm=lambda m: True)(GMAP["1.3"])
    assert status == "pass"
    assert "2->1" in detail and "->2" in detail


def test_hotplug_skip_without_external_adapter():
    status, detail = mk(probe=lambda: [FakeRadio("wlan0", onboard=True)],
                        confirm=lambda m: True)(GMAP["1.3"])
    assert status == "skip" and "no external" in detail.lower()


def test_hotplug_skip_when_operator_declines():
    status, detail = mk(probe=lambda: [FakeRadio("wlan0", onboard=True), FakeRadio("wlan1")],
                        confirm=lambda m: False)(GMAP["1.3"])
    assert status == "skip" and "declined" in detail.lower()


def test_hotplug_fail_when_no_change_observed():
    radios = [FakeRadio("wlan0", onboard=True), FakeRadio("wlan1")]
    status, detail = mk(probe=lambda: radios, confirm=lambda m: True)(GMAP["1.3"])
    assert status == "fail"           # count never dropped → not a real hotplug


# --------------------------------------------------------------------------- #
# 1.4 / 1.6 handshake (hybrid, scope-aware, artifact-verified)
# --------------------------------------------------------------------------- #

def test_handshake_skip_without_monitor_radio():
    status, detail = mk(probe=lambda: [FakeRadio("wlan0", monitor=False)])(GMAP["1.4"])
    assert status == "skip" and "monitor" in detail.lower()


def test_handshake_fail_when_no_artifact_after_capture():
    d = tempfile.mkdtemp()
    status, detail = mk(probe=lambda: [FakeRadio("wlan1", monitor=True)],
                        run=lambda c, t=20: (0, "scope: 0 active (EMPTY — nothing authorized)"),
                        confirm=lambda m: True, handshake_dirs=(d,))(GMAP["1.4"])
    assert status == "fail" and "no valid handshake" in detail.lower()


def test_handshake_autopass_from_existing_daemon_capture():
    # A valid handshake already on disk (passive daemon) → PASS with NO operator
    # action, even nested in subdirs, even if the operator would decline a live run.
    d = tempfile.mkdtemp()
    nested = os.path.join(d, "ready", "2026-10-21")
    os.makedirs(nested)
    line = ("WPA*02*" + "a" * 32 + "*001122334455*66778899aabb*6578616d706c65*"
            + "b" * 64 + "*aabbccdd*02\n")
    with open(os.path.join(nested, "cap.hc22000"), "wb") as fh:
        fh.write(line.encode())
    status, detail = mk(probe=lambda: [FakeRadio("wlan1", monitor=True)],
                        confirm=lambda m: False,    # would decline — must not matter
                        handshake_dirs=(d,))(GMAP["1.4"])
    assert status == "pass" and "capture store" in detail.lower()
    assert "001122334455" in detail                 # BSSID surfaced


def test_handshake_skip_when_nothing_on_disk_and_operator_declines():
    status, detail = mk(probe=lambda: [FakeRadio("wlan1", monitor=True)],
                        run=lambda c, t=20: (0, "scope: 1 active"),
                        confirm=lambda m: False,
                        handshake_dirs=(tempfile.mkdtemp(),))(GMAP["1.4"])
    assert status == "skip" and "did not run" in detail.lower()


# --------------------------------------------------------------------------- #
# 2.1 packs
# --------------------------------------------------------------------------- #

def test_packs_skip_when_catalog_unavailable():
    run = lambda c, t=20: (0, "plugin state unavailable: captures mount unavailable")
    status, detail = mk(run=run)(GMAP["2.1"])
    assert status == "skip" and "available" in detail.lower()


def test_packs_pass_on_clean_round_trip():
    log = []

    def run(c, t=20):
        log.append(c)
        if c[-1] == "list":
            return 0, "mypack   disabled\n"
        return 0, "ok"

    status, detail = mk(run=run)(GMAP["2.1"])
    assert status == "pass" and "mypack" in detail
    joined = [" ".join(c) for c in log]
    assert any("enable mypack" in j for j in joined)
    assert any("disable mypack" in j for j in joined)


def test_packs_fail_when_enable_errors():
    def run(c, t=20):
        if c[-1] == "list":
            return 0, "mypack disabled\n"
        if "enable" in c:
            return 2, "boom"
        return 0, ""
    status, _ = mk(run=run)(GMAP["2.1"])
    assert status == "fail"


# --------------------------------------------------------------------------- #
# 2.2 sightings (fully auto, real store)
# --------------------------------------------------------------------------- #

def test_sightings_persist_real_store():
    status, detail = mk()(GMAP["2.2"])
    assert status == "pass"
    assert "never fabricated" in detail.lower()


# --------------------------------------------------------------------------- #
# 2b.2 engines
# --------------------------------------------------------------------------- #

def _plan_out(selected, installed):
    return (f"capture engine (selected, by availability): {selected}  (offense_enabled=True, passive=None)\n"
            f"  reason line\n\nAngryOxide preview (installed={installed}):\n  preview line\n")


def test_engines_skip_without_binaries(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which", lambda n: None)
    status, detail = mk()(GMAP["2b.2"])
    assert status == "skip"


def test_engines_pass_ao_present_and_selected(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which", lambda n: "/usr/bin/" + n if n == "angryoxide" else None)
    run = lambda c, t=20: (0, _plan_out("AngryOxide", "True"))
    status, detail = mk(run=run)(GMAP["2b.2"])
    assert status == "pass" and "scalpel" in detail.lower()


def test_engines_pass_ao_absent_is_honest(monkeypatch):
    # bettercap binary present, AngryOxide absent: a static plan truthfully
    # reports no engine (bettercap needs a live driver). That is NOT a failure.
    monkeypatch.setattr(ba.shutil, "which", lambda n: "/usr/bin/" + n if n == "bettercap" else None)
    run = lambda c, t=20: (0, _plan_out("None", "False"))
    status, detail = mk(run=run)(GMAP["2b.2"])
    assert status == "pass" and "truthfully" in detail.lower()


def test_engines_fail_on_untruthful_preview(monkeypatch):
    # AngryOxide IS installed but the preview claims installed=False → not truthful.
    monkeypatch.setattr(ba.shutil, "which", lambda n: "/usr/bin/" + n if n == "angryoxide" else None)
    run = lambda c, t=20: (0, _plan_out("None", "False"))
    status, detail = mk(run=run)(GMAP["2b.2"])
    assert status == "fail" and "truthful" in detail.lower()


def test_engines_fail_ao_present_not_selected(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which", lambda n: "/usr/bin/" + n if n == "angryoxide" else None)
    run = lambda c, t=20: (0, _plan_out("bettercap", "True"))
    status, detail = mk(run=run)(GMAP["2b.2"])
    assert status == "fail" and "selected" in detail.lower()


# --------------------------------------------------------------------------- #
# 2b.9 governor
# --------------------------------------------------------------------------- #

def test_governor_skip_without_vcgencmd():
    run = lambda c, t=20: (127, "not found: vcgencmd")
    status, detail = mk(run=run)(GMAP["2b.9"])
    assert status == "skip" and "vcgencmd" in detail.lower()


def test_governor_pass_escalates_on_hot_synthetic():
    run = lambda c, t=20: (0, "temp=55.0'C") if c and c[0] == "vcgencmd" else (0, "")
    status, detail = mk(run=run)(GMAP["2b.9"])
    assert status == "pass"
    assert "SURVIVAL" in detail


# --------------------------------------------------------------------------- #
# 2b.10 boot-POST honesty
# --------------------------------------------------------------------------- #

def test_post_catches_faked_green(monkeypatch):
    # POST claims READY but there is no engine on PATH and no monitor radio → fail
    monkeypatch.setattr(ba.shutil, "which", lambda n: None)
    run = lambda c, t=20: (0, "POST: READY — all good")
    status, detail = mk(run=run, probe=lambda: [])(GMAP["2b.10"])
    assert status == "fail" and "faked green" in detail.lower()


def test_post_honest_degraded_passes(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which", lambda n: None)
    run = lambda c, t=20: (0, "POST: DEGRADED — degraded: Capture engine")
    status, detail = mk(run=run, probe=lambda: [])(GMAP["2b.10"])
    assert status == "pass" and "degraded" in detail.lower()


def test_post_ready_with_real_prereqs_passes(monkeypatch):
    monkeypatch.setattr(ba.shutil, "which",
                        lambda n: "/usr/bin/" + n if n in ("bettercap", "angryoxide") else None)
    run = lambda c, t=20: (0, "POST: READY — all good")
    status, detail = mk(run=run, probe=lambda: [FakeRadio("wlan1", monitor=True)])(GMAP["2b.10"])
    assert status == "pass"


# --------------------------------------------------------------------------- #
# default dispatch: unknown gate → operator
# --------------------------------------------------------------------------- #

def test_unknown_gate_falls_back_to_operator():
    from redux.benchpass import Gate
    called = {"n": 0}

    def ask(g):
        called["n"] += 1
        return "pass", "operator confirmed"

    fake = Gate("9.9", "subsystem", "a gate with no auto-handler", (), "run it", "it passes")
    status, detail = mk(ask=ask)(fake)
    assert called["n"] == 1
    assert status == "pass"


def test_handler_exception_falls_back_to_operator():
    def boom():
        raise RuntimeError("probe blew up")
    status, detail = mk(probe=boom, ask=lambda g: ("skip", "fell back"))(GMAP["1.2"])
    assert status == "skip" and detail == "fell back"


# --------------------------------------------------------------------------- #
# measure_facts
# --------------------------------------------------------------------------- #

def test_measure_facts_reports_missing_honestly():
    facts = ba.measure_facts(run=lambda c, t=20: (127, "nope"), probe_radios=lambda: [])
    assert facts["radios"] is False
    assert facts["monitor_radio"] is False
    assert "none" in facts["radios_detail"].lower()


def test_measure_facts_sees_radios():
    facts = ba.measure_facts(run=lambda c, t=20: (0, ""),
                             probe_radios=lambda: [FakeRadio("wlan1", monitor=True)])
    assert facts["radios"] is True and facts["monitor_radio"] is True


# --------------------------------------------------------------------------- #
# 1.5 intent switch + TFT render
# --------------------------------------------------------------------------- #

from redux.capstone import sample_inventory  # real Radio objects for decide()


def _post_tft_run(c, t=20):
    if "post" in c:
        return 1, "redux POST\n[+] Capture radio\n[X] Capture engine\n-- DEGRADED --"
    return 0, ""


def test_intent_tft_skip_without_radios():
    status, detail = mk(probe=lambda: [])(GMAP["1.5"])
    assert status == "skip" and "no radios" in detail.lower()


def test_intent_tft_pass_rearrange_and_render():
    status, detail = mk(probe=sample_inventory, run=_post_tft_run)(GMAP["1.5"])
    assert status == "pass"
    assert "distinct" in detail.lower() and "tft" in detail.lower()


def test_intent_tft_fail_when_frame_does_not_render():
    status, detail = mk(probe=sample_inventory, run=lambda c, t=20: (0, ""))(GMAP["1.5"])
    assert status == "fail" and "render" in detail.lower()


# --------------------------------------------------------------------------- #
# 2b.11 scope persistence placement
# --------------------------------------------------------------------------- #

def test_scope_persist_pass_on_persistent_fs(monkeypatch, tmp_path):
    import redux.cli as cli
    monkeypatch.setattr(cli, "_DEFAULT_SCOPE_PATH", str(tmp_path / "config" / "scope.json"))
    monkeypatch.setattr(ba, "_fstype_of", lambda p: "ext4")
    status, detail = mk()(GMAP["2b.11"])
    assert status == "pass" and "persistent" in detail.lower()


def test_scope_persist_fail_on_tmpfs(monkeypatch, tmp_path):
    import redux.cli as cli
    monkeypatch.setattr(cli, "_DEFAULT_SCOPE_PATH", str(tmp_path / "pwnagotchi" / "scope.json"))
    monkeypatch.setattr(ba, "_fstype_of", lambda p: "tmpfs")
    status, detail = mk()(GMAP["2b.11"])
    assert status == "fail" and "survive reboot" in detail.lower()


# --------------------------------------------------------------------------- #
# 2b.12 deauth detector pipeline
# --------------------------------------------------------------------------- #

def test_deauth_detect_pass_fires_and_stays_silent():
    status, detail = mk()(GMAP["2b.12"])
    assert status == "pass"
    assert "deauth_flood" in detail.lower() and "external deauth" in detail.lower()


def test_fstype_of_reads_proc_mounts():
    # smoke: resolves SOME fstype for root without throwing
    assert isinstance(ba._fstype_of("/"), str)


# --------------------------------------------------------------------------- #
# remaining subsystem gates: honest verdicts (2b.1/2b.3/2b.4/2b.5/2b.6/2b.7/2b.8)
# --------------------------------------------------------------------------- #

def test_csi_skip_without_source():
    status, detail = mk(run=lambda c, t=20: (0, ""))(GMAP["2b.1"])
    assert status == "skip" and "nexmon_csi" in detail.lower()


def test_eap_skip_but_gating_verified_when_plan_refuses():
    run = lambda c, t=20: (0, "EAP harvest ... runnable=False\n  refused: SSID not in the authorized scope")
    status, detail = mk(run=run)(GMAP["2b.3"])
    assert status == "skip" and "gating verified" in detail.lower()


def test_eap_fail_when_plan_does_not_refuse():
    run = lambda c, t=20: (0, "EAP harvest ... runnable=True, firing now")
    status, detail = mk(run=run)(GMAP["2b.3"])
    assert status == "fail" and "gating broken" in detail.lower()


def test_eap_gating_probed_against_hermetic_nonlab_scope():
    # Regression: the gate must probe a forced-empty, non-lab scope via a
    # --scope-file placed BEFORE `plan`, so lab mode being ON on the device can't
    # false-fail it (lab mode legitimately approves any SSID on sight). Before this
    # the grader read the ambient scope and failed under lab mode.
    seen = {}

    def run(c, t=20):
        seen["cmd"] = list(c)
        return (0, "EAP harvest ... runnable=False\n  refused: SSID not in the authorized scope")

    status, _ = mk(run=run)(GMAP["2b.3"])
    assert status == "skip"
    cmd = seen["cmd"]
    assert "--scope-file" in cmd
    assert cmd.index("--scope-file") < cmd.index("plan")      # attaches to `eap`, not `plan`
    scope_arg = cmd[cmd.index("--scope-file") + 1]
    assert not os.path.exists(scope_arg)                      # absent -> empty -> non-lab -> refuses


def test_mesh_skip_without_lora():
    status, detail = mk()(GMAP["2b.4"])
    assert status == "skip" and "lora" in detail.lower()


def test_foxhunt_and_sentinel_skip_honestly():
    s5, d5 = mk()(GMAP["2b.5"])
    s7, d7 = mk()(GMAP["2b.7"])
    assert s5 == "skip" and "walk" in d5.lower()
    assert s7 == "skip" and "csi" in d7.lower()


def test_fingerprint_skip_when_operator_declines():
    status, detail = mk(confirm=lambda m: False)(GMAP["2b.6"])
    assert status == "skip" and "captap live" in detail.lower()


def test_fingerprint_pass_on_live_reid():
    run = lambda c, t=20: (0, "device identities: 2 total, 1 re-identified across MAC")
    status, detail = mk(confirm=lambda m: True,
                        probe=lambda: [FakeRadio("wlan0", monitor=True)], run=run)(GMAP["2b.6"])
    assert status == "pass" and "re-identified" in detail.lower()


def test_operator_gating_pass():
    calls = {"n": 0}

    def run(c, t=20):
        if "campaign" in c:
            calls["n"] += 1
            if calls["n"] == 1:
                return 0, "[BLOCK] 00:11:22:33:44:55   capture   handshake_capture  — not authorized: scope is empty"
            return 0, "[ok   ] 00:11:22:33:44:55   capture   handshake_capture  — authorized"
        return 0, ""

    status, detail = mk(run=run)(GMAP["2b.8"])
    assert status == "pass" and "gating verified" in detail.lower()


def test_operator_gating_fail_when_unarmed_not_blocked():
    run = lambda c, t=20: (0, "[ok   ] 00:11:22:33:44:55   capture   handshake_capture  — authorized")
    status, detail = mk(run=run)(GMAP["2b.8"])
    assert status == "fail"


def test_capture_line_parsers():
    blocked = "[BLOCK] 00:11:22:33:44:55 capture handshake_capture — not authorized: scope is empty"
    okline = "[ok   ] 00:11:22:33:44:55 capture handshake_capture — authorized"
    assert ba._capture_blocked(blocked, "00:11:22:33:44:55") is True
    assert ba._capture_authorized(okline, "00:11:22:33:44:55") is True
    assert ba._capture_authorized(blocked, "00:11:22:33:44:55") is False
