"""The native display may only read its local, non-proxied Redux endpoint."""
import json
from urllib.error import HTTPError

import pytest

from redux.display import fbui, kiosk, local_http


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return self.body[:limit]


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8080/api/status",
    "http://example.org:8080/api/status",
    "http://127.0.0.1.evil.test:8080/api/status",
    "http://[::1]:8080/api/status",
    "http://name@127.0.0.1:8080/api/status",
    "http://127.0.0.1:8080/api/other",
    "http://127.0.0.1:8080/api/status?next=external",
    "http://127.0.0.1:8080/api/status#unsafe",
    "http://127.0.0.1:0/api/status",
    "http://127.0.0.1:65536/api/status",
    "http://127.0.0.1:abc/api/status",
    "http://127.0.0.1/api/status",
    "not a URL",
    None,
])
def test_display_status_reader_rejects_nonliteral_loopback_before_any_network(
        monkeypatch, url):
    monkeypatch.setattr(local_http.urllib.request, "build_opener",
                        lambda *a: pytest.fail("outside requests forbidden"))
    with pytest.raises(ValueError):
        local_http.open_status(url)


def test_display_loopback_client_ignores_environment_proxies_and_refuses_redirect(
        monkeypatch):
    # Fake the transport, inspect the actual handlers built for the request.
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.invalid:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:8888")
    monkeypatch.setenv("NO_PROXY", "")
    handlers = []
    observed = []
    sentinel = object()

    class FakeDirector:
        def open(self, url, timeout):
            observed.append((url, timeout))
            return sentinel

    def builder(*args):
        handlers.extend(args)
        return FakeDirector()

    monkeypatch.setattr(local_http.urllib.request, "build_opener", builder)
    output = local_http.open_status("http://127.0.0.1:8080/api/status", timeout=1.5)
    assert output is sentinel
    assert observed == [("http://127.0.0.1:8080/api/status", 1.5)]
    proxy = [h for h in handlers if isinstance(h, local_http.urllib.request.ProxyHandler)]
    assert len(proxy) == 1 and proxy[0].proxies == {}
    deny = [h for h in handlers if isinstance(h, local_http._NoRedirect)]
    assert len(deny) == 1
    # urllib treats a None redirect request as a 302 HTTPError instead
    # of pursuing the redirected location.
    assert deny[0].redirect_request(None, None, 302, "moved", {},
                                    "http://example.org/") is None


def test_native_fbui_default_status_reader_uses_restricted_client(monkeypatch):
    calls = []
    payload = json.dumps({
        "runtime": {"state": "running"},
        "doctor": {"overall": "unknown"},
    }).encode()

    def restricted(url, timeout):
        calls.append((url, timeout))
        return FakeResponse(payload)

    monkeypatch.setattr(fbui, "open_status", restricted)
    assert fbui.read_status(8080)["runtime"]["state"] == "running"
    assert calls == [("http://127.0.0.1:8080/api/status", 2)]


def test_kiosk_default_preflight_uses_restricted_client(monkeypatch):
    calls = []
    def restricted(url, timeout):
        calls.append((url, timeout))
        return FakeResponse(
            b'{"runtime":{"state":"running"},"doctor":{"overall":"unknown"}}')
    monkeypatch.setattr(kiosk, "open_status", restricted)
    result = kiosk.dashboard_probe(8080)
    assert result["ok"] is True
    assert calls == [("http://127.0.0.1:8080/api/status", 2)]


def test_display_preflights_fail_if_loopback_redirects_without_remote_access(
        monkeypatch):
    def denied(url, timeout):
        raise HTTPError(url, 302, "unexpected local redirect", {}, None)
    monkeypatch.setattr(fbui, "open_status", denied)
    monkeypatch.setattr(kiosk, "open_status", denied)
    assert fbui.read_status(8080) is None
    assert kiosk.dashboard_probe(8080)["ok"] is False
