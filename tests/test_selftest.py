"""redux selftest — the on-device battery returns honest PASS/FAIL/SKIP."""
from redux.selftest import run_selftest, Check


def test_selftest_core_checks_pass():
    checks = {c.name: c for c in run_selftest()}
    assert checks["python"].status == "PASS"
    assert checks["import redux"].status == "PASS"
    # the P0 pipeline must prove itself on-device (synthetic bytes → re-id + deauth_flood)
    assert checks["captap pipeline"].status == "PASS"
    assert "deauth_flood=fired" in checks["captap pipeline"].detail
    assert checks["sighting store"].status == "PASS"
    # nothing in the pure-software core should FAIL
    for name in ("detect engine", "config template+validate", "sighting store"):
        assert checks[name].status == "PASS", (name, checks[name].detail)


def test_selftest_vault_pass_or_skip_never_crashes():
    v = {c.name: c for c in run_selftest()}["vault (at-rest)"]
    assert v.status in ("PASS", "SKIP")   # PASS with crypto extra, honest SKIP without
