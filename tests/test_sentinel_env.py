"""REDUX_SENTINEL env arming — the documented 'warning' value must actually arm
the Sentinel, not silently no-op (the Severity enum spells it 'warn')."""
import redux.core.live_runtime as lr
from redux.core.augur import Augur


def test_sentinel_env_warning_arms(monkeypatch):
    monkeypatch.setenv("REDUX_SENTINEL", "warning")
    result = lr._arm_sentinel_from_env(Augur())
    assert result is not None          # previously swallowed Severity('warning') ValueError


def test_sentinel_env_warn_and_critical_arm(monkeypatch):
    for v in ("warn", "critical", "info", "armed"):
        monkeypatch.setenv("REDUX_SENTINEL", v)
        assert lr._arm_sentinel_from_env(Augur()) is not None


def test_sentinel_env_unset_is_off(monkeypatch):
    monkeypatch.delenv("REDUX_SENTINEL", raising=False)
    assert lr._arm_sentinel_from_env(Augur()) is None
