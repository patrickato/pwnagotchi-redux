"""`redux portal` makes the evil-portal reachable, still gated by --authorized."""
import io
import contextlib

from redux.cli import main


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_plan_previews_without_serving():
    rc, out = _run(["portal", "--engagement", "acme-test", "plan"])
    assert rc == 0
    assert "engagement=acme-test" in out
    assert "NOT serving" in out
    assert "imitates no brand" in out       # built-in generic template


def test_serve_refuses_without_authorized(tmp_path, monkeypatch):
    # hermetic: pin the scope path to an empty (non-lab) scope so the refusal
    # doesn't depend on any ambient default-scope state.
    monkeypatch.setenv("REDUX_SCOPE", str(tmp_path / "nope.json"))
    rc, out = _run(["portal", "--engagement", "acme-test", "serve"])
    assert rc == 3
    assert "requires --authorized" in out


def test_plan_reports_a_custom_template(tmp_path):
    page = tmp_path / "login.html"
    page.write_text("<html><body>my own page</body></html>")
    rc, out = _run(["portal", "--engagement", "acme", "--template", str(page), "plan"])
    assert rc == 0 and "custom (" in out


def test_serve_refused_under_detection_only_persona():
    rc, out = _run(["portal", "--engagement", "t", "--persona", "blue", "serve", "--authorized"])
    assert rc == 3 and "detection-only" in out      # posture hard-disables firing


def test_serve_waived_under_lab_mode(tmp_path, monkeypatch):
    # lab mode (own-gear switch) waives the per-run --authorized, like it waives
    # pre-arming elsewhere. A detection-only persona would still block (not here).
    import redux.portal as portal_mod
    from redux.core.scope import Scope
    sp = tmp_path / "scope.json"
    Scope(lab_mode=True).save(str(sp))
    monkeypatch.setenv("REDUX_SCOPE", str(sp))

    class FakeHttpd:
        def serve_forever(self): raise KeyboardInterrupt   # stop immediately
        def shutdown(self): pass
    monkeypatch.setattr(portal_mod, "serve", lambda *a, **k: FakeHttpd())

    rc, out = _run(["portal", "--engagement", "mylab", "serve"])   # no --authorized
    assert rc == 0
    assert "lab mode" in out.lower() and "waived" in out.lower()


def test_serve_still_refuses_without_authorized_when_lab_mode_off(tmp_path, monkeypatch):
    from redux.core.scope import Scope
    sp = tmp_path / "scope.json"
    Scope(lab_mode=False).save(str(sp))
    monkeypatch.setenv("REDUX_SCOPE", str(sp))
    rc, out = _run(["portal", "--engagement", "t", "serve"])
    assert rc == 3 and "requires --authorized" in out
