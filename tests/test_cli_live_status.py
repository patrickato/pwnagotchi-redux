"""`redux live status` must report an unreadable/missing snapshot, never crash.

Regression: run as a non-root user, the default snapshot (/captures/redux/live.json)
lives under a dir the user can't stat, so Path.is_symlink() raised PermissionError
(EACCES is not in pathlib's ignore list) and the status command tracebacked.
"""
import io
import contextlib
import pathlib

from redux.cli import main


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_status_reports_permission_denied_with_sudo_hint(monkeypatch):
    def deny(self):
        raise PermissionError(13, "Permission denied")
    # The first stat the command makes is is_symlink(); make it raise EACCES.
    monkeypatch.setattr(pathlib.Path, "is_symlink", deny)
    rc, out = _run(["live", "status", "--file", "/captures/redux/live.json"])
    assert rc == 3
    assert "cannot read runtime snapshot" in out
    assert "sudo" in out.lower()
    assert "Traceback" not in out


def test_status_reports_missing_snapshot(tmp_path):
    rc, out = _run(["live", "status", "--file", str(tmp_path / "nope.json")])
    assert rc == 3
    assert "no verified runtime snapshot" in out


def test_status_rejects_a_symlinked_snapshot(tmp_path):
    real = tmp_path / "real.json"
    real.write_text('{"state": "idle"}')
    link = tmp_path / "live.json"
    link.symlink_to(real)
    rc, out = _run(["live", "status", "--file", str(link)])
    assert rc == 3
    assert "no verified runtime snapshot" in out


def test_status_prints_a_valid_snapshot(tmp_path):
    snap = tmp_path / "live.json"
    snap.write_text('{"state": "recon", "radio": "wlan1"}')
    rc, out = _run(["live", "status", "--file", str(snap)])
    assert rc == 0
    assert '"state"' in out and "recon" in out
