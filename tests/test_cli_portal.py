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


def test_serve_refuses_without_authorized():
    rc, out = _run(["portal", "--engagement", "acme-test", "serve"])
    assert rc == 3
    assert "requires --authorized" in out


def test_plan_reports_a_custom_template(tmp_path):
    page = tmp_path / "login.html"
    page.write_text("<html><body>my own page</body></html>")
    rc, out = _run(["portal", "--engagement", "acme", "--template", str(page), "plan"])
    assert rc == 0 and "custom (" in out
