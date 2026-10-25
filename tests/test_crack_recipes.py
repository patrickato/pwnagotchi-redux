"""Guided any-hash cracker: catalog, identify, plan, suggest, and the gated runner."""
import os
from pathlib import Path

import pytest

from redux.crack import recipes as R
from redux.crack.anycrack import CrackSettings, authorize, run_plan


# ---- catalog ------------------------------------------------------------- #

def test_catalog_is_sane_and_unique():
    slugs = [r.name for r in R.RECIPES]
    assert len(slugs) == len(set(slugs)), "duplicate slug"
    # wifi is still here (we didn't drop what the pipeline does)
    assert R.by_name("wifi").mode == 22000
    for r in R.RECIPES:
        assert r.category in R.CATEGORIES
        assert r.attack in R.ATTACKS


def test_by_name_accepts_slug_and_bare_mode():
    assert R.by_name("ntlm").mode == 1000
    assert R.by_name("1000").name == "ntlm"       # bare -m number
    assert R.by_name("nope") is None
    assert R.by_name("") is None


def test_find_searches_multiple_fields():
    assert any(r.name == "ntlm" for r in R.find("windows"))
    assert any(r.mode == 22000 for r in R.find("22000"))
    assert R.find("") == list(R.RECIPES)           # empty -> whole catalog


# ---- identify ------------------------------------------------------------ #

@pytest.mark.parametrize("sample,expected", [
    ("$6$abc$def", "linux-shadow"),
    ("$2y$10$abcdefghijklmnopqrstuv", "bcrypt"),
    ("$1$abc$def", "md5crypt"),
    ("$P$Babcdefghijklmnopqrst", "phpass"),
    ("$krb5tgs$23$*user$REALM*...", "kerberoast"),
    ("$krb5asrep$23$user@REALM:aaa", "asreproast"),
    ("$7z$0$...", "7zip"),
    ("$office$*2013*...", "office2013"),
])
def test_identify_prefixes(sample, expected):
    cands = R.identify(sample)
    assert cands and cands[0].name == expected


def test_identify_32hex_is_ambiguous_ntlm_then_md5():
    cands = R.identify("b4b9b02e6f09a389e6b57a1e6b57a1e6")
    names = [c.name for c in cands]
    assert names[:2] == ["ntlm", "md5"]            # NTLM first on AD jobs, MD5 offered too


def test_identify_hex_lengths():
    assert R.identify("a" * 40)[0].name == "sha1"
    assert R.identify("a" * 64)[0].name == "sha256"
    assert R.identify("a" * 128)[0].name == "sha512"


def test_identify_netntlmv2_shape():
    s = "admin::CORP:1122334455667788:aabbccddeeff:0101000000000000"
    assert any(c.name == "ntlmv2" for c in R.identify(s))


def test_identify_empty_is_empty():
    assert R.identify("") == []
    assert R.identify("   ") == []


# ---- suggest (the smarts) ------------------------------------------------ #

def test_suggest_fast_hash_uses_rules():
    s = R.suggest(R.by_name("ntlm"))
    assert s.attack == 0 and s.use_rules and s.rules == "best64"


def test_suggest_slow_hash_declines_rules():
    s = R.suggest(R.by_name("bcrypt"))
    assert s.attack == 0 and not s.use_rules
    assert "slow" in s.reason.lower()


# ---- rule / mask resolution --------------------------------------------- #

def test_resolve_rules_preset_found(tmp_path):
    rf = tmp_path / "best64.rule"
    rf.write_text("x")
    path, why = R.resolve_rules("best64", dirs=[str(tmp_path)])
    assert path == str(rf) and "best64" in why


def test_resolve_rules_missing_is_soft_refusal():
    path, why = R.resolve_rules("best64", dirs=["/nonexistent"])
    assert path is None and "not found" in why


def test_resolve_mask_preset_and_raw():
    assert R.resolve_mask("digits8")[0] == "?d?d?d?d?d?d?d?d"
    assert R.resolve_mask("?l?l?d")[0] == "?l?l?d"
    assert R.resolve_mask("notamask")[0] is None


# ---- plan ---------------------------------------------------------------- #

def test_plan_straight_with_rules(tmp_path):
    wl = tmp_path / "rockyou.txt"; wl.write_text("password\n")
    p = R.plan(R.by_name("ntlm"), "/abs/hashes.txt", attack=0,
               wordlist=str(wl), rules="best64",
               rule_exists=lambda _p: True)
    assert p.ok
    assert "-m" in p.argv and "1000" in p.argv
    assert "-a" in p.argv and "0" in p.argv
    assert str(wl) in p.argv
    assert "-r" in p.argv                          # rules wired in


def test_plan_straight_missing_wordlist_fails_closed():
    p = R.plan(R.by_name("ntlm"), "/abs/h.txt", attack=0, wordlist=None)
    assert not p.ok and "wordlist" in p.explanation
    assert p.argv == []                            # no runnable command emitted


def test_plan_mask_attack(tmp_path):
    p = R.plan(R.by_name("wifi"), "/abs/h.22000", attack=3, mask="digits8")
    assert p.ok and "?d?d?d?d?d?d?d?d" in p.argv
    assert "-a" in p.argv and "3" in p.argv


def test_plan_mask_without_mask_fails():
    p = R.plan(R.by_name("wifi"), "/abs/h.22000", attack=3, mask=None)
    assert not p.ok and "mask" in p.explanation


def test_plan_requires_absolute_hash_path(tmp_path):
    wl = tmp_path / "w"; wl.write_text("a\n")
    p = R.plan(R.by_name("md5"), "relative.txt", attack=0, wordlist=str(wl))
    assert not p.ok and "absolute" in p.explanation


def test_plan_missing_rule_file_drops_rules_but_still_runs(tmp_path):
    wl = tmp_path / "w"; wl.write_text("a\n")
    p = R.plan(R.by_name("ntlm"), "/abs/h.txt", attack=0, wordlist=str(wl),
               rules="best64", rule_exists=lambda _p: False)
    assert p.ok                                    # soft: run continues
    assert "-r" not in p.argv
    assert "without rules" in p.explanation


# ---- authorize gate ------------------------------------------------------ #

def test_authorize_lab_unlocks():
    ok, why = authorize(authorized=False, lab_mode=True)
    assert ok and "LAB" in why


def test_authorize_field_needs_flag():
    ok, why = authorize(authorized=False, lab_mode=False)
    assert not ok and "authorized" in why
    ok2, _ = authorize(authorized=True, lab_mode=False)
    assert ok2


# ---- run_plan (injected runner, no real hashcat) ------------------------- #

class _FakeProc:
    def __init__(self, rc): self.returncode = rc


def _runner_factory(rc, *, write_result=False):
    def _run(argv, **kw):
        if write_result:
            # emulate hashcat writing the outfile
            idx = argv.index("--outfile")
            Path(argv[idx + 1]).write_text("hash:cracked\n")
        return _FakeProc(rc)
    return _run


def _plan(tmp_path):
    wl = tmp_path / "w"; wl.write_text("a\n")
    return R.plan(R.by_name("ntlm"), "/abs/h.txt", attack=0, wordlist=str(wl))


def test_run_refuses_in_field_without_authorization(tmp_path):
    s = CrackSettings(results_dir=tmp_path / "out")
    res = run_plan(_plan(tmp_path), s, authorized=False, lab_mode=False,
                   runner=_runner_factory(0))
    assert res["status"] == "refused" and "FIELD" in res["reason"]


def test_run_lab_recovers_and_writes_private(tmp_path):
    s = CrackSettings(results_dir=tmp_path / "out")
    res = run_plan(_plan(tmp_path), s, lab_mode=True,
                   runner=_runner_factory(0, write_result=True))
    assert res["status"] == "recovered"
    rp = Path(res["result"])
    assert rp.is_file()
    assert (rp.stat().st_mode & 0o777) == 0o600   # private result


def test_run_exhausted_and_completed(tmp_path):
    s = CrackSettings(results_dir=tmp_path / "out")
    assert run_plan(_plan(tmp_path), s, authorized=True,
                    runner=_runner_factory(1))["status"] == "exhausted"
    assert run_plan(_plan(tmp_path), s, authorized=True,
                    runner=_runner_factory(0))["status"] == "completed"


def test_run_refuses_a_broken_plan(tmp_path):
    s = CrackSettings(results_dir=tmp_path / "out")
    bad = R.plan(R.by_name("ntlm"), "/abs/h.txt", attack=0, wordlist=None)
    res = run_plan(bad, s, lab_mode=True, runner=_runner_factory(0))
    assert res["status"] == "refused"
