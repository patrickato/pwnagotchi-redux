"""Boot-POST — the power-on self-test that can't fake a green.

These tests pin the honesty contract: PASS comes only from a probe that actually
returned healthy this run; a raising probe is a FAIL; an UNKNOWN on a *critical*
check never reads as READY; and the verdict is a pure function of probe outputs.
"""
import pytest

from redux.core.post import (
    PowerOnSelfTest, PostCheck, PostStatus, Verdict,
)
from redux.core.doctor import DoctorInputs, Status, Finding
from redux.core import Augur, Scope
from redux.radio import Radio, Intent


def _check(key, ret, critical=False):
    return PostCheck(key, key.title(), (lambda: (ret, f"{key}:{ret}")), critical=critical)


# --- the core honesty property: PASS is earned, never set -------------------- #

def test_pass_only_from_a_truthy_probe():
    pst = PowerOnSelfTest([_check("a", True), _check("b", False), _check("c", None)])
    by = {r.key: r.status for r in pst.results()}
    assert by["a"] is PostStatus.PASS      # True → PASS
    assert by["b"] is PostStatus.FAIL      # False → FAIL
    assert by["c"] is PostStatus.UNKNOWN   # None (can't measure) → UNKNOWN, NOT pass


def test_a_raising_probe_is_a_fail_not_a_skip():
    def boom():
        raise RuntimeError("radio exploded")
    pst = PowerOnSelfTest([PostCheck("x", "X", boom, critical=True)])
    r = pst.results()[0]
    assert r.status is PostStatus.FAIL
    assert "radio exploded" in r.reason
    # and a critical failure halts — it does not quietly pass
    assert pst.verdict(pst.results()) is Verdict.HALT


def test_a_probe_cannot_smuggle_pending_as_a_status():
    pst = PowerOnSelfTest([_check("p", PostStatus.PENDING)])
    assert pst.results()[0].status is PostStatus.UNKNOWN


# --- verdict is a pure function of results ----------------------------------- #

def test_all_critical_pass_is_ready():
    pst = PowerOnSelfTest([_check("a", True, critical=True), _check("b", True)])
    assert pst.verdict(pst.results()) is Verdict.READY


def test_critical_fail_halts():
    pst = PowerOnSelfTest([_check("a", False, critical=True), _check("b", True)])
    assert pst.verdict(pst.results()) is Verdict.HALT


def test_critical_unknown_is_not_ready():
    # couldn't check a must-have → DEGRADED, never READY ("didn't look" ≠ "fine")
    pst = PowerOnSelfTest([_check("a", None, critical=True)])
    assert pst.verdict(pst.results()) is Verdict.DEGRADED


def test_noncritical_fail_degrades_but_does_not_halt():
    pst = PowerOnSelfTest([_check("a", True, critical=True), _check("b", False)])
    assert pst.verdict(pst.results()) is Verdict.DEGRADED


def test_warn_counts_as_passing_for_the_verdict():
    pst = PowerOnSelfTest([_check("a", True, critical=True), _check("w", PostStatus.WARN)])
    results = pst.results()
    assert any(r.status is PostStatus.WARN for r in results)
    assert pst.verdict(results) is Verdict.READY


# --- streaming / ordering ---------------------------------------------------- #

def test_run_yields_in_order_with_index_and_total():
    pst = PowerOnSelfTest([_check("a", True), _check("b", True), _check("c", True)])
    seen = list(pst.run())
    assert [r.key for r in seen] == ["a", "b", "c"]
    assert [r.index for r in seen] == [1, 2, 3]
    assert all(r.total == 3 for r in seen)


def test_tft_frame_shows_pending_tail_while_streaming():
    pst = PowerOnSelfTest([_check("a", True), _check("b", True), _check("c", True)])
    results = pst.results()
    # renderer has resolved only the first line (pending_after=1)
    frame = pst.tft_frame(results, pending_after=1)
    assert frame[0] == "redux POST"
    assert frame[1].startswith("[+]")      # a resolved
    assert frame[2].startswith("[ ]")      # b still pending
    assert frame[3].startswith("[ ]")      # c still pending
    # no verdict banner until the stream completes
    assert not any(line.startswith("--") for line in frame)


# --- renderers are ascii / monochrome-safe ----------------------------------- #

def test_tft_frame_is_ascii_and_banners_the_verdict():
    pst = PowerOnSelfTest([_check("a", False, critical=True)])
    frame = pst.tft_frame(pst.results())
    assert all(line.isascii() for line in frame)
    assert "[X]*" in frame[1]              # critical fail, starred
    assert frame[-1] == "-- HALT --"


def test_console_has_a_line_per_check_and_a_verdict_footer():
    pst = PowerOnSelfTest([_check("a", True, critical=True), _check("b", False)])
    text = pst.console(pst.results())
    lines = text.splitlines()
    assert len(lines) == 3                 # 2 checks + footer
    assert lines[-1].startswith("POST: DEGRADED")


# --- report shape ------------------------------------------------------------ #

def test_report_shape_and_counts():
    pst = PowerOnSelfTest([_check("a", True, critical=True), _check("b", False), _check("c", None)])
    rep = pst.report()
    assert rep["verdict"] == "degraded" and rep["ready"] is False
    assert rep["counts"]["pass"] == 1 and rep["counts"]["fail"] == 1 and rep["counts"]["unknown"] == 1
    assert [c["key"] for c in rep["checks"]] == ["a", "b", "c"]
    assert rep["checks"][0]["critical"] is True


# --- standard(): the Doctor → boot mapping, live ----------------------------- #

def test_standard_maps_doctor_statuses():
    # with no inputs at all, every Doctor probe is UNKNOWN → capture is a critical
    # UNKNOWN → DEGRADED (we could not confirm the radio), never READY.
    pst = PowerOnSelfTest.standard(DoctorInputs())
    rep = pst.report()
    assert rep["verdict"] == "degraded"
    cap = next(c for c in rep["checks"] if c["key"] == "capture")
    assert cap["status"] == "unknown" and cap["critical"] is True


def test_standard_appends_extra_hardware_probes():
    extra = [PostCheck("tft", "TFT init", (lambda: (True, "panel up")))]
    pst = PowerOnSelfTest.standard(DoctorInputs(), extra=extra)
    assert any(c.key == "tft" for c in pst.checks)


# --- integration through Augur ------------------------------------------- #

class _FakeDriver:
    """No-op bettercap driver so the CAPTURE_HANDSHAKE engine is present."""
    def __getattr__(self, name):
        return lambda *a, **k: {}


def _bc(radios, driver=None):
    return Augur(radios=radios, intent=Intent.RECON, driver=driver)


def test_augur_boot_post_ready_with_a_monitor_radio_and_engine():
    mon = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
    rep = _bc([mon], driver=_FakeDriver()).boot_post()   # driver up → capture engine present
    cap = next(c for c in rep["checks"] if c["key"] == "capture")
    eng = next(c for c in rep["checks"] if c["key"] == "capture-engine")
    assert cap["status"] == "pass" and eng["status"] == "pass"
    assert rep["verdict"] == "ready" and rep["ready"] is True


def test_augur_boot_post_degraded_when_engine_absent():
    # a monitor radio but NO capture engine (no driver / no AngryOxide) → honest
    # DEGRADED, not READY: you can't capture without an engine.
    mon = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False,
                driver="brcmfmac", onboard=True)
    rep = _bc([mon]).boot_post()
    assert next(c for c in rep["checks"] if c["key"] == "capture")["status"] == "pass"
    assert next(c for c in rep["checks"] if c["key"] == "capture-engine")["status"] == "fail"
    assert rep["verdict"] == "degraded" and rep["ready"] is False


def test_augur_boot_post_halts_without_a_capture_radio():
    # a radio that can't do monitor mode → the critical capture check FAILS → HALT
    blind = Radio("wlan9", bands=frozenset({"2.4"}), monitor=False, inject=False,
                  driver="rtl8188", onboard=False)
    rep = _bc([blind]).boot_post()
    cap = next(c for c in rep["checks"] if c["key"] == "capture")
    assert cap["status"] == "fail"
    assert rep["verdict"] == "halt" and rep["ready"] is False
