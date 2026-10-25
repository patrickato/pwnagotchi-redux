"""Device re-identification across randomized MACs (pure engine + CLI).

MACs whose first octet has the 0x02 bit set are locally-administered (random):
fa/fe/aa/02 are randomized; 3c/b8/d4 are real OUI-assigned.
"""
from redux.geo.reid import reidentify, Entity


def _clients(rows):
    """rows: (mac, [ssids], probes) -> ProbeHarvester.clients() shape."""
    return [{"client": m, "ssids": list(s), "probes": p} for (m, s, p) in rows]


def test_links_two_randomized_macs_by_distinctive_ssid_not_ubiquitous_one():
    # A+B are one phone (share the rare "Bob-iPhone"); everyone shares xfinitywifi
    # but that must NOT fuse them, and SoloNet's owner stays its own identity.
    ents = reidentify(_clients([
        ("fa:11:22:33:44:01", ["Bob-iPhone", "HomeNet", "xfinitywifi"], 10),
        ("fe:55:66:77:88:02", ["Airport_Free", "Bob-iPhone", "xfinitywifi"], 8),
        ("3c:aa:bb:cc:dd:03", ["OfficeNet", "xfinitywifi"], 5),
        ("b8:11:22:33:44:04", ["GuestWiFi", "xfinitywifi"], 3),
        ("d4:11:22:33:44:05", ["Library", "xfinitywifi"], 2),
        ("aa:bb:cc:dd:ee:06", ["SoloNet"], 1),
    ]))
    assert len(ents) == 1
    e = ents[0]
    assert e.macs == ("fa:11:22:33:44:01", "fe:55:66:77:88:02")
    assert e.randomized == 2
    assert e.shared_ssids == ("Bob-iPhone",)       # ubiquitous SSID excluded as evidence
    assert "xfinitywifi" not in e.shared_ssids
    assert e.confidence == "strong"                # linked by an SSID only these two probe
    assert e.probes == 18


def test_shared_ubiquitous_ssid_alone_never_links():
    ents = reidentify(_clients([
        ("fa:00:00:00:00:01", ["attwifi", "Alpha"], 2),
        ("fe:00:00:00:00:02", ["attwifi", "Beta"], 2),
        ("aa:00:00:00:00:03", ["attwifi", "Gamma"], 2),
        ("02:00:00:00:00:04", ["attwifi", "Delta"], 2),
    ]))
    assert ents == []                              # attwifi is noise, uniques aren't shared


def test_medium_confidence_when_evidence_is_only_semi_distinctive():
    # three MACs share the same two SSIDs probed by all three (df=3): links, but
    # weaker evidence than an exclusive pair.
    ents = reidentify(_clients([
        ("fa:00:00:00:00:10", ["S1", "S2"], 3),
        ("fe:00:00:00:00:11", ["S1", "S2"], 3),
        ("02:00:00:00:00:12", ["S1", "S2"], 3),
    ]))
    assert len(ents) == 1
    assert ents[0].macs == ("02:00:00:00:00:12", "fa:00:00:00:00:10", "fe:00:00:00:00:11")
    assert ents[0].randomized == 3
    assert ents[0].confidence == "medium"


def test_no_shared_ssids_means_no_entity():
    ents = reidentify(_clients([
        ("fa:00:00:00:00:01", ["Home"], 1),
        ("fe:00:00:00:00:02", ["Work"], 1),
    ]))
    assert ents == []


def test_real_oui_macs_link_too_and_randomized_count_is_honest():
    # two globally-administered (real) MACs sharing a private SSID — same owner;
    # randomized count must be 0, not fabricated.
    ents = reidentify(_clients([
        ("3c:11:22:33:44:01", ["Patricks-Pixel", "corp-guest"], 4),
        ("b8:11:22:33:44:02", ["Patricks-Pixel", "hotel-wifi"], 4),
    ]))
    assert len(ents) == 1
    assert ents[0].randomized == 0
    assert ents[0].shared_ssids == ("Patricks-Pixel",)


def test_invalid_macs_and_empty_pnls_are_dropped_not_crashed():
    ents = reidentify(_clients([
        ("not-a-mac", ["Whatever"], 1),
        ("fa:00:00:00:00:01", [], 0),              # no probes -> no PNL
        ("fe:00:00:00:00:02", ["", "  "], 0),      # only blank SSIDs
        ("aa:00:00:00:00:03", ["Real"], 1),
    ]))
    assert ents == []                              # nothing valid left to link


def test_min_link_weight_knob_gates_borderline_evidence():
    rows = _clients([
        ("fa:00:00:00:00:01", ["OnlyUs"], 2),      # df(OnlyUs)=2 -> weight 0.5
        ("fe:00:00:00:00:02", ["OnlyUs"], 2),
    ])
    assert len(reidentify(rows, min_link_weight=0.5)) == 1   # meets threshold
    assert reidentify(rows, min_link_weight=0.6) == []       # raised above it -> no link


def test_single_client_returns_nothing():
    assert reidentify(_clients([("fa:00:00:00:00:01", ["X", "Y"], 3)])) == []
    assert reidentify([]) == []


# --- CLI wiring (`redux reid`) --------------------------------------------- #

def test_cmd_reid_human_output(tmp_path, capsys):
    from redux.cli import cmd_reid
    from redux.portal.karma import ProbeHarvester
    from types import SimpleNamespace
    store = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(store))
    for ssid in ("Bob-iPhone", "HomeNet"):
        h.note("fa:11:22:33:44:01", ssid)
    for ssid in ("Bob-iPhone", "CoffeeShop"):
        h.note("fe:55:66:77:88:02", ssid)
    args = SimpleNamespace(store=str(store), min_weight=0.5,
                           max_common_fraction=0.5, json=False)
    assert cmd_reid(args) == 0
    out = capsys.readouterr().out
    assert "re-identified device" in out
    assert "Bob-iPhone" in out and "randomized" in out


def test_cmd_reid_empty_store_is_honest(tmp_path, capsys):
    from redux.cli import cmd_reid
    from types import SimpleNamespace
    args = SimpleNamespace(store=str(tmp_path / "missing.json"), min_weight=0.5,
                           max_common_fraction=0.5, json=False)
    assert cmd_reid(args) == 0
    assert "no probes harvested" in capsys.readouterr().out


def test_cmd_reid_json_output(tmp_path, capsys):
    import json as _json
    from redux.cli import cmd_reid
    from redux.portal.karma import ProbeHarvester
    from types import SimpleNamespace
    store = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(store))
    for ssid in ("Bob-iPhone", "HomeNet"):
        h.note("fa:11:22:33:44:01", ssid)
    for ssid in ("Bob-iPhone", "CoffeeShop"):
        h.note("fe:55:66:77:88:02", ssid)
    args = SimpleNamespace(store=str(store), min_weight=0.5,
                           max_common_fraction=0.5, json=True)
    assert cmd_reid(args) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["randomized"] == 2
    assert payload[0]["shared_ssids"] == ["Bob-iPhone"]
