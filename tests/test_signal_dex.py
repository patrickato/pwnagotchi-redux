"""Signal Dex: one catalog across sightings + sub-GHz + IR (read-side, no fabrication)."""
from redux.geo import SightingStore, Sighting
from redux.sdr.subghz import SubGhzLibrary
from redux.ir import IRLibrary, IRCode
from redux.dex.signal_dex import build_signal_dex, DOMAINS


def _store():
    s = SightingStore(":memory:")
    s.insert(Sighting(kind="wifi", mac="b8:27:eb:11:22:33", ssid="pi-ap",
                      ts=1.0, first_seen=1.0, provenance="t"))
    s.insert(Sighting(kind="ble", mac="da:a1:19:44:55:66", ssid="",
                      ts=2.0, first_seen=2.0, provenance="t"))
    return s


def test_aggregates_all_sources(tmp_path):
    sg = SubGhzLibrary(tmp_path / "sg.json")
    sg.add("garage", 390e6, protocol="Generic")
    sg.add("carfob", 433.92e6, protocol="KeeLoq")       # rolling
    ir = IRLibrary(tmp_path / "ir.json")
    ir.add(IRCode("TV power", "NEC", "01", "02"))

    view = build_signal_dex(sighting_store=_store(), subghz_lib=sg, ir_lib=ir)
    assert view.summary["total"] == 5
    assert view.summary["by_domain"]["wifi"] == 1
    assert view.summary["by_domain"]["subghz"] == 2
    assert view.summary["by_domain"]["ir"] == 1
    assert view.summary["by_source"] == {"sightings": 2, "subghz": 2, "ir": 1}


def test_subghz_rolling_flagged_in_detail(tmp_path):
    sg = SubGhzLibrary(tmp_path / "sg.json")
    sg.add("carfob", 433.92e6, protocol="KeeLoq")
    view = build_signal_dex(subghz_lib=sg)
    e = view.by_domain("subghz")[0]
    assert "rolling" in e.detail and "433.92" in e.detail


def test_sightings_carry_vendor_enrichment():
    view = build_signal_dex(sighting_store=_store())
    pi = next(e for e in view.entries if e.identifier == "b8:27:eb:11:22:33")
    assert "RaspberryPi" in pi.detail            # OUI enrichment flows through
    rnd = next(e for e in view.entries if e.identifier == "da:a1:19:44:55:66")
    assert "randomized" in rnd.detail


def test_omitted_sources_are_absent():
    view = build_signal_dex()                    # nothing passed
    assert view.entries == [] and view.summary["total"] == 0


def test_entries_sorted_by_domain_order(tmp_path):
    sg = SubGhzLibrary(tmp_path / "sg.json"); sg.add("x", 390e6)
    ir = IRLibrary(tmp_path / "ir.json"); ir.add(IRCode("y"))
    view = build_signal_dex(sighting_store=_store(), subghz_lib=sg, ir_lib=ir)
    domains = [e.domain for e in view.entries]
    # wifi/ble (sightings) come before subghz which comes before ir
    assert domains.index("wifi") < domains.index("subghz") < domains.index("ir")
    assert all(d in DOMAINS for d in domains)
