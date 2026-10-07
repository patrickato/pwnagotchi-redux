"""Captive portal (authorized testing) — refusal gate, serve, capture."""
import threading
import urllib.request
import urllib.parse

import pytest

from redux.portal import CaptivePortal, make_handler, serve, DEFAULT_TEMPLATE
from http.server import ThreadingHTTPServer


def test_refuses_to_start_without_explicit_authorization():
    p = CaptivePortal(engagement="acme-test")
    with pytest.raises(ValueError):
        serve(p, authorized=False)


def test_record_captures_submissions():
    p = CaptivePortal(engagement="acme-test")
    p.record({"username": "alice", "password": "s3cret"}, client="10.0.0.9", now=100.0)
    assert len(p.submissions) == 1
    s = p.submissions[0]
    assert s.fields["username"] == "alice" and s.fields["password"] == "s3cret"
    assert s.client == "10.0.0.9" and s.ts == 100.0


def test_default_template_is_brand_neutral():
    # the shipped default must not impersonate a real org
    t = DEFAULT_TEMPLATE.lower()
    assert "network access" in t
    for brand in ("google", "facebook", "microsoft", "apple", "amazon", "gmail", "office365"):
        assert brand not in t


def test_handler_serves_page_and_records_post():
    p = CaptivePortal(engagement="acme-test")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            assert b"Network Access" in r.read()
        data = urllib.parse.urlencode({"username": "bob", "password": "pw"}).encode()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", data=data, timeout=3) as r:
            r.read()
        assert len(p.submissions) == 1 and p.submissions[0].fields["username"] == "bob"
    finally:
        srv.shutdown(); srv.server_close()


def test_serve_binds_least_exposed_by_default():
    p = CaptivePortal(engagement="acme-test")
    httpd = serve(p, authorized=True, port=0, bind_scope="localhost", _serve=False)
    try:
        assert httpd.server_address[0] == "127.0.0.1"   # localhost, not 0.0.0.0
    finally:
        httpd.server_close()
