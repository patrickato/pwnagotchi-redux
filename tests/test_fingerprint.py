"""Device fingerprinting — identity across MAC randomization.

Pins the win (two MACs → one device via a shared PNL/IE) and, just as important,
the false-link guards: OUI never links two MACs, and no MAC-independent signal
means NOT linkable (each random MAC stays its own ephemeral identity).
"""
from redux.dex import (
    DeviceObservation, DeviceLinker, fingerprint, is_randomized, link_store,
)
from redux.geo import SightingStore, Sighting


# --- MAC randomization detection -------------------------------------------- #

def test_is_randomized_reads_the_laa_bit():
    assert is_randomized("a2:aa:bb:cc:dd:ee") is True      # 0xa2 has 0x02 set
    assert is_randomized("dc:a6:32:11:22:33") is False     # real Pi OUI, LAA clear
    assert is_randomized("garbage") is True                # unparseable → don't trust


# --- the headline: re-identification across MAC rotation -------------------- #

def test_shared_pnl_links_two_different_macs_into_one_device():
    pnl = frozenset({"HomeNet", "CoffeeShop", "Airport_Free_WiFi"})
    a = DeviceObservation(mac="a2:11:11:11:11:11", ssids=pnl, ts=100.0)
    b = DeviceObservation(mac="de:22:22:22:22:22", ssids=pnl, ts=200.0)   # different (randomized) MAC
    linker = DeviceLinker()
    i1 = linker.observe(a)
    i2 = linker.observe(b)
    assert i1.key == i2.key                     # same device
    ident = linker.identities()[0]
    assert ident.mac_count == 2 and ident.reidentified is True
    assert ident.first_seen == 100.0 and ident.last_seen == 200.0


def test_ie_template_links_even_with_empty_pnl():
    a = DeviceObservation(mac="a2:00:00:00:00:01", ie_hash="deadbeef", ts=1.0)
    b = DeviceObservation(mac="fe:00:00:00:00:02", ie_hash="deadbeef", ts=2.0)
    linker = DeviceLinker()
    linker.observe(a); linker.observe(b)
    assert linker.identities()[0].reidentified is True


def test_richer_pnl_is_stronger():
    weak = fingerprint(DeviceObservation(mac="a2:0:0:0:0:1", ssids=frozenset({"X"})))
    strong = fingerprint(DeviceObservation(
        mac="a2:0:0:0:0:1", ssids=frozenset({"A", "B", "C", "D"})))
    assert strong.strength > weak.strength
    assert weak.linkable and strong.linkable


# --- the false-link guards (the part that must not over-claim) -------------- #

def test_oui_alone_never_links_two_macs():
    # same vendor OUI, non-randomized, but NO pnl/ie → must stay two identities
    a = DeviceObservation(mac="dc:a6:32:11:11:11")
    b = DeviceObservation(mac="dc:a6:32:22:22:22")
    linker = DeviceLinker()
    linker.observe(a); linker.observe(b)
    assert len({i.key for i in linker.identities()}) == 2
    assert all(not i.reidentified for i in linker.identities())


def test_randomized_mac_without_signal_is_not_linkable():
    fp = fingerprint(DeviceObservation(mac="a2:ab:cd:ef:00:01"))
    assert fp.linkable is False and fp.strength == 0.0
    assert "cannot link" in fp.reason


def test_two_bare_randomized_macs_do_not_collapse():
    linker = DeviceLinker()
    linker.observe(DeviceObservation(mac="a2:00:00:00:00:01"))
    linker.observe(DeviceObservation(mac="b6:00:00:00:00:02"))
    assert linker.summary()["reidentified"] == 0
    assert linker.summary()["identities"] == 2


def test_stable_mac_tracked_but_not_linked():
    fp = fingerprint(DeviceObservation(mac="dc:a6:32:11:22:33"))
    assert fp.linkable is False and fp.key == "mac:dc:a6:32:11:22:33"
    assert "non-randomized" in fp.reason


# --- summary math ------------------------------------------------------------ #

def test_summary_counts_reid_and_savings():
    pnl = frozenset({"N1", "N2"})
    linker = DeviceLinker()
    for mac in ("a2:0:0:0:0:1", "de:0:0:0:0:2", "fa:0:0:0:0:3"):   # 3 MACs, one device
        linker.observe(DeviceObservation(mac=mac, ssids=pnl, ts=1.0))
    linker.observe(DeviceObservation(mac="b6:0:0:0:0:9"))           # a lone unlinkable one
    s = linker.summary()
    assert s["reidentified"] == 1 and s["macs_collapsed"] == 3
    assert s["reidentified_mac_savings"] == 2       # 3 MACs folded into 1 device


# --- store adapter ----------------------------------------------------------- #

def test_link_store_groups_by_mac_from_sightings():
    store = SightingStore()
    # two randomized MACs both seen against the same network name. (The store
    # dedups to one row per (kind,mac), so the best-effort adapter gets one SSID
    # per MAC — a weak-but-real link; rich PNL needs the raw-frame tap.)
    store.insert_many([
        Sighting(kind="wifi", mac="a2:aa:aa:aa:aa:aa", ssid="HomeNet", ts=10.0, provenance="test"),
        Sighting(kind="wifi", mac="de:bb:bb:bb:bb:bb", ssid="HomeNet", ts=11.0, provenance="test"),
    ])
    linker = link_store(store)
    assert linker.summary()["reidentified"] == 1
