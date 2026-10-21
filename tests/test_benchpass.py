"""Benchpass — the one lump-sum hardware session. The orchestration (ordering,
resumable ledger, report) is pure and takes an injected executor, so it is
exercised here with no hardware and no I/O prompts."""
import json

from redux import benchpass as bp


def test_registry_is_well_formed():
    ids = [g.id for g in bp.GATES]
    assert len(ids) == len(set(ids)), "gate ids must be unique"
    assert len(bp.GATES) >= 15
    for g in bp.GATES:
        assert g.phase in ("phase1", "platform", "subsystem")
        assert g.title and g.live and g.passes
        assert isinstance(g.manual, bool)
    # the headline gate is present
    assert any(g.id == "1.6" for g in bp.GATES)


def test_gear_checklist_dedups_preserving_order():
    gear = bp.gear_checklist()
    assert gear == list(dict.fromkeys(gear))          # no duplicates
    assert "your own AP" in gear                       # appears in several gates, once here


def test_ledger_roundtrip_and_resume(tmp_path):
    p = str(tmp_path / "ledger.json")
    led = bp.Ledger.load(p)                             # fresh
    led.record("1.1", "pass", "booted")
    led.record("1.2", "fail", "no adapter")
    led.save()
    again = bp.Ledger.load(p)
    assert again.status_of("1.1") == "pass"
    assert again.gates["1.2"]["detail"] == "no adapter"
    assert again.done("1.1") and again.done("1.2")
    assert not again.done("1.6")                        # untouched -> pending


def test_run_records_all_and_counts(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    calls = []

    def ex(g):
        calls.append(g.id)
        return ("pass", "")

    rep = bp.run_benchpass(bp.GATES, ex, led)
    assert calls == [g.id for g in bp.GATES]           # walked in order, all of them
    assert rep.counts["pass"] == len(bp.GATES)
    assert rep.complete and rep.clean


def test_resume_skips_terminal_gates(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    led.record("1.1", "pass")
    led.record("1.2", "skip")
    seen = []

    def ex(g):
        seen.append(g.id)
        return ("pass", "")

    bp.run_benchpass(bp.GATES, ex, led, resume=True)
    assert "1.1" not in seen and "1.2" not in seen       # already terminal -> not re-run
    assert "1.6" in seen


def test_only_filters_the_walk_and_report(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    seen = []

    def ex(g):
        seen.append(g.id)
        return ("pass", "")

    rep = bp.run_benchpass(bp.GATES, ex, led, only=["1.6", "2b.6"])
    assert set(seen) == {"1.6", "2b.6"}
    assert {r["id"] for r in rep.rows} == {"1.6", "2b.6"}
    assert rep.counts["pass"] == 2 and rep.clean


def test_non_terminal_status_is_coerced_to_skip(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    rep = bp.run_benchpass([bp.GATES[0]], lambda g: ("weird", ""), led)
    assert led.status_of(bp.GATES[0].id) == "skip"
    assert rep.counts["skip"] == 1


def test_report_clean_vs_complete(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    led.record("1.1", "pass")
    rep = bp.build_report(bp.GATES, led)
    assert not rep.complete                              # the rest are pending
    assert not rep.clean
    # a fail makes it complete-but-not-clean once nothing is pending
    led2 = bp.Ledger(path=str(tmp_path / "l2.json"))
    for g in bp.GATES:
        led2.record(g.id, "pass")
    led2.record("1.4", "fail", "no handshake")
    rep2 = bp.build_report(bp.GATES, led2)
    assert rep2.complete and not rep2.clean


def test_pending_status_is_preserved_not_coerced(tmp_path):
    led = bp.Ledger(path=str(tmp_path / "l.json"))
    bp.run_benchpass([bp.GATES[0]], lambda g: ("pending", "no input"), led)
    assert led.status_of(bp.GATES[0].id) == "pending"


def test_preflight_board_marks_ok_and_missing():
    board = bp.preflight({"radios": True, "radios_detail": "2 enumerated",
                          "monitor_radio": False, "capture_engine": True,
                          "captures_mount": True, "software_battery": True,
                          "crypto": False})
    assert board["radios"]["ok"] is True
    assert board["radios"]["detail"] == "2 enumerated"
    assert board["monitor_radio"]["ok"] is False
    assert "1.6" in board["monitor_radio"]["unblocks"]


def test_preflight_ready_ignores_optional_crypto():
    base = {"radios": True, "monitor_radio": True, "capture_engine": True,
            "captures_mount": True, "software_battery": True, "crypto": False}
    assert bp.preflight_ready(bp.preflight(base)) is True        # crypto optional
    base["capture_engine"] = False                               # a required one missing
    assert bp.preflight_ready(bp.preflight(base)) is False
