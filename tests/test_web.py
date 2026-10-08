import json
import threading
import urllib.request
import urllib.error

from redux.web import status_payload, render_page, resolve_host, serve, make_handler, auth_token
from redux.core import Augur
from redux.radio import Radio, Intent


def _serve(handler):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _get(port, path, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
ALFA = Radio("wlan1", bands=frozenset({"2.4", "5"}), monitor=True, inject=True, driver="mt76x2u", usb_gen=3, high_draw=True)


def test_status_payload_has_fields():
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT)
    p = status_payload(bc)
    assert p["intent"] == "hunt" and p["capture_iface"] == "wlan1"
    assert "narration" in p and "recommendation" in p


def test_status_payload_includes_own_position_honestly():
    # no position provider → position is None, never a fabricated coordinate
    bc = Augur([ONBOARD], intent=Intent.RECON)
    assert status_payload(bc)["position"] is None
    bc2 = Augur([ONBOARD], intent=Intent.RECON, position_provider=lambda: (45.0, -93.0))
    assert status_payload(bc2)["position"] == {"lat": 45.0, "lon": -93.0}


def test_status_payload_lists_access_points_from_cache():
    from redux.geo.db import Sighting
    bc = Augur([ONBOARD], intent=Intent.RECON)
    bc.store.insert(Sighting(kind="wifi", mac="aa:bb:cc:11:22:33", ssid="HomeLab",
                             channel=6, rssi=-42, ts=100.0, provenance="test"))
    bc.store.insert(Sighting(kind="wifi", mac="de:ad:be:ef:00:01", ssid="",
                             channel=11, rssi=-70, ts=101.0, provenance="test"))
    bc.store.insert(Sighting(kind="ble", mac="ff:ee:dd:cc:bb:aa", ts=102.0, provenance="test"))
    aps = status_payload(bc)["access_points"]
    macs = {a["bssid"] for a in aps}
    assert "aa:bb:cc:11:22:33" in macs and "de:ad:be:ef:00:01" in macs
    assert "ff:ee:dd:cc:bb:aa" not in macs          # BLE is a device, not an AP
    ap = next(a for a in aps if a["bssid"] == "aa:bb:cc:11:22:33")
    assert ap["ssid"] == "HomeLab" and ap["channel"] == 6 and ap["rssi"] == -42


def test_page_has_access_points_panel():
    html = render_page()
    assert "renderAPs" in html and "aptbl" in html and "access_points" in html


def test_page_has_rich_skin_toggle_and_moving_track():
    html = render_page()
    assert "skinbtn" in html and "data-skin" in html      # plain/rich skin toggle
    assert "TRACK" in html and "renderMap" in html         # client-accumulated track
    assert "src=" not in html and "cdn" not in html.lower()  # still self-contained


def test_page_is_self_contained_html():
    html = render_page()
    assert html.lstrip().startswith("<!doctype html>")
    assert "/api/status" in html and "http" not in html.split("fetch('/api/status')")[0][-200:]
    # no external asset links (self-contained)
    assert "src=" not in html and "cdn" not in html.lower()


def test_bind_scope_defaults_to_localhost():
    assert resolve_host("localhost") == "127.0.0.1"
    assert resolve_host("lan") == "0.0.0.0"
    assert resolve_host("anything-unknown") == "127.0.0.1"   # safe default


def test_handler_serves_status_and_page():
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT)
    snap = status_payload(bc)                 # computed in THIS (store-owning) thread
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: snap))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
            data = json.loads(r.read())
        assert data["intent"] == "hunt"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            assert b"<!doctype html>" in r.read().lower()
    finally:
        srv.shutdown(); srv.server_close()


def test_serve_loop_bounded_updates_snapshot():
    bc = Augur([ONBOARD, ALFA], intent=Intent.HUNT)
    # bounded loop (no driver -> pump is a no-op), just proves serve() runs + binds
    serve(bc, port=0, bind_scope="localhost", interval=0, pump=True, _cycles=2)


def test_located_sightings_returns_only_real_fixes():
    from redux.geo.db import Sighting
    bc = Augur([ONBOARD], intent=Intent.RECON)
    bc.store.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:aa", ssid="A", lat=40.1, lon=-80.2, ts=1.0, provenance="test"))
    bc.store.insert(Sighting(kind="wifi", mac="bb:bb:bb:bb:bb:bb", ssid="B", ts=2.0, provenance="test"))  # no fix
    located = bc.located_sightings()
    macs = {p["mac"] for p in located}
    assert "aa:aa:aa:aa:aa:aa" in macs            # has lat/lon -> included
    assert "bb:bb:bb:bb:bb:bb" not in macs        # no fix -> never fabricated
    assert all(p["lat"] is not None and p["lon"] is not None for p in located)


def test_status_payload_includes_located_points():
    from redux.geo.db import Sighting
    bc = Augur([ONBOARD], intent=Intent.RECON)
    bc.store.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:aa", ssid="A", lat=40.1, lon=-80.2, ts=1.0, provenance="test"))
    p = status_payload(bc)
    assert "located" in p and len(p["located"]) == 1
    assert p["located"][0]["lat"] == 40.1 and p["located"][0]["lon"] == -80.2


def test_page_has_map_panel():
    html = render_page()
    assert 'id="map"' in html and "renderMap" in html
    # honest empty state wording present
    assert "needs a GPS fix" in html


# --- surface auth (fail-closed when exposed off-box) ------------------------- #

def test_auth_token_policy():
    assert auth_token("localhost") is None                 # loopback: open by default
    assert auth_token("localhost", "x") == "x"             # explicit token always wins
    assert auth_token("lan", "x") == "x"
    t = auth_token("lan")                                   # exposed + no token → minted, fail-closed
    assert isinstance(t, str) and len(t) >= 16


def test_api_requires_token_when_set():
    srv, port = _serve(make_handler(lambda: {"intent": "hunt"}, token="s3cret"))
    try:
        assert _get(port, "/api/status")[0] == 401                                   # none
        assert _get(port, "/api/status", {"Authorization": "Bearer wrong"})[0] == 401  # wrong
        code, body = _get(port, "/api/status", {"Authorization": "Bearer s3cret"})
        assert code == 200 and json.loads(body)["intent"] == "hunt"                   # right
    finally:
        srv.shutdown(); srv.server_close()


def test_non_ascii_token_401s_not_crashes():
    # an odd/non-ASCII presented token must fail closed with 401, never crash the thread
    srv, port = _serve(make_handler(lambda: {"intent": "hunt"}, token="s3cret"))
    try:
        assert _get(port, "/api/status", {"Authorization": "Bearer café"})[0] == 401
    finally:
        srv.shutdown(); srv.server_close()


def test_page_shows_login_until_token_then_dashboard():
    srv, port = _serve(make_handler(lambda: {"intent": "hunt"}, token="s3cret"))
    try:
        code, body = _get(port, "/")
        assert code == 401 and "access token" in body and "renderMap" not in body    # unlock page only
        code, body = _get(port, "/", {"Cookie": "augur_token=s3cret"})
        assert code == 200 and "renderMap" in body                                    # cookie unlocks the real page
    finally:
        srv.shutdown(); srv.server_close()


def test_open_handler_needs_no_token():
    srv, port = _serve(make_handler(lambda: {"intent": "hunt"}))   # token=None → open (localhost default)
    try:
        assert _get(port, "/api/status")[0] == 200
        assert "renderMap" in _get(port, "/")[1]
    finally:
        srv.shutdown(); srv.server_close()


def test_status_payload_airspace_from_store():
    from redux.geo.db import Sighting
    bc = Augur([ONBOARD], intent=Intent.RECON)
    bc.store.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:01", channel=6, rssi=-55, ts=1.0, provenance="t"))
    bc.store.insert(Sighting(kind="wifi", mac="aa:aa:aa:aa:aa:02", channel=11, rssi=-72, ts=2.0, provenance="t"))
    air = status_payload(bc)["airspace"]
    assert air["channels"].get(6) == 1 and air["channels"].get(11) == 1   # channel occupancy
    assert sum(air["rssi"].values()) == 2                                  # RSSI distribution


def test_page_has_airspace_panel():
    html = render_page()
    assert "renderAirspace" in html and 'id="chanbars"' in html and "airspace" in html
    assert "src=" not in html and "cdn" not in html.lower()                # still self-contained
