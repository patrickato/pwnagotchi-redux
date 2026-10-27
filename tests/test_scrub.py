"""Secure-wipe / panic-scrub — device's own data, brain preserved on scrub."""
import os

import pytest

from redux.core.scrub import SecureWipe, SCRUB_TIERS, PANIC_TIERS


def _seed(root):
    """Lay down a realistic captures tree."""
    def w(rel, data=b"x"):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    w("ready/handshake.hc22000")
    w("loot/files/host_secrets.env")
    w("loot/vault.json")
    w("live/pmkid.json")
    w("live/experience.jsonl", b"learning")
    w("wordlists/grown.txt")
    w("live/sightings.db")
    w("config/scope.json")
    w("config/oui.tsv")
    return root


def _wipe(tmp_path):
    root = _seed(str(tmp_path / "captures"))
    return SecureWipe(root=root)


def test_refuses_non_captures_root(tmp_path):
    sw = SecureWipe(root=str(tmp_path / "home"))
    with pytest.raises(ValueError):
        sw.execute("panic", apply=True)


def test_scrub_wipes_raw_keeps_brain(tmp_path):
    sw = _wipe(tmp_path)
    plan = sw.plan("scrub")
    wiped = {os.path.basename(i.path) for i in plan["wipe"] if i.exists}
    kept = {os.path.basename(i.path) for i in plan["keep"] if i.exists}
    assert "ready" in wiped and "vault.json" in wiped and "pmkid.json" in wiped
    # the brain + geo + target list are kept on a surgical scrub
    assert "experience.jsonl" in kept and "wordlists" in kept
    assert "sightings.db" in kept and "scope.json" in kept


def test_panic_also_wipes_brain_geo_and_targetlist(tmp_path):
    sw = _wipe(tmp_path)
    plan = sw.plan("panic")
    wiped_tiers = {i.tier for i in plan["wipe"] if i.exists}
    assert "brain" in wiped_tiers and "geo" in wiped_tiers and "config-sensitive" in wiped_tiers
    # ordinary config dir still kept so the box boots
    kept = {os.path.basename(i.path) for i in plan["keep"] if i.exists}
    assert "config" in kept


def test_dry_run_is_default_and_touches_nothing(tmp_path):
    sw = _wipe(tmp_path)
    res = sw.execute("panic")                       # no apply
    assert res["applied"] is False and "DRY RUN" in res["note"]
    assert os.path.exists(os.path.join(sw.root, "loot/vault.json"))   # still there


def test_apply_scrub_removes_raw_keeps_brain(tmp_path):
    sw = _wipe(tmp_path)
    res = sw.execute("scrub", apply=True)
    assert res["applied"] and res["wiped"] >= 3
    assert not os.path.exists(os.path.join(sw.root, "loot/vault.json"))     # raw gone
    assert not os.path.exists(os.path.join(sw.root, "live/pmkid.json"))     # raw gone
    assert os.path.exists(os.path.join(sw.root, "live/experience.jsonl"))   # BRAIN kept
    assert os.path.exists(os.path.join(sw.root, "live/sightings.db"))       # geo kept on scrub


def test_apply_panic_removes_brain_too(tmp_path):
    sw = _wipe(tmp_path)
    sw.execute("panic", apply=True)
    assert not os.path.exists(os.path.join(sw.root, "live/experience.jsonl"))  # brain gone
    assert not os.path.exists(os.path.join(sw.root, "config/scope.json"))      # target list gone
    assert os.path.exists(os.path.join(sw.root, "config/oui.tsv"))             # config kept → boots


def test_overwrite_before_unlink(tmp_path):
    sw = _wipe(tmp_path)
    overwritten = []
    sw.execute("scrub", apply=True, overwriter=lambda p: overwritten.append(p),
               remover=lambda p: os.remove(p))
    assert any("vault.json" in p for p in overwritten)     # secret was overwritten first


def test_exposure_report(tmp_path):
    sw = _wipe(tmp_path)
    exp = sw.exposure()
    assert exp["raw_sensitive_items"] >= 3
    assert "raw-sensitive" in exp["note"]


def test_scrub_and_panic_tier_sets():
    assert SCRUB_TIERS == {"raw"}
    assert {"raw", "brain", "geo"} <= PANIC_TIERS
