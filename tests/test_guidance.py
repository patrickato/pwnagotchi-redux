"""Guidance registry: the single source of in-product help / info-bubbles."""
from redux import guidance as G


def test_catalog_is_coherent():
    ids = [t.id for t in G.TOPICS]
    assert len(ids) == len(set(ids)), "duplicate topic id"
    for t in G.TOPICS:
        assert t.title and t.bubble and t.when and t.how
        assert len(t.bubble) <= 160, f"bubble too long for {t.id}"
        # related ids must resolve
        for r in t.related:
            assert G.get(r) is not None, f"{t.id} -> unknown related {r}"


def test_core_topics_present():
    for tid in ("getting-started", "lab-vs-field", "scope", "sdr", "crack",
                "wps", "karma", "tracker", "safety"):
        assert G.get(tid) is not None


def test_get_is_case_insensitive():
    assert G.get("LAB-VS-FIELD").id == "lab-vs-field"
    assert G.get("nope") is None


def test_bubble_helper():
    assert "FIELD" in G.bubble("lab-vs-field")
    assert G.bubble("nonexistent") == ""


def test_search_matches_fields():
    assert any(t.id == "tracker" for t in G.search("following"))
    assert any(t.id == "sdr" for t in G.search("pager"))
    assert G.search("") == list(G.TOPICS)


def test_honesty_sdr_waterfall_not_overpromised():
    # the SDR help must not claim a live waterfall exists (it doesn't yet)
    sdr = G.get("sdr")
    assert "waterfall" in sdr.how.lower() and "not built yet" in sdr.how.lower()


def test_safety_topic_states_the_contract():
    s = G.get("safety")
    assert "Scope decides WHERE" in s.how
    assert "jamming" in s.how.lower()
