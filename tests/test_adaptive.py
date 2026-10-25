"""Adaptive unleashed — order targets by learned capture success."""
from types import SimpleNamespace

from redux.operator.adaptive import by_vendor_success
from redux.operator import autopilot
from redux.core.experience import Experience
from redux.core import Scope


def _rows():
    # GoodCo: 3/3 captures; BadCo: 0/3. (vendor is the learned dimension)
    rows = [Experience(ts=float(i), outcome="handshake", context={"vendor": "GoodCo"}) for i in range(3)]
    rows += [Experience(ts=float(i), outcome="none", context={"vendor": "BadCo"}) for i in range(3)]
    return rows


# map test BSSIDs to vendors
_VENDOR = {"aa:00": "BadCo", "bb:00": "GoodCo", "cc:00": "BadCo"}


def _lookup(mac):
    return _VENDOR.get(mac)


def test_vendor_success_orders_good_vendor_first():
    order = by_vendor_success(_rows(), _lookup)
    out = order(["aa:00", "bb:00", "cc:00"])      # BadCo, GoodCo, BadCo
    assert out[0] == "bb:00"                       # GoodCo (proven) jumps to the front


def test_ties_keep_original_order():
    order = by_vendor_success(_rows(), _lookup)
    out = order(["aa:00", "cc:00"])                # both BadCo -> stable
    assert out == ["aa:00", "cc:00"]


def test_unknown_vendor_is_explored_not_starved():
    # an untried vendor gets a UCB lift, so it isn't dead last behind a proven-bad one
    order = by_vendor_success(_rows(), lambda m: {"aa:00": "BadCo", "zz:00": "NewCo"}.get(m))
    out = order(["aa:00", "zz:00"])                # BadCo (0/3) vs NewCo (untried)
    assert out[0] == "zz:00"                        # explore the unknown over the proven-bad


def test_autopilot_applies_prioritize_each_cycle():
    s = Scope(lab_mode=True); s.add("bb:00", "bssid", job="cage")
    ok = lambda name: (lambda t: (True, f"{name} {t}"))
    from redux.operator.operator import Phase
    execs = {p: ok("x") for p in Phase if p is not Phase.RECON}
    seen = []
    def src():
        return ["aa:00", "bb:00"]
    def prio(targets):
        seen.append(list(targets))
        return list(reversed(targets))
    rep = autopilot(s, target_source=src, executors=execs, confirmed=True, prioritize=prio)
    assert rep.ran and seen == [["aa:00", "bb:00"]]   # prioritize was applied to the cycle


def test_autopilot_without_prioritize_is_unchanged():
    s = Scope(lab_mode=True)
    from redux.operator.operator import Phase
    execs = {p: (lambda t: (True, "x")) for p in Phase if p is not Phase.RECON}
    rep = autopilot(s, target_source=lambda: ["aa:00"], executors=execs, confirmed=True)
    assert rep.ran and rep.cycles[0].targets == 1
