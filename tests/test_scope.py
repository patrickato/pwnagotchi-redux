"""Central Scope — the one authorized-target list the firing gate consults."""
import time

from redux.core import Scope, ScopeEntry, classify_target
from redux.engine import BettercapDriver, ReplayTransport, FiringRefused


def test_empty_by_default_refuses():
    s = Scope()
    assert s.empty is True
    ok, reason = s.authorize(bssid="aa:bb:cc:dd:ee:ff")
    assert ok is False and "empty" in reason


def test_add_and_permit_bssid_and_ssid():
    s = Scope()
    s.add("AA:BB:CC:DD:EE:FF", label="my AP")
    s.add("MyLab", "ssid")
    assert s.empty is False
    assert s.permits(bssid="aa:bb:cc:dd:ee:ff") is True      # normalized/case-insensitive
    assert s.permits(ssid="MyLab") is True
    assert s.permits(ssid="mylab") is False                  # SSIDs are case-sensitive
    assert s.permits(bssid="11:22:33:44:55:66") is False


def test_classify_target_autodetects_kind():
    assert classify_target("aa:bb:cc:dd:ee:ff") == "bssid"
    assert classify_target("de-ad-be-ef-00-01") == "bssid"
    assert classify_target("192.168.1.0/24") == "cidr"
    assert classify_target("10.0.0.5") == "cidr"
    assert classify_target("CoffeeShopWiFi") == "ssid"


def test_cidr_authorization_for_network_layer():
    s = Scope()
    s.add("192.168.50.0/24", job="acme")
    assert s.permits(ip="192.168.50.17") is True
    assert s.permits(ip="192.168.51.17") is False


def test_expiry_lapses_on_its_own():
    s = Scope()
    now = 1000.0
    s.add("aa:bb:cc:dd:ee:ff", expires=now + 100, now=now)
    assert s.permits(bssid="aa:bb:cc:dd:ee:ff", now=now + 50) is True    # within window
    assert s.permits(bssid="aa:bb:cc:dd:ee:ff", now=now + 200) is False  # lapsed
    # and an all-expired scope reads as empty
    assert Scope(entries=[ScopeEntry("bssid", "aa:bb:cc:dd:ee:ff", expires=now, added=0.0)]).active_entries(now=now + 1) == []


def test_named_jobs_scope_and_clear_as_a_unit():
    s = Scope()
    s.add("aa:bb:cc:dd:ee:01", job="acme")
    s.add("aa:bb:cc:dd:ee:02", job="globex")
    assert sorted(s.jobs()) == ["acme", "globex"]
    # job-scoped authorization
    assert s.permits(bssid="aa:bb:cc:dd:ee:01", job="acme") is True
    assert s.permits(bssid="aa:bb:cc:dd:ee:01", job="globex") is False
    # clearing one job leaves the other
    removed = s.clear(job="acme")
    assert removed == 1 and s.jobs() == ["globex"]


def test_bulk_load_pasted_list():
    s = Scope()
    text = """
    # my engagement targets
    AA:BB:CC:DD:EE:01
    AA:BB:CC:DD:EE:02
    192.168.9.0/24
    TargetCorpWiFi
    """
    n = s.bulk_load(text, job="engagement-7")
    assert n == 4
    assert s.permits(bssid="aa:bb:cc:dd:ee:01", job="engagement-7") is True
    assert s.permits(ip="192.168.9.42", job="engagement-7") is True
    assert s.permits(ssid="TargetCorpWiFi", job="engagement-7") is True


def test_arm_lab_pre_authorizes_own_gear():
    s = Scope()
    n = s.arm_lab(bssids=["aa:bb:cc:dd:ee:ff"], ssids=["HomeLab"], cidrs=["10.0.0.0/24"])
    assert n == 3
    assert s.permits(bssid="aa:bb:cc:dd:ee:ff") is True
    assert s.permits(ip="10.0.0.9") is True
    # lab entries never expire
    assert all(e.expires is None for e in s.entries)


def test_remove_and_dedupe():
    s = Scope()
    s.add("aa:bb:cc:dd:ee:ff", label="first")
    s.add("aa:bb:cc:dd:ee:ff", label="second")      # same target → replace, not stack
    assert len(s.entries) == 1 and s.entries[0].label == "second"
    assert s.remove("AA:BB:CC:DD:EE:FF") == 1
    assert s.empty is True


def test_persistence_roundtrip_atomic(tmp_path):
    p = str(tmp_path / "scope.json")
    s = Scope(path=p)
    s.add("aa:bb:cc:dd:ee:ff", job="acme")
    s.add("192.168.1.0/24", job="acme")
    s.save()
    again = Scope.load(p)
    assert again.permits(bssid="aa:bb:cc:dd:ee:ff") is True
    assert again.permits(ip="192.168.1.5") is True
    assert again.jobs() == ["acme"]


def test_load_missing_file_is_empty_not_crash(tmp_path):
    s = Scope.load(str(tmp_path / "nope.json"))
    assert s.empty is True and s.entries == []


def test_scope_is_drop_in_for_the_firing_gate():
    # the bettercap driver's gate only needs .empty + .permits(bssid=, ssid=)
    s = Scope()
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=s)
    # empty scope -> refuses
    try:
        drv.deauth("aa:bb:cc:dd:ee:ff")
        assert False, "expected FiringRefused on empty scope"
    except FiringRefused:
        pass
    # arm it -> fires
    s.add("aa:bb:cc:dd:ee:ff", label="authorized AP")
    out = drv.deauth("aa:bb:cc:dd:ee:ff")
    assert out  # transport ran the command


def test_save_creates_missing_parent_dir(tmp_path):
    """Turnkey: a first-ever `scope add` must not fail just because the scope
    directory (e.g. /etc/pwnagotchi) has never been created. save() makes it."""
    nested = tmp_path / "etc" / "pwnagotchi" / "scope.json"
    assert not nested.parent.exists()
    s = Scope()
    s.add("AA:BB:CC:DD:EE:FF", label="my AP")
    out = s.save(str(nested))
    assert out == str(nested)
    assert nested.is_file()
    # and it round-trips
    reloaded = Scope.load(str(nested))
    assert reloaded.permits(bssid="aa:bb:cc:dd:ee:ff") is True


def test_save_hardens_permissions(tmp_path):
    """The firing-authorization file must be owner-only (0600) in an owner-only
    dir (0700) — it lives on a shared partition on the device."""
    import os, stat
    p = tmp_path / "cfg" / "scope.json"
    s = Scope()
    s.add("AA:BB:CC:DD:EE:FF")
    s.save(str(p))
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(p.parent).st_mode) == 0o700


def test_save_refuses_symlinked_temp(tmp_path):
    """A pre-planted scope.json.tmp symlink must not be followed (no redirecting
    the write onto another file)."""
    import os
    victim = tmp_path / "victim"
    victim.write_text("precious")
    p = tmp_path / "scope.json"
    os.symlink(str(victim), str(p) + ".tmp")
    Scope().save(str(p))                       # must not clobber the symlink target
    assert victim.read_text() == "precious"
    assert p.is_file()


def test_default_scope_path_prefers_captures_mount(monkeypatch):
    import redux.core.scope as sc
    monkeypatch.delenv("REDUX_SCOPE", raising=False)
    monkeypatch.setattr(sc.os.path, "ismount", lambda p: p == "/captures")
    assert sc.default_scope_path() == "/captures/config/scope.json"
    monkeypatch.setattr(sc.os.path, "ismount", lambda p: False)
    assert sc.default_scope_path() == "/etc/pwnagotchi/scope.json"
    monkeypatch.setenv("REDUX_SCOPE", "/tmp/custom.json")
    assert sc.default_scope_path() == "/tmp/custom.json"


# --- F04: a malformed scope must fail CLOSED, never open --------------------- #

def test_scope_load_fails_closed_on_malformed(tmp_path):
    import json
    from redux.core.scope import Scope

    def _load(obj_or_text):
        p = tmp_path / "scope.json"
        p.write_text(obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text))
        return Scope.load(str(p))

    # a top-level JSON array/string/number is not a scope → empty, never a crash
    assert _load("[]").empty is True
    assert _load('"nope"').empty is True
    assert _load("42").empty is True

    # the STRING "false" (and other truthy-but-not-True values) must NOT arm lab:
    # bool("false") is True / bool(1) is True — only a real JSON boolean true counts
    for val in ("false", "true", "yes", 1, 0, None, [], {}):
        assert _load({"lab_mode": val}).lab_mode is False, f"{val!r} must not arm lab"

    # a real boolean true still enables lab mode
    assert _load({"lab_mode": True}).lab_mode is True

    # a malformed entries container is tolerated (empty), not fatal, and does not
    # drag lab_mode down with it
    s = _load({"entries": "nope", "lab_mode": True})
    assert s.lab_mode is True and list(s.entries) == []

    # non-dict rows inside entries are skipped, valid rows kept
    s2 = _load({"entries": ["garbage", {"kind": "bssid", "value": "aa:bb:cc:dd:ee:ff"}]})
    assert [e.value for e in s2.entries] == ["aa:bb:cc:dd:ee:ff"]
