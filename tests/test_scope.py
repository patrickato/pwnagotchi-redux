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
