"""Hardware-free tests for the wordlist registry (multi-list store + NAS pull)."""
from __future__ import annotations

from pathlib import Path

import pytest

from redux.crack import WordlistStore, resolve_wordlist, TIERS


def _write(p: Path, text: str) -> Path:
    p.write_text(text)
    return p


def test_register_measures_and_dedups_by_content(tmp_path):
    store = WordlistStore(tmp_path / "store")
    a = _write(tmp_path / "rockyou.txt", "password\n123456\n")
    wl, reason = store.register(a, tier="standard")
    assert wl is not None and wl.name == "rockyou.txt" and wl.size_bytes > 0
    assert "registered" in reason and wl.id in reason
    # same bytes under a different filename -> same id (dedup), no duplicate row
    b = _write(tmp_path / "copy.txt", "password\n123456\n")
    wl2, reason2 = store.register(b, name="copy", tier="fast")
    assert wl2.id == wl.id and "updated" in reason2
    assert len([w for w in store.list() if w.id == wl.id]) == 1


def test_register_rejects_empty_relative_and_bad_tier(tmp_path):
    store = WordlistStore(tmp_path / "store")
    empty = _write(tmp_path / "empty.txt", "")
    assert store.register(empty)[0] is None
    assert store.register(Path("relative.txt"))[0] is None
    good = _write(tmp_path / "w.txt", "x\n")
    assert store.register(good, tier="nope")[0] is None


def test_index_persists_across_reopen(tmp_path):
    base = tmp_path / "store"
    store = WordlistStore(base)
    store.register(_write(tmp_path / "w.txt", "a\nb\n"), name="mylist", tier="thorough")
    reopened = WordlistStore(base)
    got = reopened.get("mylist")
    assert got is not None and got.tier == "thorough"


def test_select_by_name_tier_and_default(tmp_path):
    store = WordlistStore(tmp_path / "store")
    store.register(_write(tmp_path / "small.txt", "a\n"), name="small", tier="fast")
    store.register(_write(tmp_path / "mid.txt", "a\nb\nc\n"), name="mid", tier="standard")
    big = _write(tmp_path / "big.txt", "a\nb\nc\nd\ne\nf\n")
    store.register(big, name="big", tier="thorough")
    # explicit name
    w, why = store.select(name="mid")
    assert w.name == "mid" and "by name" in why
    # tier picks the most thorough (largest) in that tier
    store.register(_write(tmp_path / "big2.txt", "a\nb\nc\nd\ne\nf\ng\nh\n"), name="big2", tier="thorough")
    w, why = store.select(tier="thorough")
    assert w.name == "big2" and "most thorough" in why
    # default picks the fastest/smallest tier first
    w, why = store.select()
    assert w.name == "small" and "fastest available default" in why


def test_select_fails_closed_when_empty_or_missing(tmp_path):
    store = WordlistStore(tmp_path / "store")
    w, why = store.select()
    assert w is None and "no registered wordlist" in why
    # register then delete the backing file -> selection skips it / reports missing
    p = _write(tmp_path / "gone.txt", "a\n")
    store.register(p, name="gone")
    p.unlink()
    w, why = store.select(name="gone")
    assert w is None and "missing" in why
    w, why = store.select()
    assert w is None and "no registered wordlist is present" in why


def test_pull_from_nas_uses_injected_fetcher(tmp_path):
    store = WordlistStore(tmp_path / "store")
    nas_src = _write(tmp_path / "nas_rockyou.txt", "pw1\npw2\npw3\n")

    def fake_fetcher(spec, dest):
        # stands in for an SMB/rsync pull off the QNAP
        Path(dest).write_bytes(Path(nas_src).read_bytes())

    wl, why = store.pull_from_nas("qnap://share/rockyou.txt", fake_fetcher,
                                  name="rockyou-nas", tier="thorough")
    assert wl is not None and wl.source.startswith("nas:")
    assert wl.tier == "thorough" and "registered" in why
    assert store.get("rockyou-nas") is not None


def test_pull_fails_closed_on_fetcher_error_or_empty(tmp_path):
    store = WordlistStore(tmp_path / "store")

    def boom(spec, dest):
        raise RuntimeError("share unreachable")

    wl, why = store.pull_from_nas("qnap://x", boom)
    assert wl is None and "failed" in why

    def writes_nothing(spec, dest):
        pass

    wl, why = store.pull_from_nas("qnap://y", writes_nothing)
    assert wl is None and "no usable file" in why
    assert store.list() == []


def test_verify_detects_drift(tmp_path):
    store = WordlistStore(tmp_path / "store")
    p = _write(tmp_path / "w.txt", "a\nb\n")
    store.register(p, name="w")
    assert store.verify() == []
    p.write_text("a\nb\nc\nCHANGED\n")  # tamper after registration
    problems = store.verify()
    assert len(problems) == 1 and "changed" in problems[0][1]


def test_resolve_wordlist_helper(tmp_path):
    base = tmp_path / "store"
    store = WordlistStore(base)
    store.register(_write(tmp_path / "w.txt", "a\n"), name="only", tier="fast")
    path, why = resolve_wordlist(base, name="only")
    assert path is not None and Path(path).is_file()
    path, why = resolve_wordlist(base, name="missing")
    assert path is None and "no wordlist named" in why


def test_tiers_are_ordered_fast_to_thorough():
    assert TIERS == ("fast", "standard", "thorough")


def test_audit_settings_resolve_wordlist_from_store(tmp_path):
    # the audit worker config can point at the registry + a name/tier instead of a
    # single path, and AuditSettings.load resolves it through the store.
    from redux.crack.audit import AuditSettings
    store = WordlistStore(tmp_path / "store")
    wl, _ = store.register(_write(tmp_path / "rockyou.txt", "a\nb\n"), name="rockyou", tier="standard")
    cfg_path = tmp_path / "pipeline.toml"
    cfg_path.write_text(
        f'[pipeline]\ndatabase = "/captures/jobs.db"\n'
        f'[audit]\nenabled = false\n'
        f'wordlist_dir = "{tmp_path / "store"}"\nwordlist_name = "rockyou"\n'
        f'scope_file = "/captures/scope.json"\nresults_dir = "/captures/audits"\n')
    settings = AuditSettings.load(cfg_path)
    assert str(settings.wordlist) == wl.path


def test_audit_settings_backward_compatible_single_path(tmp_path):
    # without a registry dir, the literal wordlist path is kept unchanged.
    from redux.crack.audit import AuditSettings
    cfg_path = tmp_path / "pipeline.toml"
    cfg_path.write_text(
        '[pipeline]\ndatabase = "/captures/jobs.db"\n'
        '[audit]\nenabled = false\nwordlist = "/captures/rockyou.txt"\n'
        'scope_file = "/captures/scope.json"\nresults_dir = "/captures/audits"\n')
    settings = AuditSettings.load(cfg_path)
    assert str(settings.wordlist) == "/captures/rockyou.txt"
