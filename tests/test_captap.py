"""Raw 802.11 capture tap — parser + routing to fingerprint/detectors."""
import struct

from redux.captap import (
    parse_dot11, parse_radiotap_len, rssi_from_radiotap, build_probe_req, build_deauth,
    build_beacon, CaptureTap, AccessPoint, to_frame, capture_run, live_source,
)
from redux.detect.engine import DetectEngine
from redux.captap.dot11 import Dot11Frame
from redux.detect.deauth_flood import DeauthFloodDetector
from redux.detect.frames import FrameType
from redux.core import Augur
from redux.radio import Intent


IES = [(1, b"\x82\x84\x0b\x16"), (45, b"\x2d\x40\x00"), (127, b"\x00\x00\x00\x00\x00\x00\x40")]


# --- parser ------------------------------------------------------------------ #

def test_parse_deauth():
    f = parse_dot11(build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66", reason=7))
    assert f.kind == "deauth" and f.reason == 7
    assert f.src == "aa:bb:cc:dd:ee:ff" and f.bssid == "11:22:33:44:55:66"


def test_parse_probe_req_ssid_tags_and_hash():
    f = parse_dot11(build_probe_req("a2:11:11:11:11:11", "HomeNet", ies=IES))
    assert f.kind == "probe_req" and f.ssid == "HomeNet" and f.src == "a2:11:11:11:11:11"
    assert 0 in f.ie_tags and 45 in f.ie_tags and f.ie_hash


def test_ie_hash_is_ssid_independent():
    # same capability IEs, different SSID → same device fingerprint (PNL is separate)
    a = parse_dot11(build_probe_req("a2:11:11:11:11:11", "NetA", ies=IES))
    b = parse_dot11(build_probe_req("a2:11:11:11:11:11", "NetB", ies=IES))
    assert a.ie_hash == b.ie_hash and a.ie_hash != ""
    # different capability IEs → different fingerprint
    c = parse_dot11(build_probe_req("a2:11:11:11:11:11", "NetA", ies=[(1, b"\x82\x84")]))
    assert c.ie_hash != a.ie_hash


def test_radiotap_offset_is_skipped():
    rt = b"\x00\x00" + struct.pack("<H", 8) + b"\x00\x00\x00\x00"   # it_len=8
    assert parse_radiotap_len(rt + b"x") == 8
    frame = build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66")
    f = parse_dot11(rt + frame, radiotap=True)
    assert f is not None and f.kind == "deauth"


def test_non_management_and_short_frames_rejected():
    assert parse_dot11(b"\x08\x00" + b"\x00" * 30) is None    # data frame (type 2)
    assert parse_dot11(b"\x40\x00") is None                   # too short


def test_truncated_ie_stops_cleanly():
    # a probe-req body claiming a longer IE than present must not raise or invent
    body = bytes([0, 3]) + b"ab"       # SSID len says 3, only 2 bytes
    frame = build_probe_req("a2:11:11:11:11:11") [:24] + body
    f = parse_dot11(frame)
    assert f is not None and f.kind == "probe_req"


# --- tap routing + cross-MAC re-id ------------------------------------------- #

def test_tap_reidentifies_phone_across_mac_rotation():
    tap = CaptureTap()
    pnl = ["HomeLab-5G", "CoffeeShop"]
    for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):   # one phone, two MACs
        for ssid in pnl:
            tap.feed(build_probe_req(mac, ssid, ies=IES), ts=100.0)
    tap.feed(build_probe_req("b6:99:99:99:99:99", "Guest", ies=[(1, b"\x82")]), ts=101.0)  # other device
    s = tap.link().summary()
    assert s["reidentified"] == 1 and s["identities"] == 2


def test_tap_collects_deauth_events():
    tap = CaptureTap()
    for _ in range(4):
        tap.feed(build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66"), ts=5.0)
    ev = tap.deauth_events()
    assert len(ev) == 4 and ev[0]["bssid"] == "11:22:33:44:55:66" and ev[0]["reason"] == 7


def test_observations_carry_real_pnl_and_ie():
    tap = CaptureTap()
    tap.feed(build_probe_req("a2:11:11:11:11:11", "HomeNet", ies=IES), ts=1.0)
    obs = tap.observations()
    assert len(obs) == 1 and "HomeNet" in obs[0].ssids and obs[0].ie_hash


# --- Augur hook ---------------------------------------------------------- #

def test_augur_ingest_frames():
    frames = []
    for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):
        frames.append((build_probe_req(mac, "HomeLab-5G", ies=IES), 100.0))
    frames.append((build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66"), 101.0))
    bc = Augur(radios=None, intent=Intent.RECON)
    out = bc.ingest_frames(frames)
    assert out["frames"] == 3 and out["deauth_events"] == 1
    assert out["device_identities"]["reidentified"] == 1


# --- the P0 chain: captured frames reach the detectors ----------------------- #

def test_to_frame_maps_kinds():
    d = parse_dot11(build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66"))
    assert to_frame(d, ts=5.0).type is FrameType.DEAUTH and to_frame(d, ts=5.0).ts == 5.0
    p = parse_dot11(build_probe_req("a2:11:11:11:11:11", "HomeNet", ies=IES))
    assert to_frame(p).type is FrameType.PROBE_REQ
    assert to_frame(Dot11Frame(kind="disassoc", bssid="11:22:33:44:55:66")).type is FrameType.DISASSOC
    assert to_frame(Dot11Frame(kind="other")) is None        # not a consumed kind


def test_captured_deauths_fire_the_flood_detector():
    # raw bytes -> parse -> bridge -> detector: a real deauth burst must alert
    tap = CaptureTap()
    for i in range(22):
        tap.feed(build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"),
                 ts=100.0 + i * 0.1)
    det = DeauthFloodDetector(window_s=5.0, threshold=20)
    alerts = det.feed_many(tap.detect_frames())
    assert alerts and alerts[0].kind.value == "deauth_flood"
    assert alerts[0].bssid == "11:22:33:44:55:66" and alerts[0].reason


def test_ingest_frames_closes_loop_to_detector():
    # end-to-end through the core: a captured flood surfaces as a deauth_flood alert
    frames = [(build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"),
               200.0 + i * 0.1) for i in range(22)]
    bc = Augur(radios=None, intent=Intent.RECON)
    out = bc.ingest_frames(frames)
    assert out["detector_frames"] == 22
    assert "deauth_flood" in out["alerts"]
    # and it was voiced (glass-box) in the creature's narration
    assert any("deauth_flood" in l.text for l in bc.narrator.lines())


def test_capture_run_over_a_canned_source():
    # capture_run consumes any (raw, ts) iterable → re-id + detector alerts, no radio
    src = []
    for mac in ("a2:11:11:11:11:11", "de:22:22:22:22:22"):      # one phone, two MACs
        for ssid in ("HomeLab-5G", "CoffeeShop"):
            src.append((build_probe_req(mac, ssid, ies=IES), 10.0))
    for i in range(22):                                          # a deauth burst
        src.append((build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"), 20.0 + i * 0.1))
    tap, alerts = capture_run(iter(src), engine=DetectEngine())
    assert tap.link().summary()["reidentified"] == 1
    assert "deauth_flood" in {a.kind.value for a in alerts}


def test_capture_run_respects_max_frames():
    src = [(build_probe_req("a2:11:11:11:11:11", "N", ies=IES), float(i)) for i in range(100)]
    tap, _ = capture_run(iter(src), max_frames=5)
    assert tap.frames_seen == 5                                  # bounded, didn't drain the source


# --- beacons → passive AP inventory (the survey) ---------------------------- #

def test_build_beacon_parses_to_ap_with_channel():
    f = parse_dot11(build_beacon("aa:bb:cc:11:22:33", "HomeLab", channel=6))
    assert f is not None and f.kind == "beacon"
    assert f.bssid == "aa:bb:cc:11:22:33" and f.ssid == "HomeLab" and f.channel == 6


def test_tap_collects_access_points_busiest_first():
    tap = CaptureTap()
    for _ in range(5):
        tap.feed(build_beacon("aa:bb:cc:11:22:33", "HomeLab", 6), ts=1.0)
    for _ in range(2):
        tap.feed(build_beacon("de:ad:be:ef:00:01", "CoffeeShop", 11), ts=2.0)
    aps = tap.access_points()
    assert len(aps) == 2 and all(isinstance(a, AccessPoint) for a in aps)
    assert aps[0].bssid == "aa:bb:cc:11:22:33" and aps[0].frames == 5
    assert aps[0].channel == 6 and aps[0].ssid == "HomeLab"
    assert aps[1].bssid == "de:ad:be:ef:00:01" and aps[1].frames == 2 and aps[1].channel == 11


def test_tap_ap_hidden_ssid_is_empty():
    tap = CaptureTap()
    tap.feed(build_beacon("12:34:56:78:9a:bc", "", channel=1), ts=1.0)
    aps = tap.access_points()
    assert len(aps) == 1 and aps[0].ssid == "" and aps[0].channel == 1


def test_ap_record_tracks_first_last_and_counts():
    tap = CaptureTap()
    tap.feed(build_beacon("aa:bb:cc:11:22:33", "HomeLab", 6), ts=10.0)
    tap.feed(build_beacon("aa:bb:cc:11:22:33", "HomeLab", 6), ts=25.0)
    ap = tap.access_points()[0]
    assert ap.frames == 2 and ap.first_seen == 10.0 and ap.last_seen == 25.0


def test_beacons_are_passive_only_no_detector_frames_or_deauths():
    # a survey of beacons must not fabricate detector traffic or deauth events
    tap = CaptureTap()
    for _ in range(10):
        tap.feed(build_beacon("aa:bb:cc:11:22:33", "HomeLab", 6), ts=1.0)
    assert tap.detect_frames() == [] and tap.deauths == []
    assert tap.frames_seen == 10 and len(tap.access_points()) == 1


# --- radiotap RSSI (the fox-hunt signal) ------------------------------------ #

def test_rssi_from_radiotap_reads_signed_dbm():
    present = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 5)   # Flags, Rate, Channel, AntSignal
    hdr = (b"\x00\x00" + struct.pack("<H", 15) + struct.pack("<I", present)
           + bytes([0x00, 0x02]) + struct.pack("<HH", 2412, 0) + struct.pack("<b", -42))
    assert rssi_from_radiotap(hdr) == -42
    # and a full monitor frame (radiotap + 802.11) still yields the signal
    frame = hdr + build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66")
    assert rssi_from_radiotap(frame) == -42


def test_rssi_from_radiotap_absent_or_short_is_none():
    # present word without the antsignal bit → None, never a guess
    hdr = b"\x00\x00" + struct.pack("<H", 10) + struct.pack("<I", (1 << 1) | (1 << 2)) + bytes([0, 2])
    assert rssi_from_radiotap(hdr) is None
    assert rssi_from_radiotap(b"\x00\x00") is None        # too short


# --- regression: live monitor frames carry a radiotap header (hardware-found) --- #

_RT = b"\x00\x00" + struct.pack("<H", 8) + b"\x00\x00\x00\x00"   # radiotap, it_len=8


def test_live_source_flags_radiotap():
    frame = _RT + build_deauth("aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66", "11:22:33:44:55:66")

    class FakeSock:
        def recv(self, n):
            return frame

    raw, ts, radiotap = next(live_source("wlan1mon", _socket=FakeSock()))
    assert radiotap is True and raw == frame        # monitor frames must be flagged radiotap


def test_capture_run_parses_radiotap_frames_not_garbage():
    # the bug: a radiotap-prefixed frame fed WITHOUT the flag misparses to nothing
    src = [(_RT + build_deauth("de:ad:00:00:00:01", "ff:ff:ff:ff:ff:ff", "11:22:33:44:55:66"),
            10.0 + i * 0.1, True) for i in range(22)]
    tap, alerts = capture_run(iter(src), engine=DetectEngine())
    assert len(tap.deauths) == 22 and "deauth_flood" in {a.kind.value for a in alerts}
    # same bytes, no radiotap flag → header misread, nothing recognized (what live_source now prevents)
    bad, _ = capture_run(iter([(src[0][0], 1.0)]))
    assert len(bad.deauths) == 0
