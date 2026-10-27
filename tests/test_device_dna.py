"""Device DNA — behavioral re-identification past MAC randomization."""
from redux.dex.device_dna import (
    BehaviorTrace, DeviceDNA, device_dna, dna_similarity, DnaLinker,
)


def _trace(mac, **kw):
    return BehaviorTrace(mac=mac, **kw)


def test_dna_from_timing_and_structure():
    t = _trace("aa:bb:cc:dd:ee:01",
               timestamps=(0.0, 10.0, 20.0, 30.0),   # ~10s cadence
               rates=(1, 2, 5, 11), channels=(1, 6, 11), ie_hash="deadbeef")
    dna = device_dna(t)
    assert dna.cadence == 10.0 and dna.rates == (1, 2, 5, 11)
    assert dna.channels == frozenset({1, 6, 11}) and dna.ie_hash == "deadbeef"
    assert dna.strength > 0.8                 # many distinctive components


def test_thin_trace_is_low_strength():
    dna = device_dna(_trace("aa:bb:cc:dd:ee:02", channels=(6,)))
    assert dna.strength < 0.4                 # one weak signal


def test_similarity_dominated_by_ie_then_rates():
    a = device_dna(_trace("m1", ie_hash="abc", rates=(1, 2, 5), channels=(1, 6)))
    b = device_dna(_trace("m2", ie_hash="abc", rates=(1, 2, 5), channels=(1, 6)))
    sim, matched = dna_similarity(a, b)
    assert sim >= 0.8 and "ie" in matched and "rates" in matched


def test_timing_alone_cannot_carry_a_link():
    # same cadence only → below the link threshold (timing never links by itself)
    a = device_dna(_trace("m1", timestamps=(0.0, 10.0, 20.0)))
    b = device_dna(_trace("m2", timestamps=(0.0, 10.0, 20.0)))
    sim, matched = dna_similarity(a, b)
    assert matched == ["cadence"] and sim < 0.55


def test_linker_reidentifies_across_mac_rotation():
    # one device, two randomized MACs, same IE + rates + cadence → one identity
    common = dict(timestamps=(0.0, 10.0, 20.0, 30.0), rates=(1, 2, 5, 11),
                  channels=(1, 6, 11), ie_hash="cafebabe")
    link = DnaLinker()
    link.add(_trace("a2:11:11:11:11:11", **common))
    link.add(_trace("de:22:22:22:22:22", **common))
    ids = link.identities()
    reid = [d for d in ids if d.reidentified]
    assert len(reid) == 1
    assert set(reid[0].macs) == {"a2:11:11:11:11:11", "de:22:22:22:22:22"}
    assert "ie" in reid[0].matched_on


def test_linker_keeps_distinct_devices_apart():
    link = DnaLinker()
    link.add(_trace("m1", rates=(1, 2, 5, 11), ie_hash="aaa", channels=(1,)))
    link.add(_trace("m2", rates=(6, 9, 12, 54), ie_hash="bbb", channels=(36,)))
    assert link.summary()["devices"] == 2 and link.summary()["reidentified"] == 0


def test_weak_dna_is_never_fuzzy_merged():
    # two stable-looking devices with only a single shared channel must NOT merge
    link = DnaLinker()
    link.add(_trace("00:11:22:33:44:55", channels=(6,)))
    link.add(_trace("66:77:88:99:aa:bb", channels=(6,)))
    assert link.summary()["reidentified"] == 0


def test_summary_counts():
    common = dict(ie_hash="f00d", rates=(1, 2), channels=(1, 6))
    link = DnaLinker()
    link.add(_trace("a2:00:00:00:00:01", **common))
    link.add(_trace("de:00:00:00:00:02", **common))
    link.add(_trace("ff:ff:ff:ff:ff:ff", ie_hash="other", rates=(54,), channels=(36,)))
    s = link.summary()
    assert s["macs_seen"] == 3 and s["devices"] == 2 and s["reidentified"] == 1
