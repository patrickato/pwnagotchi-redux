"""The AlertBus is now wired into the supervisor: near-duplicate alerts are deduped."""
from redux.core.supervisor import Supervisor
from redux.detect.alerts import Alert, AlertKind


class _Drv:
    def set_interface(self, iface): pass
    def recon(self, on=True): pass
    def poll_events(self, clear=True): return []


class _DupEngine:
    """Returns the SAME alert twice per pump — the bus should suppress the dup."""
    def __init__(self, alert): self._a = alert
    def feed_many(self, frames): return [self._a, self._a]
    @property
    def detector_count(self): return 1


def _alert(ts=1.0):
    return Alert(kind=AlertKind.DEAUTH_FLOOD, reason="flood", ts=ts,
                 severity="warning", bssid="aa:bb:cc:dd:ee:ff", ssid="N")


def test_supervisor_dedups_duplicate_alerts():
    sup = Supervisor(driver=_Drv(), detect_engine=_DupEngine(_alert()))
    out = sup.pump()
    assert len(out) == 1          # the duplicate was suppressed by the wired AlertBus


def test_injected_alert_bus_is_used():
    from redux.detect.bus import AlertBus
    bus = AlertBus()
    sup = Supervisor(driver=_Drv(), detect_engine=_DupEngine(_alert()), alert_bus=bus)
    sup.pump()
    # proof the SAME bus instance saw the pump's alerts (2 of them): a 3rd identical
    # alert in-window now crosses escalate_after and is escalated by this bus.
    esc = bus.publish(_alert(ts=1.0))
    assert esc is not None and esc.severity == "critical"


def test_sentinel_arms_from_env(monkeypatch):
    from redux.core.live_runtime import _arm_sentinel_from_env
    from redux.core.augur import Augur
    monkeypatch.delenv("REDUX_SENTINEL", raising=False)
    assert _arm_sentinel_from_env(Augur()) is None       # unset -> off, no behavior change
    monkeypatch.setenv("REDUX_SENTINEL", "armed")
    aug = Augur()
    s = _arm_sentinel_from_env(aug)
    assert s is not None and aug.sentinel() is s          # the guardian is actually armed
