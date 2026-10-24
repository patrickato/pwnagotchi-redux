"""Experience layer: real-outcome recording, honest stats, exploration-aware rank."""
from redux.core.experience import (
    Experience, ExperienceStore, aggregate, rank, SUCCESS_OUTCOMES, DIMENSIONS,
)


def _rows(spec):
    """spec: list of (outcome, context) -> Experience list with ts increasing."""
    return [Experience(ts=1000.0 + i, outcome=o, context=c) for i, (o, c) in enumerate(spec)]


# ---- success semantics --------------------------------------------------- #

def test_success_outcomes():
    assert Experience(0, "handshake", {}).success
    assert Experience(0, "pmkid", {}).success
    assert not Experience(0, "none", {}).success
    assert not Experience(0, "miss", {}).success


# ---- aggregate ----------------------------------------------------------- #

def test_aggregate_counts_successes_and_trials():
    rows = _rows([
        ("handshake", {"channel": 6}),
        ("none", {"channel": 6}),
        ("pmkid", {"channel": 6}),
        ("none", {"channel": 11}),
    ])
    agg = aggregate(rows, "channel")
    assert agg[6] == (2, 3)     # 2 successes of 3 trials
    assert agg[11] == (0, 1)


def test_aggregate_rejects_unknown_dimension():
    try:
        aggregate([], "nonsense")
        assert False
    except ValueError:
        pass


def test_aggregate_ignores_missing_dimension_value():
    rows = _rows([("handshake", {"band": "2.4"}), ("none", {})])
    assert aggregate(rows, "band") == {"2.4": (1, 1)}


# ---- rank: honesty + exploration ---------------------------------------- #

def test_rank_orders_by_score_and_reports_samples():
    rows = _rows(
        [("handshake", {"channel": 6})] * 8 + [("none", {"channel": 6})] * 2 +   # 8/10
        [("handshake", {"channel": 1})] * 1 + [("none", {"channel": 1})] * 9      # 1/10
    )
    stats = rank(rows, "channel")
    by = {s.value: s for s in stats}
    assert by[6].successes == 8 and by[6].trials == 10
    assert by[1].successes == 1 and by[1].trials == 10
    assert by[6].score > by[1].score            # the productive channel ranks higher
    assert "8/10" in by[6].reason               # sample size surfaced honestly


def test_rank_explores_untried_candidate():
    # ch6 has a mediocre record; ch11 is untried -> UCB must not starve ch11
    rows = _rows([("none", {"channel": 6})] * 20 + [("handshake", {"channel": 6})] * 2)
    stats = rank(rows, "channel", candidates=[6, 11])
    by = {s.value: s for s in stats}
    assert by[11].trials == 0 and "untried" in by[11].reason
    assert by[11].score > by[6].score           # exploration lifts the untried option


def test_rank_untried_uses_prior_not_zero():
    stats = rank([], "channel", candidates=[6])
    assert stats[0].smoothed == 0.5             # Beta(1,1) prior, not a fabricated 0 or 1


def test_confidence_labels_scale_with_samples():
    rows = _rows([("handshake", {"hour": 9})] * 1)
    s = rank(rows, "hour")[0]
    assert "very low confidence" in s.reason    # 1 trial is honestly flagged


# ---- store --------------------------------------------------------------- #

def test_store_record_and_load(tmp_path):
    st = ExperienceStore(tmp_path / "exp.jsonl", now=lambda: 1700000000.0)
    st.record("handshake", {"channel": 6, "band": "2.4"})
    st.record("none", {"channel": 6, "band": "2.4"})
    rows = st.load()
    assert len(rows) == 2 and rows[0].success and not rows[1].success
    # hour is auto-derived from ts
    assert "hour" in rows[0].context


def test_store_stats_and_summary(tmp_path):
    st = ExperienceStore(tmp_path / "exp.jsonl")
    for _ in range(3):
        st.record("handshake", {"channel": 6})
    st.record("none", {"channel": 6})
    summ = st.summary()
    assert summ["total"] == 4 and summ["successes"] == 3
    assert summ["by_outcome"]["handshake"] == 3
    ch = {s.value: s for s in st.stats("channel")}
    assert ch[6].successes == 3 and ch[6].trials == 4


def test_store_prunes_to_max(tmp_path):
    st = ExperienceStore(tmp_path / "exp.jsonl", max_rows=5)
    for i in range(12):
        st.record("none", {"channel": i})
    assert len(st.load()) == 5                  # bounded


def test_store_reset(tmp_path):
    st = ExperienceStore(tmp_path / "exp.jsonl")
    st.record("handshake", {"channel": 1})
    assert "cleared" in st.reset()
    assert st.load() == []
    assert "no experience" in st.reset()


def test_store_tolerates_corrupt_lines(tmp_path):
    p = tmp_path / "exp.jsonl"
    p.write_text('{"ts": 1, "outcome": "handshake", "context": {}}\nNOT JSON\n\n')
    assert len(ExperienceStore(p).load()) == 1   # bad line skipped, good one kept
