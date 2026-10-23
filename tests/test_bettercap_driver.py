import pytest

from redux.engine import (
    BettercapDriver, BettercapConfig, Allowlist, FiringRefused,
    ReplayTransport, normalize_event, Event,
)


# --- recorded bettercap events (atlas B-12: real-shaped JSON, no radio) ----- #
RECORDED = [
    {"tag": "wifi.ap.new", "time": 1000.0, "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "HomeNet"}},
    {"tag": "wifi.client.handshake", "time": 1001.0, "data": {"ap": "aa:bb:cc:dd:ee:ff"}},
    {"tag": "ble.device.new", "time": 1002.0, "data": {"mac": "11:22:33:44:55:66"}},
]


def test_config_base_url_and_bind_scope():
    cfg = BettercapConfig(host="10.0.0.5", port=8080, bind_scope="tailscale")
    assert cfg.base_url == "http://10.0.0.5:8080/api"
    with pytest.raises(ValueError):
        BettercapConfig(bind_scope="everywhere")


def test_normalize_event_maps_and_reasons():
    ev = normalize_event(RECORDED[1])
    assert isinstance(ev, Event)
    assert ev.type == "handshake"
    assert "aa:bb:cc:dd:ee:ff" in ev.reason
    assert ev.raw_tag == "wifi.client.handshake"
    # unknown tag degrades to its tag, not a crash
    assert normalize_event({"tag": "weird.thing", "data": {}}).type == "weird.thing"



def test_normalize_rfc3339_event_timestamps_to_real_unix_seconds():
    utc = normalize_event({
        "tag": "wifi.ap.new",
        "time": "2024-01-01T00:00:00.123456789Z",
        "data": {"mac": "aa:bb:cc:dd:ee:ff"},
    })
    offset = normalize_event({
        "tag": "wifi.ap.new",
        "time": "2024-01-01T02:00:00.123456789+02:00",
        "data": {"mac": "aa:bb:cc:dd:ee:ff"},
    })
    assert utc.at == pytest.approx(1704067200.123456)
    assert offset.at == pytest.approx(utc.at)
    assert normalize_event({"tag": "wifi.ap.new", "time": 123.5}).at == 123.5


def test_bad_event_clock_never_raises():
    for invalid in ("not-a-clock", "", "2024-01-01T00:00:00", float("nan"),
                    float("inf"), True, None):
        ev = normalize_event({"tag": "wifi.ap.new", "time": invalid})
        assert ev.at > 0

def test_set_interface_builds_command_and_logs():
    rec = []
    t = ReplayTransport()
    drv = BettercapDriver(transport=t, log=rec.append)
    drv.set_interface("wlan1")
    drv.recon(True)
    drv.set_channels([1, 6, 11])
    assert t.commands == ["set wifi.interface wlan1", "wifi.recon on", "wifi.recon.channel 1,6,11"]
    assert any("wlan1" in line for line in rec)        # glass-box log fired


def test_poll_events_normalizes_recorded_stream():
    drv = BettercapDriver(transport=ReplayTransport(events=RECORDED))
    evs = drv.poll_events(clear=False)
    assert [e.type for e in evs] == ["ap.new", "handshake", "ble.new"]


def test_stream_events_bounded():
    drv = BettercapDriver(transport=ReplayTransport(events=list(RECORDED)))
    got = list(drv.stream_events(interval=0, _max_polls=1))
    assert len(got) == 3


def test_deauth_refused_when_allowlist_empty():
    rec = []
    drv = BettercapDriver(transport=ReplayTransport(), allowlist=Allowlist(), log=rec.append)
    assert drv.allowlist.empty
    with pytest.raises(FiringRefused) as ei:
        drv.deauth("aa:bb:cc:dd:ee:ff")
    assert "empty" in str(ei.value)
    assert any("REFUSED" in line for line in rec)      # refusal is logged, glass-box


def test_deauth_refused_when_target_not_listed():
    drv = BettercapDriver(transport=ReplayTransport(),
                          allowlist=Allowlist(bssids={"00:11:22:33:44:55"}))
    with pytest.raises(FiringRefused):
        drv.deauth("aa:bb:cc:dd:ee:ff")


def test_deauth_allowed_for_authorized_target():
    t = ReplayTransport()
    drv = BettercapDriver(transport=t, allowlist=Allowlist(bssids={"AA:BB:CC:DD:EE:FF"}))
    drv.deauth("aa:bb:cc:dd:ee:ff")   # case-insensitive match
    assert t.commands == ["wifi.deauth aa:bb:cc:dd:ee:ff"]


def test_allowlist_add_and_permits():
    al = Allowlist()
    al.add(ssid="MyLab")
    al.add(bssid="De:Ad:Be:Ef:00:01")
    assert not al.empty
    assert al.permits(ssid="MyLab")
    assert al.permits(bssid="de:ad:be:ef:00:01")
    assert not al.permits(bssid="de:ad:be:ef:00:02")


def test_http_transport_uses_documented_api_routes_and_auth(monkeypatch):
    """Smoke-test real URL construction, not a replay-only Transport double."""
    import base64
    import json
    from redux.engine.bettercap_driver import HttpTransport
    from urllib import request as urllib_request
    seen = []

    class Reply:
        def __init__(self, data):
            self.data = data
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return self.data

    def open_fake(req, timeout=None):
        seen.append((req.full_url, req.get_method(),
                     req.get_header("Authorization"), req.data))
        if req.full_url.endswith("/events") and req.get_method() == "GET":
            return Reply(b'[{"tag":"wifi.ap.new","data":{"mac":"aa:bb:cc:dd:ee:ff"}}]')
        if req.get_method() == "POST":
            return Reply(b'{"success":true,"msg":""}')
        return Reply(b'{}')

    # Bettercap traffic must use a proxy-free opener even if the inherited
    # service environment contains a corporate or debugging HTTP proxy.
    def fake_opener(*handlers):
        assert any(isinstance(h, urllib_request.ProxyHandler)
                   and h.proxies == {} for h in handlers)
        class Opener:
            def open(self, req, timeout=None):
                return open_fake(req, timeout=timeout)
        return Opener()
    monkeypatch.setattr(urllib_request, "build_opener", fake_opener)
    transport = HttpTransport(BettercapConfig(
        host="127.0.0.1", port=8081, username="redux", password="synthetic"
    ))
    assert transport.run("wifi.recon on")["success"] is True
    assert transport.session() == {}
    assert len(transport.events(clear=True)) == 1
    assert [(url, method) for url, method, _, _ in seen] == [
        ("http://127.0.0.1:8081/api/session", "POST"),
        ("http://127.0.0.1:8081/api/session", "GET"),
        ("http://127.0.0.1:8081/api/events", "GET"),
        ("http://127.0.0.1:8081/api/events", "DELETE"),
    ]
    expected = "Basic " + base64.b64encode(b"redux:synthetic").decode()
    assert all(auth == expected for _, _, auth, _ in seen)
    assert json.loads(seen[0][3]) == {"cmd": "wifi.recon on"}


def test_http_transport_event_clear_failure_is_not_hidden(monkeypatch):
    from urllib.error import URLError
    from redux.engine.bettercap_driver import HttpTransport, BettercapUnavailable
    transport = HttpTransport(BettercapConfig())
    calls = []
    def fake(method, path, body=None):
        calls.append((method, path))
        if method == "DELETE":
            raise BettercapUnavailable("synthetic offline DELETE failure")
        return [{"tag": "wifi.ap.new", "data": {"mac": "aa:bb:cc:dd:ee:ff"}}]
    monkeypatch.setattr(transport, "_request", fake)
    with pytest.raises(BettercapUnavailable, match="DELETE failure"):
        transport.events(clear=True)
    assert calls == [("GET", "/events"), ("DELETE", "/events")]


def test_http_transport_rejects_invalid_event_payload(monkeypatch):
    from redux.engine.bettercap_driver import HttpTransport, BettercapUnavailable
    transport = HttpTransport(BettercapConfig())
    calls = []
    def fake(method, path, body=None):
        calls.append(method)
        return {"error": "unexpected response"}
    monkeypatch.setattr(transport, "_request", fake)
    with pytest.raises(BettercapUnavailable, match="unexpected payload"):
        transport.events(clear=True)
    assert calls == ["GET"]

def test_http_transport_bypasses_inherited_proxy_with_real_local_http_server(monkeypatch):
    """An environmental proxy must not answer the Bettercap loopback route."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from redux.engine.bettercap_driver import HttpTransport

    requests = []
    class BettercapMock(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path,
                             self.headers.get("Authorization")))
            payload = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    class ProxyMock(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(("PROXY", self.path, None))
            self.send_error(400, "proxy refuses local host")
        def log_message(self, *args):
            pass

    main = ThreadingHTTPServer(("127.0.0.1", 0), BettercapMock)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyMock)
    workers = []
    try:
        for server in (main, proxy):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            workers.append(thread)
        for key in ("http_proxy", "HTTP_PROXY", "no_proxy", "NO_PROXY"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy.server_port}")
        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
        transport = HttpTransport(BettercapConfig(
            host="127.0.0.1", port=main.server_port,
            username="redux", password="synthetic"))
        assert transport.session() == {"ok": True}
        assert [r[0] for r in requests] == ["GET"]
        assert requests[0][1] == "/api/session"
        assert requests[0][2].startswith("Basic ")
    finally:
        main.shutdown()
        proxy.shutdown()
        main.server_close()
        proxy.server_close()
        for worker in workers:
            worker.join(timeout=2)


def test_http_transport_400_surfaces_method_and_status_not_secret_or_endpoint(monkeypatch):
    from urllib import error as urllib_error, request as urllib_request
    from redux.engine.bettercap_driver import HttpTransport, BettercapUnavailable
    class Opener:
        def open(self, req, timeout=None):
            raise urllib_error.HTTPError(
                req.full_url, 400, "Bad Request", {},
                __import__("io").BytesIO(b"private api.rest.password SecretPass"))
    monkeypatch.setattr(urllib_request, "build_opener", lambda *args: Opener())
    transport = HttpTransport(BettercapConfig(password="SecretPass"))
    with pytest.raises(BettercapUnavailable) as caught:
        transport.session()
    diagnostic = str(caught.value)
    assert "HTTP 400" in diagnostic
    assert "GET /session" in diagnostic
    assert "SecretPass" not in diagnostic
    assert "private" not in diagnostic
    assert "127.0.0.1" not in diagnostic


def test_http_transport_invalid_json_is_explicit(monkeypatch):
    from urllib import request as urllib_request
    from redux.engine.bettercap_driver import HttpTransport, BettercapUnavailable
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b"<html>not bettercap</html>"
    class Opener:
        def open(self, req, timeout=None): return Response()
    monkeypatch.setattr(urllib_request, "build_opener", lambda *args: Opener())
    with pytest.raises(BettercapUnavailable, match="invalid JSON for GET /session"):
        HttpTransport(BettercapConfig()).session()



def test_deauth_refuses_a_malformed_bssid_before_firing():
    # a non-MAC must never be interpolated into the engine command, even with a
    # non-empty allowlist authorizing by SSID.
    drv = BettercapDriver(transport=ReplayTransport(),
                          allowlist=Allowlist(ssids={"MyNet"}))
    with pytest.raises(FiringRefused, match="not a valid BSSID"):
        drv.deauth("$(reboot)", ssid="MyNet")
    with pytest.raises(FiringRefused, match="not a valid BSSID"):
        drv.deauth("zz:zz:zz:zz:zz:zz", ssid="MyNet")
