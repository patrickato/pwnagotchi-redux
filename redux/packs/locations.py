"""Shared installed locations and explicit writable-state preparation."""
import os
import stat
from pathlib import Path
from ..storage_mount import capture_mount_snapshot

CATALOG = Path('/usr/share/redux/packs')
STATE = Path('/captures/config/packs.json')


def locations(root=None, state=None):
    root = Path(root or os.environ.get('REDUX_PACKS_DIR') or CATALOG)
    state = Path(state or os.environ.get('REDUX_PACKS_STATE') or
                 (STATE if root == CATALOG else root / '.packs-state.json'))
    return root, state


class StateUnavailable(ValueError):
    """Fixed local state-safety diagnostic; safe for operator output."""


def captures_snapshot():
    snapshot = capture_mount_snapshot()
    try:
        label = Path('/dev/disk/by-label/REDUXCAP')
        if snapshot is None or not label.is_symlink():
            return None
        device = label.stat()
        if not stat.S_ISBLK(device.st_mode) or device.st_rdev != Path('/captures').stat().st_dev:
            return None
    except OSError:
        return None
    return snapshot


def mount_guard(state, *, probe=None):
    """Pin captures generation; custom development paths need no Pi mount."""
    state = Path(state).absolute()
    if state != Path('/captures') and Path('/captures') not in state.parents:
        return lambda: None
    probe = probe or captures_snapshot
    expected = probe()
    if expected is None:
        raise StateUnavailable('captures mount unavailable; plugin requests were not changed')
    def guard():
        if probe() != expected:
            raise StateUnavailable('captures mount changed; plugin request stopped')
    return guard


def prepare_parent(state, guard):
    """Only a confirmed local write creates state directories, never the UI."""
    state = Path(state).absolute()
    guard()
    if state.parent.absolute() != state.parent.resolve():
        raise ValueError('linked plugin state parent is unsafe')
    ancestor = state.parent
    missing = []
    while not ancestor.exists():
        missing.append(ancestor.name)
        ancestor = ancestor.parent
    fd = os.open(ancestor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        guard()
        for name in reversed(missing):
            guard()
            try:
                os.mkdir(name, mode=0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        guard()
    finally:
        os.close(fd)
