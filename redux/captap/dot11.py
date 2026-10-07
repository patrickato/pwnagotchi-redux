"""802.11 management-frame parser — the raw-frame tap's pure core.

bettercap's REST stream is high-level (an AP/client appeared); it does NOT expose
raw 802.11 management frames. That gap is why deauth-flood / surveillance-sweep
detection and fingerprint PNL/IE re-identification were dark. This module is the
producer: parse a monitor-mode frame (optionally behind a radiotap header) into a
normalized record —

  - **deauth / disassoc** → src/dst/bssid + reason code (feeds the flood detectors),
  - **probe request** → the directed SSID (a PNL entry) and an **IE fingerprint**
    (the ordered capability information-elements, SSID excluded) that identifies the
    device across MAC randomization (feeds the fingerprint Dex).

Pure and fully testable from raw bytes — no radio. The live monitor socket is the
thin, honest-tool-absent adapter in `tap.py`.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


def _mac(b: bytes) -> str:
    return ":".join("%02x" % x for x in b)


def parse_radiotap_len(buf: bytes) -> int:
    """Radiotap header length (it_len, LE u16 at offset 2). 0 if too short."""
    if len(buf) < 4:
        return 0
    return struct.unpack_from("<H", buf, 2)[0]


@dataclass(frozen=True)
class Dot11Frame:
    kind: str                       # deauth | disassoc | probe_req | other
    dst: str = ""
    src: str = ""
    bssid: str = ""
    reason: Optional[int] = None    # deauth/disassoc reason code
    ssid: str = ""                  # probe-req directed SSID (a PNL entry), "" if wildcard
    ie_tags: Tuple[int, ...] = ()   # ordered IE tag numbers present
    ie_hash: str = ""               # fingerprint over capability IEs (SSID excluded)
    ts: float = 0.0


def _parse_ies(body: bytes) -> Tuple[str, Tuple[int, ...], str]:
    """Walk 802.11 tagged parameters → (ssid, ordered_tags, ie_fingerprint_hash).
    The fingerprint hashes the ordered (tag,value) of capability IEs with the SSID
    (tag 0) EXCLUDED, so it identifies the device regardless of which network it is
    probing for."""
    ssid = ""
    tags: List[int] = []
    fp_parts: List[bytes] = []
    i = 0
    n = len(body)
    while i + 2 <= n:
        tag = body[i]
        ln = body[i + 1]
        val = body[i + 2:i + 2 + ln]
        if len(val) < ln:
            break                   # truncated IE — stop, don't invent
        tags.append(tag)
        if tag == 0:                # SSID element → PNL, not part of the IE fingerprint
            ssid = val.decode("utf-8", "replace")
        else:
            fp_parts.append(bytes([tag, ln]) + val)
        i += 2 + ln
    ie_hash = hashlib.sha256(b"".join(fp_parts)).hexdigest()[:16] if fp_parts else ""
    return ssid, tuple(tags), ie_hash


def parse_dot11(buf: bytes, *, radiotap: bool = False, ts: float = 0.0) -> Optional[Dot11Frame]:
    """Parse one 802.11 frame (optionally behind a radiotap header). Returns a
    Dot11Frame for management deauth/disassoc/probe-req, a generic 'other' for other
    management frames, or None if it isn't a parseable management frame."""
    frame = buf[parse_radiotap_len(buf):] if radiotap else buf
    if len(frame) < 24:
        return None
    fc0 = frame[0]
    ftype = (fc0 >> 2) & 0x3
    subtype = (fc0 >> 4) & 0xF
    if ftype != 0:                  # management frames only
        return None
    dst, src, bssid = _mac(frame[4:10]), _mac(frame[10:16]), _mac(frame[16:22])
    body = frame[24:]
    if subtype in (10, 12):         # disassoc / deauth
        reason = struct.unpack_from("<H", body, 0)[0] if len(body) >= 2 else None
        return Dot11Frame("deauth" if subtype == 12 else "disassoc",
                          dst=dst, src=src, bssid=bssid, reason=reason, ts=ts)
    if subtype == 4:                # probe request
        ssid, tags, ie_hash = _parse_ies(body)
        return Dot11Frame("probe_req", dst=dst, src=src, bssid=bssid,
                          ssid=ssid, ie_tags=tags, ie_hash=ie_hash, ts=ts)
    return Dot11Frame("other", dst=dst, src=src, bssid=bssid, ts=ts)


# --- a tiny frame builder (tests / demo; also documents the wire layout) ----- #

def build_probe_req(src: str, ssid: str = "", *, ies=None, ts: float = 0.0) -> bytes:
    """Build a probe-request frame. `ies` is a list of (tag, value_bytes) capability
    IEs (SSID is added as tag 0 from `ssid`). For tests and the demo."""
    def macb(m):
        return bytes(int(x, 16) for x in m.split(":"))
    hdr = bytes([0x40, 0x00]) + b"\x00\x00" + macb("ff:ff:ff:ff:ff:ff") + macb(src) + macb("ff:ff:ff:ff:ff:ff") + b"\x00\x00"
    body = bytes([0, len(ssid)]) + ssid.encode()
    for tag, val in (ies or []):
        body += bytes([tag, len(val)]) + val
    return hdr + body


def build_deauth(src: str, dst: str, bssid: str, reason: int = 7, *, ts: float = 0.0) -> bytes:
    def macb(m):
        return bytes(int(x, 16) for x in m.split(":"))
    hdr = bytes([0xC0, 0x00]) + b"\x00\x00" + macb(dst) + macb(src) + macb(bssid) + b"\x00\x00"
    return hdr + struct.pack("<H", reason)
