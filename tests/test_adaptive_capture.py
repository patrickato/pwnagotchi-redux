"""Adaptive capture-method selection — pick the method that works, per AP."""
from redux.core.experience import Experience
from redux.crack.adaptive_capture import (
    recommend_capture_method, method_table, CAPTURE_METHODS,
)


def _row(method, outcome, *, band=None, bssid=None):
    ctx = {"method": method}
    if band:
        ctx["band"] = band
    if bssid:
        ctx["bssid"] = bssid
    return Experience(ts=1.0, outcome=outcome, context=ctx)


def test_cold_start_uses_preferred_available():
    m, why = recommend_capture_method([], available=["deauth", "angryoxide"])
    assert m == "angryoxide"              # preferred order wins before any history
    assert "no capture history" in why


def test_cold_start_respects_whats_available():
    m, why = recommend_capture_method([], available=["deauth", "tcpdump-lock"])
    assert m == "deauth"                  # angryoxide not available → next preferred


def test_learns_the_winning_method():
    rows = ([_row("deauth", "none")] * 5 +      # deauth keeps failing
            [_row("pmkid", "handshake")] * 5)   # pmkid keeps working
    m, why = recommend_capture_method(rows, available=["deauth", "pmkid"])
    assert m == "pmkid" and "adaptive pick" in why


def test_untried_method_is_explored_not_starved():
    # deauth has a modest record; angryoxide is untried → UCB should surface it
    rows = [_row("deauth", "handshake"), _row("deauth", "none")]
    tbl = method_table(rows, available=["deauth", "angryoxide"])
    names = [r["method"] for r in tbl]
    assert set(names) == {"deauth", "angryoxide"}
    untried = next(r for r in tbl if r["method"] == "angryoxide")
    assert untried["trials"] == 0 and "explore" in untried["reason"]


def test_adapts_per_ap():
    ap = "18:a5:e9:65:34:d2"
    rows = ([_row("pmkid", "handshake", bssid=ap)] * 4 +
            [_row("deauth", "none", bssid=ap)] * 4 +
            [_row("deauth", "handshake")] * 10)   # other APs love deauth, but not THIS ap
    m, why = recommend_capture_method(rows, available=["pmkid", "deauth"], bssid=ap)
    assert m == "pmkid" and ap in why


def test_no_method_available_is_honest():
    m, why = recommend_capture_method([_row("pmkid", "handshake")], available=[])
    assert m is None and "no capture method available" in why
