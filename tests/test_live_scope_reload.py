"""The running daemon re-reads scope.json when it changes — `redux scope add/
remove` and `lab-mode on/off` take effect without a restart, fail-safe."""
import os
from types import SimpleNamespace

from redux.core.live_runtime import LiveRuntime, _scope_mtime
from redux.core.scope import Scope


class _FakeAugur:
    """Records scope assignments; the real Augur.scope setter re-arms the driver."""
    def __init__(self):
        self._s = Scope()
        self.assigned = []

    @property
    def scope(self):
        return self._s

    @scope.setter
    def scope(self, value):
        self._s = value
        self.assigned.append(value)


def _rt(scope_path):
    return SimpleNamespace(
        augur=_FakeAugur(),
        _scope_path=str(scope_path),
        _scope_mtime=_scope_mtime(str(scope_path)),
    )


def _bump_mtime(path):
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 5))   # guarantee a detectable change


def test_unchanged_scope_file_is_not_reloaded(tmp_path):
    p = tmp_path / "scope.json"
    Scope().save(str(p))
    rt = _rt(p)
    LiveRuntime._maybe_reload_scope(rt)
    assert rt.augur.assigned == []            # no change -> no re-arm


def test_edited_scope_file_reloads_and_rearms(tmp_path):
    p = tmp_path / "scope.json"
    Scope().save(str(p))
    rt = _rt(p)
    s = Scope(); s.add("10.0.0.0/24", job="lab"); s.save(str(p))   # `redux scope add`
    _bump_mtime(str(p))
    LiveRuntime._maybe_reload_scope(rt)
    assert len(rt.augur.assigned) == 1
    reloaded = rt.augur.assigned[0]
    assert any(e.value == "10.0.0.0/24" for e in reloaded.active_entries())


def test_scope_appearing_later_is_picked_up(tmp_path):
    p = tmp_path / "scope.json"              # absent at startup
    rt = _rt(p)
    assert rt._scope_mtime is None
    s = Scope(); s.add("192.168.1.0/24", job="lab"); s.save(str(p))
    LiveRuntime._maybe_reload_scope(rt)
    assert len(rt.augur.assigned) == 1
    assert any(e.value == "192.168.1.0/24" for e in rt.augur.assigned[0].active_entries())


def test_unreadable_scope_reloads_to_empty_passive(tmp_path):
    p = tmp_path / "scope.json"
    s = Scope(); s.add("10.0.0.0/24", job="lab"); s.save(str(p))
    rt = _rt(p)
    p.write_text("{ not valid json")          # corrupt it
    _bump_mtime(str(p))
    LiveRuntime._maybe_reload_scope(rt)
    assert len(rt.augur.assigned) == 1
    assert rt.augur.assigned[0].active_entries() == []   # fail-safe: empty, passive


def test_no_augur_is_a_noop(tmp_path):
    p = tmp_path / "scope.json"
    Scope().save(str(p))
    rt = SimpleNamespace(augur=None, _scope_path=str(p), _scope_mtime=None)
    LiveRuntime._maybe_reload_scope(rt)       # must not raise
