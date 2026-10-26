"""Small, real-telemetry, browser-free RGB565 Redux TFT dashboard.

Opt-in only: --framebuffer must name a verified Linux /dev/fbN. We never
probe arbitrary /dev/fb0 as a fallback, choose input devices, start Xorg,
or change Wi-Fi, kernel overlays or touch calibration. UI reads the existing
loopback live-status API and never represents a missing sample as healthy.
"""
from __future__ import annotations

from functools import lru_cache

import argparse
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import time
import urllib.error
import urllib.request

from .local_http import open_status
from . import calibration_file

FB_PATTERN = re.compile(r"/dev/fb[0-9]{1,2}\Z")
FB_TOUCH_PATTERN = re.compile(r"/dev/input/event[0-9]{1,3}\Z")
RESOLUTION = (480, 320)
MAX_STATUS = 65536
MAX_MEASURED_COUNT = (1 << 63) - 1
INK = (220, 232, 239)
SUBTLE = (128, 150, 164)
TEAL = (49, 210, 230)        # primary accent (now cyan, matching the Augur skin)
AMBER = (255, 178, 60)
RED = (255, 107, 130)
BLUE = (120, 170, 255)
BACKGROUND = (6, 9, 13)
# --- Augur skin tokens (dark instrument theme) ---
PANEL = (14, 22, 30)
PANEL2 = (11, 18, 26)
STROKE = (28, 44, 56)
CY2 = (138, 242, 255)
GRN = (90, 224, 170)
MAG = (189, 123, 255)
MUT = (74, 92, 106)


_TOUCH_CONTROLLERS = ("ads7846", "xpt2046")


def resolve_touch_device(*, inventory=None):
    """Return the single XPT2046/ADS7846 touch node, or (None, reason).

    This identifies the resistive touch controller by its kernel name — it does
    NOT guess an arbitrary /dev/input/eventN. Exactly one matching node is
    required; zero or several is reported, not picked (matching the display
    acceptance 'MISSING'/'AMBIGUOUS' policy), so the display still runs without
    touch rather than opening the wrong device."""
    if inventory is None:
        from .acceptance import inventory as collect
        try:
            inventory = collect()
        except OSError as error:
            return None, f"input inventory unavailable ({type(error).__name__})"
    devices = inventory.get("input_devices") if isinstance(inventory, dict) else None
    if not isinstance(devices, list):
        return None, "input inventory invalid"

    def labels(row):
        parts = []
        for key in ("name", "modalias", "compatible"):
            value = row.get(key)
            if isinstance(value, str) and len(value) <= 256:
                parts.append(value.lower())
        return " ".join(parts)

    matches = [row for row in devices if isinstance(row, dict)
               and isinstance(row.get("device"), str)
               and FB_TOUCH_PATTERN.fullmatch(row["device"])
               and any(kind in labels(row) for kind in _TOUCH_CONTROLLERS)]
    if not matches:
        return None, "no XPT2046/ADS7846 touch node present"
    if len(matches) > 1:
        return None, "multiple touch nodes; select one explicitly"
    return matches[0]["device"], "resolved by controller name"


def framebuffer_info(device, *, sys_root=Path("/sys/class/graphics"),
                     statter=os.stat):
    """Require measured 480x320 RGB565-compatible 16-bit fb, with exact stride."""
    info = {"ok": False, "device": device, "reason": "", "name": "",
            "width": None, "height": None, "stride": None, "bpp": None}
    if not FB_PATTERN.fullmatch(device):
        info["reason"] = "select an explicit /dev/fbN (no guesses)"
        return info
    try:
        if not stat.S_ISCHR(statter(device).st_mode):
            raise ValueError("framebuffer is not a character device")
        fb = sys_root / Path(device).name
        dims = (fb / "virtual_size").read_text().strip()
        if not re.fullmatch(r"[0-9]{2,4},[0-9]{2,4}", dims):
            raise ValueError("invalid virtual_size")
        width, height = [int(x) for x in dims.split(",")]
        bpp = int((fb / "bits_per_pixel").read_text().strip())
        stride = int((fb / "stride").read_text().strip())
        name = (fb / "name").read_text().strip()[:80]
        info.update(width=width, height=height, bpp=bpp,
                    stride=stride, name=name)
        if (width, height) != RESOLUTION:
            raise ValueError("requires verified 480x320 framebuffer")
        if bpp != 16 or stride < width * 2 or stride > 8192:
            raise ValueError("requires 16bpp framebuffer with supported stride")
        info["ok"] = True
        info["reason"] = "verified fb node, resolution, 16bpp, and stride"
    except (OSError, ValueError) as exc:
        info["reason"] = f"framebuffer preflight failed: {exc}"
    return info


def read_status(port=8080, *, opener=None):
    """A bounded, proxy-free, no-redirect read of the local Redux API."""
    if not 1 <= port <= 65535:
        raise ValueError("invalid status port")
    request = open_status if opener is None else opener
    try:
        with request(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            payload = response.read(MAX_STATUS + 1)
        if len(payload) > MAX_STATUS:
            raise ValueError("status response is oversized")
        status = json.loads(payload)
        if (not isinstance(status, dict)
                or not isinstance(status.get("runtime"), dict)
                or not isinstance(status.get("doctor"), dict)):
            raise ValueError("not a Redux live-status report")
        return status
    except (OSError, ValueError, TypeError, UnicodeError,
            TimeoutError, urllib.error.HTTPError):
        return None


def _safe_text(value, max_len=55):
    # Live API labels must be strings. Never stringify untrusted nested
    # objects or enormous integers in the refresh/render hot path.
    if not isinstance(value, str):
        return "UNKNOWN"
    return "".join(ch if 32 <= ord(ch) < 127 else " " for ch in value[:max_len])


def _bounded_count(value):
    """Only measured unsigned 64-bit-range counters may become TFT metrics."""
    return value if type(value) is int and 0 <= value <= MAX_MEASURED_COUNT else None

def _bounded_seconds(value):
    """Accept only finite, bounded retry durations from a fresh status report."""
    if type(value) not in (int, float):
        return None
    try:
        return round(value) if math.isfinite(value) and 0 <= value <= 86400 else None
    except (OverflowError, ValueError, TypeError):
        return None


def _valid_utc_clock(value):
    """A non-finite or malformed clock can never prove live telemetry."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except (OverflowError, ValueError):
        return False


def status_view(report, *, now=None):
    """Conservative display model with no synthetic success values."""
    now = time.time() if now is None else now
    runtime = report.get("runtime", {}) if isinstance(report, dict) else {}
    doctor = report.get("doctor", {}) if isinstance(report, dict) else {}
    pipeline = report.get("capture_processing", {}) if isinstance(report, dict) else {}
    # Validate structure and both clocks before comparing ages. In particular
    # NaN compares false in *both* directions and otherwise looks fresh.
    if not isinstance(runtime, dict):
        runtime = {}
    if not isinstance(doctor, dict):
        doctor = {}
    if not isinstance(pipeline, dict):
        pipeline = {}
    updated = runtime.get("updated_utc")
    stale = (not _valid_utc_clock(updated) or not _valid_utc_clock(now)
             or updated > now + 30 or now - updated > 20)
    health = doctor.get("overall", "unknown")
    if health not in ("ok", "attention", "degraded", "action"):
        health = "unknown"
    running = runtime.get("state") == "running"
    if stale:
        health = "unknown"
    state = "STALE" if stale else _safe_text(runtime.get("state"), 18).upper()
    if not report:
        state = "OFFLINE"
    engine = "ACTIVE" if running and not stale else state
    count = lambda key: _bounded_count(runtime.get(key))
    raw_link = runtime.get("capture_link_type")
    # Treat link-type metadata as measured only when the whole runtime sample
    # is fresh. Old radiotap evidence must never look like current RF status.
    # Constrain malformed values before formatting; huge integers can raise
    # during decimal conversion and must not crash a physical dashboard.
    link = (raw_link if type(raw_link) is int and 0 <= raw_link <= 65535
            and not stale else None)
    if stale:
        rf_label = "RF NOT CURRENT"
    elif link is None:
        rf_label = "RF UNVERIFIED"
    elif link in (801, 803):
        # A kernel header type can never prove actual received 802.11 frames.
        rf_label = f"802.11 {link} / RX UNVERIFIED"
    elif link == 1:
        rf_label = "ETHERNET 1 / NO NATIVE RF"
    else:
        rf_label = f"UNQUALIFIED LINK {link}"

    # Display health cannot be greener than fresh, directly measured runtime
    # evidence. Doctor summaries normally agree; this also fails safely when
    # they are delayed, inconsistent, or partially unavailable.
    if not stale:
        severity = {"ok": 0, "attention": 1, "degraded": 2, "action": 3}
        measured = None
        runtime_state = runtime.get("state")
        if runtime_state in ("storage_paused", "lease_blocked", "termination_pending"):
            measured = "action"
        elif runtime_state == "degraded" or (running and link == 1):
            measured = "degraded"
        elif runtime_state in ("starting", "starting_engine", "rotating", "stopped"):
            measured = "attention"
        # A fresh process can still report a current failure.
        if runtime.get("last_error") or (
                _bounded_count(runtime.get("failure_streak")) not in (None, 0)):
            measured = max((measured, "degraded"), key=lambda v: severity.get(v, -1))
        if measured is not None and severity.get(health, -1) < severity[measured]:
            health = measured

    free = _bounded_count(runtime.get("free_bytes"))
    free_mb = round(free / 1048576) if free is not None else None
    results = pipeline.get("by_status", {}) if isinstance(pipeline.get("by_status"), dict) else {}
    errors = _bounded_count(results.get("error"))
    findings = doctor.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    actionable = next(
        (f for f in findings if isinstance(f, dict)
         and f.get("status") in ("action", "degraded", "attention")),
        None,
    )
    # Doctor's detailed page only promotes *fresh*, bounded, actionable
    # findings. Metadata-only or stale reports never make claims of health.
    doctor_rows = []
    if not stale:
        for finding in findings[:64]:
            if not isinstance(finding, dict):
                continue
            severity = finding.get("status")
            if severity not in ("action", "degraded", "attention"):
                continue
            summary = _safe_text(finding.get("summary"), 47).strip() or "UNKNOWN"
            doctor_rows.append({"status": severity, "summary": summary})
        order = {"action": 0, "degraded": 1, "attention": 2}
        doctor_rows.sort(key=lambda item: order[item["status"]])
        if doctor_rows:
            # The summary cannot override a verified worse individual finding.
            rank = {"ok": 0, "attention": 1, "degraded": 2, "action": 3}
            most_severe = doctor_rows[0]["status"]
            if rank.get(health, -1) < rank[most_severe]:
                health = most_severe
    return {
        "health": health, "state": state, "engine": engine,
        "iface": _safe_text(runtime.get("iface") or "UNKNOWN", 18),
        "rf_link_type": link,
        "rf_label": rf_label,
        "events": count("events_seen"),
        "handoffs": count("handed_off"),
        "storage_mb": free_mb,
        "processed": _bounded_count(results.get("ready")),
        "errors": errors, "pipeline_available": pipeline.get("available") is True,
        "retry_seconds": _bounded_seconds(runtime.get("retry_in_seconds")) if not stale else None,
        "failure_streak": _bounded_count(runtime.get("failure_streak")) if not stale else None,
        "engine_attempts": _bounded_count(runtime.get("engine_attempts")) if not stale else None,
        "capture_mount": runtime.get("capture_mount") if not stale and type(runtime.get("capture_mount")) is bool else None,
        "finding": _safe_text(actionable.get("summary"), 55) if actionable else
                   ("No actionable findings" if health == "ok" else "No verified health detail"),
        "detail": _safe_text(actionable.get("remediation"), 67) if actionable else
                  ("Live readings" if not stale else "Waiting for fresh live telemetry"),
        "doctor_rows": doctor_rows[:3],
        "doctor_all": doctor_rows,
        "doctor_more": max(0, len(doctor_rows) - 3),
        "stale": stale,
    }


def doctor_window(view, offset=0):
    """Bound scrolling to current verified findings; no stale detail replay."""
    if type(offset) is not int or offset < 0:
        raise ValueError("doctor offset must be a nonnegative integer")
    if not isinstance(view, dict):
        raise TypeError("doctor view must be a mapping")
    if view.get("stale") is not False:
        return {"rows": [], "start": 0, "total": 0, "more": 0}
    rows = view.get("doctor_all", [])
    if not isinstance(rows, list):
        return {"rows": [], "start": 0, "total": 0, "more": 0}
    rows = rows[:64]
    total = len(rows)
    first = min(offset, max(0, total - 3))
    return {"rows": rows[first:first + 3], "start": first, "total": total,
            "more": max(0, total - first - 3)}


@lru_cache(maxsize=24)
def _font(size=13, *, bold=False):
    from PIL import ImageFont
    root = "/usr/share/fonts/truetype/dejavu/"
    file = ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(root + file, size)
    except OSError:
        return ImageFont.load_default()



def fit_text(draw, value, font, width):
    """Fit bounded literal text to measured glyph widths, keeping an ellipsis."""
    value = _safe_text(value, 180)
    if draw.textlength(value, font=font) <= width:
        return value
    left, right = 0, len(value)
    while left < right:
        middle = (left + right + 1) // 2
        if draw.textlength(value[:middle] + "...", font=font) <= width:
            left = middle
        else:
            right = middle - 1
    return value[:left] + "..." if left else ""

# --- Augur skin drawing helpers (pure Pillow; cheap, no per-pixel loops) ---
def _spaced(draw, pos, text, font, fill, track):
    """Letter-spaced wordmark; returns the x past the last glyph."""
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + track
    return x


def _aperture(draw, cx, cy, r, color):
    """The Augur mark — an aperture/eye: faint outer ring, a lens, a bright core."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(40, 70, 86), width=1)
    lens = (cx - r, cy - int(r * 0.6), cx + r, cy + int(r * 0.6))
    draw.arc(lens, 200, 340, fill=color, width=2)
    draw.arc(lens, 20, 160, fill=color, width=2)
    draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=color)
    for a in range(0, 360, 45):
        px = cx + int((r - 3) * math.cos(math.radians(a)))
        py = cy + int((r - 3) * math.sin(math.radians(a)))
        draw.point((px, py), fill=(70, 130, 160))


def _panel(draw, box, *, radius=8, fill=PANEL, outline=STROKE, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _stat_value(rows, label):
    """Compact a live evidence row's value for a header chip ('47.0 C' -> '47°')."""
    for row in rows:
        if row.get("label") == label:
            value = row.get("value")
            if not isinstance(value, str) or value in ("Unavailable", ""):
                return "--"
            return value.replace(".0", "").replace(" C", "°").replace(" ", "")
    return "--"


def _arc_gauge(draw, cx, cy, r, frac, color, big, small):
    """A ring gauge with a centered figure, for the system/health pages."""
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.arc(box, 0, 360, fill=(31, 45, 56), width=5)
    try:
        frac = max(0.0, min(1.0, float(frac)))
    except (TypeError, ValueError):
        frac = 0.0
    if frac > 0:
        draw.arc(box, -90, -90 + int(360 * frac), fill=color, width=5)
    bf = _font(13, bold=True)
    bb = draw.textbbox((0, 0), big, font=bf)
    draw.text((cx - (bb[2] - bb[0]) / 2, cy - (bb[3] - bb[1]) / 2 - 3), big, font=bf, fill=INK)
    sf = _font(8)
    sb = draw.textbbox((0, 0), small, font=sf)
    draw.text((cx - (sb[2] - sb[0]) / 2, cy + 9), small, font=sf, fill=SUBTLE)


def _mode_dial(draw, cx, cy, r, is_lab):
    """The FIELD/LAB selector dial — the signature control from the Augur skin."""
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.arc(box, 95, 265, fill=AMBER if not is_lab else (74, 58, 30), width=6)   # FIELD (left)
    draw.arc(box, -85, 85, fill=TEAL if is_lab else (28, 66, 80), width=6)         # LAB (right)
    for a in range(0, 360, 15):
        r1, r2 = r - 11, r - 17
        draw.line((cx + int(r1 * math.cos(math.radians(a))), cy + int(r1 * math.sin(math.radians(a))),
                   cx + int(r2 * math.cos(math.radians(a))), cy + int(r2 * math.sin(math.radians(a)))),
                  fill=(58, 76, 90), width=1)
    for rr, col in ((r - 22, (20, 30, 40)), (r - 30, (13, 21, 29))):
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col, outline=(36, 54, 66), width=1)
    acc = TEAL if is_lab else AMBER
    draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=acc)
    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(8, 14, 20))
    pa = 0 if is_lab else 180
    px = cx + int((r - 3) * math.cos(math.radians(pa)))
    py = cy + int((r - 3) * math.sin(math.radians(pa)))
    draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(255, 255, 255))


# The on-screen map of everything Augur can do, grouped for a 3.5" panel. Static
# data (cheap to draw); the menu page renders it so every capability is
# discoverable from the screen even when its action runs via CLI/web.
_MENU_GROUPS = (
    ("STATUS", "overview captures radios", "system trends doctor"),
    ("RECON", "survey dex signals", "tracker tpms netrecon"),
    ("CRACK", "crack wordlist", "pipeline distribute"),
    ("SDR / RF", "sdr spectrum waterfall", "subghz ir  (TX=LAB)"),
    ("OPS", "mode scope karma", "wps eap portal"),
    ("SYSTEM", "gps toolbox learn", "oui push notebook"),
)

# Friendly dock labels for the footer (cosmetic only; navigation uses the page keys).
_DOCK_LABELS = {
    "overview": "HOME", "ops": "OPS", "captures": "CACHE", "doctor": "HEALTH", "radios": "RADIO",
    "trends": "TREND", "system": "SYS", "signals": "AIR", "power": "MODE", "menu": "MENU",
}


def render(report, *, page="overview", now=None, doctor_offset=0, trends=None,
           system_offset=0, system_detail=None, mode=None, mode_arming=False,
           panels=None):
    """Pillow RGB image, no placeholder activity, no radio-facing operations.

    `panels` is an optional dict of pre-sampled snapshots for the data-backed new
    pages (e.g. {'signals': {...}}). It's sampled by the loop on its cadence, not
    per-render, so render stays pure/cheap; a page degrades to a clean hint when
    its panel is absent."""
    from PIL import Image, ImageDraw
    data = status_view(report, now=now)
    doctor = doctor_window(data, doctor_offset) if page == "doctor" else None
    from .gesture_nav import PAGES
    if page not in PAGES:
        raise ValueError("unknown framebuffer page")
    img = Image.new("RGB", RESOLUTION, BACKGROUND)
    px = img.load()
    # Header band — a subtle vertical gradient, brightest at the very top.
    for y in range(0, 124):
        t = 1 - y / 124.0
        row = (int(7 + 7 * t), int(12 + 11 * t), int(17 + 15 * t))
        for x in range(480):
            px[x, y] = row
    draw = ImageDraw.Draw(img)

    def text_line(position, message, *, font, fill):
        draw.text(position, fit_text(draw, message, font, 462 - position[0]), font=font, fill=fill)

    from .ui_state import model as evidence_model
    evidence = evidence_model(report, now=now)
    colors = {"ok": TEAL, "attention": AMBER, "degraded": RED,
              "action": RED, "unknown": BLUE}
    accent = colors[data["health"]]
    # --- identity header (the mark, the wordmark, the mode pill, live stats) ---
    _aperture(draw, 27, 24, 11, TEAL)
    endx = _spaced(draw, (46, 11), "AUGUR", _font(20, bold=True), INK, 3)
    text_line((int(endx) + 6, 23), "REDUX", font=_font(9), fill=MUT)
    # Operating-mode pill. The whole header strip (y<40) is the tap-to-switch zone:
    # LAB is the armed/automatic posture (amber), FIELD the passive lock-down (cyan).
    # While arming, the pill shows a confirm prompt so a stray tap can't flip posture.
    is_lab = mode == "lab"
    label = "LAB" if is_lab else "FIELD" if mode == "field" else None
    if label:
        pill = AMBER if is_lab else TEAL
        if mode_arming:
            pill, label = RED, "TAP AGAIN"
        _panel(draw, (196, 12, 196 + len(label) * 9 + 26, 34), radius=11,
               fill=(13, 21, 29), outline=pill, width=1)
        draw.ellipse((205, 19, 212, 26), fill=pill)
        text_line((217, 15), label, font=_font(11, bold=True), fill=pill)
    # Live header stats pulled from the same fresh evidence model, plus a health dot.
    rows = evidence["rows"]
    temp = _stat_value(rows, "CPU temperature")
    text_line((360, 13), temp, font=_font(13, bold=True), fill=AMBER if temp != "--" else SUBTLE)
    text_line((360, 31), "TEMP", font=_font(7), fill=MUT)
    cpu_util = _stat_value(rows, "CPU utilization")
    text_line((410, 13), cpu_util, font=_font(13, bold=True), fill=INK)
    text_line((410, 31), "CPU", font=_font(7), fill=MUT)
    draw.ellipse((456, 17, 467, 28), fill=accent)
    draw.line((18, 43, 462, 43), fill=STROKE, width=1)
    text_line((20, 50), "ENGINE  " + data["engine"], font=_font(19, bold=True), fill=accent)
    text_line((20, 80), "RADIO  " + data["iface"], font=_font(12), fill=SUBTLE)
    # Link-layer compatibility is not evidence of received RF frames.
    rf_color = RED if data["rf_link_type"] == 1 else SUBTLE
    text_line((180, 80), data["rf_label"], font=_font(10, bold=True), fill=rf_color)
    draw.line((18, 123, 462, 123), fill=STROKE, width=1)

    if page != "radios":
        cpu, load, memory = evidence["rows"][:3]
        summary = "CPU " + cpu["value"] + " / " + load["value"] + " / RAM " + memory["value"]
        summary += " / " + evidence["sensors"]["state"].upper()
        text_line((20, 101), summary, font=_font(9), fill=TEAL if cpu["state"] == "fresh" else AMBER)

    def metric(x, label, value):
        text_line((x, 164), label, font=_font(11), fill=SUBTLE)
        text_line((x, 187), value, font=_font(23, bold=True), fill=INK)

    value = lambda n: str(n) if n is not None else "--"
    if page == "overview":
        metric(20, "EVENTS", value(data["events"]))
        metric(180, "FILE HANDOFFS", value(data["handoffs"]))
        metric(352, "FREE MB", value(data["storage_mb"]))
    elif page == "captures":
        from .capture_panel import model as capture_model, compact_count
        capture = capture_model(report, now=now)
        metric(20, "PREPARED", compact_count(capture["ready"]))
        metric(180, "FAILED", compact_count(capture["errors"]))
        metric(350, "HASH ROWS", compact_count(capture["hash_records"]))
    elif page == "ops":
        # Real-time operations: what the box is doing + how detectable it is now.
        rpt = report if isinstance(report, dict) else {}
        st = rpt.get("stealth") or {}
        fp = st.get("footprint") or {}
        pl = rpt.get("passive_loot") or {}
        eng = rpt.get("engagement") or None
        caps = (rpt.get("capabilities") or {}).get("by_state") or {}
        metric(20, "FOOTPRINT", str(fp.get("label") or "--").upper())
        metric(180, "PMKID NEW", value(pl.get("new_loot")))
        metric(352, "READY", value(caps.get("ready")))
        quiet = "ON" if st.get("quiet") else "off"
        mac = "on" if st.get("randomize_mac") else "off"
        score = fp.get("score")
        text_line((20, 228),
                  "STEALTH  quiet " + quiet + " / score " + (str(score) if score is not None else "--")
                  + " / budget " + str(st.get("frame_budget", 0)) + " / MAC " + mac,
                  font=_font(10), fill=TEAL if st.get("quiet") else SUBTLE)
        if pl:
            loot = ("LOOT  " + str(pl.get("new_loot", 0)) + " new / " + str(pl.get("aps_leaking_pmkid", 0))
                    + " leaking / " + str(pl.get("footprint", "silent")))
        else:
            loot = "LOOT  no passive sweep recorded"
        text_line((20, 246), loot, font=_font(10), fill=SUBTLE)
        if eng:
            case = ("CASE  " + str(eng.get("name", "?")) + " [" + str(eng.get("status", "?")) + "] / "
                    + str(eng.get("authorization", "")))
        else:
            case = "CASE  no active engagement"
        text_line((20, 264), case, font=_font(10), fill=AMBER if eng else SUBTLE)
    elif page == "system":
        system = evidence
        rows = system["rows"]
        offset = min(max(0, system_offset), max(0, len(rows) - 3))
        if system_detail is not None:
            selected = rows[min(max(0, system_detail), len(rows) - 1)]
            text_line((20, 155), selected["label"].upper(), font=_font(12, bold=True), fill=INK)
            detail = selected["value"] + " / " + selected["source"]
            remaining = _safe_text(detail, 340)
            y = 175
            while remaining and y <= 271:
                fitted = fit_text(draw, remaining, _font(10), 440)
                length = len(fitted) - 3 if fitted.endswith("...") else len(fitted)
                if length <= 0:
                    break
                text_line((20, y), remaining[:length], font=_font(10), fill=INK)
                remaining = remaining[length:]
                y += 12
            stamp = selected["measured_at"]
            measured = f"at {stamp:g}" if stamp is not None else "timestamp unavailable"
            text_line((20, 285), selected["state"].upper() + " / " + measured + " / TAP TO RETURN", font=_font(9), fill=AMBER)
        else:
            text_line((20, 155), "SYSTEM / tap detail / swipe up-down", font=_font(11, bold=True), fill=SUBTLE)
            for index, row in enumerate(rows[offset:offset + 3]):
                y = 177 + index * 34
                text_line((20, y), fit_text(draw, row["label"], _font(11), 166), font=_font(11), fill=SUBTLE)
                text_line((196, y), _safe_text(row["value"], 30), font=_font(12, bold=True), fill=INK)
                stamp = row["measured_at"]
                measured = f"at {stamp:g} / " if stamp is not None else ""
                detail = row["state"].upper() + " / " + measured + row["source"]
                text_line((20, y + 16), _safe_text(detail, 100), font=_font(9), fill=TEAL if row["state"] == "fresh" else AMBER)
            text_line((20, 285), f"{offset + 1}-{min(offset + 3, len(rows))} / {len(rows)} / Independent measurement timestamps", font=_font(8), fill=SUBTLE)
    elif page == "trends":
        from .trend_chart import draw_trends
        draw_trends(draw, trends, font=_font, ink=INK, subtle=SUBTLE,
                    teal=TEAL, amber=AMBER, blue=BLUE)
    elif page == "radios":
        from .radio_panel import model as radio_model
        radios = radio_model(report, now=now)
        snapshot_only = radios["inventory"] == "SAVED SNAPSHOT"
        readiness = radios["readiness"]
        rt_age = readiness["runtime_age_seconds"]
        inv_age = readiness["inventory_age_seconds"]
        age_label = ("RT -- / INV --" if rt_age is None or inv_age is None else
                     f"RT {rt_age:g}s / INV {inv_age:g}s")
        text_line((20, 112),
                  "SAVED INVENTORY / HISTORICAL - NOT LIVE" if snapshot_only
                  else "DUAL / " + readiness["state"] + " / " + age_label,
                  font=_font(10, bold=True),
                  fill=AMBER if snapshot_only or readiness["state"] != "UNVERIFIED" else SUBTLE)
        blockers = readiness["blockers"]
        text_line((20, 147), "BLOCK / " + (" + ".join(blockers[:2]) if blockers else "NONE"),
                  font=_font(8), fill=AMBER)
        radio_capture = radios["capture"]
        text_line((20, 162), "BETTERCAP / " + radio_capture["iface"],
                  font=_font(12, bold=True), fill=INK)
        text_line((20, 181), radio_capture["state"] + " / " + radio_capture["framing"],
                  font=_font(10), fill=SUBTLE)
        text_line((20, 197), "KERNEL TYPE " +
                  (str(radio_capture["link"]) if radio_capture["link"] is not None else "--") +
                  " / RX UNVERIFIED", font=_font(10), fill=AMBER)
        draw.line((18, 214, 462, 214), fill=(36, 57, 69), width=1)
        onboard = radios["onboard"]
        text_line((20, 219), "ONBOARD / " + onboard["iface"] + " / " +
                  onboard["state"] + " / " + onboard["driver"],
                  font=_font(10, bold=True),
                  fill=INK if onboard["state"] == "OBSERVED" else AMBER)
        text_line((20, 237), "MODE " + onboard["mode"] + " / LINK " +
                  (str(onboard["link"]) if onboard["link"] is not None else "--") +
                  " / RX UNVERIFIED", font=_font(9), fill=AMBER)
        draw.line((18, 253, 462, 253), fill=(36, 57, 69), width=1)
        usb = radios["usb"]
        text_line((20, 258), "USB / " + usb["iface"] + " / " +
                  usb["state"] + " / " + usb["driver"],
                  font=_font(10, bold=True), fill=INK if usb["state"] == "OBSERVED" else AMBER)
        text_line((20, 278), "MODE " + usb["mode"] + " / LINK " +
                  (str(usb["link"]) if usb["link"] is not None else "--") +
                  " / RX UNVERIFIED / OWNER UNVERIFIED",
                  font=_font(8), fill=AMBER)
        extras = radios.get("externals", [])[1:]   # additional dongles beyond the first
        if extras:
            label = "ALSO " + ", ".join(e["iface"] + "/" + e["mode"][:3] for e in extras[:4])
            if len(extras) > 4:
                label += " +" + str(len(extras) - 4)
            text_line((20, 296), label[:60], font=_font(8), fill=SUBTLE)
    elif page == "menu":
        text_line((20, 149), "MENU / everything Augur can do", font=_font(12, bold=True), fill=INK)
        # two-column grid of capability groups; a static map, cheap to draw
        col_x = (20, 246)
        for index, (name, line1, line2) in enumerate(_MENU_GROUPS):
            cx = col_x[index % 2]
            cy = 166 + (index // 2) * 42
            draw.rectangle((cx, cy, cx + 214, cy + 38), outline=(36, 57, 69), width=1)
            text_line((cx + 8, cy + 2), name, font=_font(11, bold=True), fill=TEAL)
            text_line((cx + 8, cy + 15), line1, font=_font(9), fill=SUBTLE)
            text_line((cx + 8, cy + 26), line2, font=_font(9), fill=SUBTLE)
    elif page == "power":
        from redux.core import power_tier as _pt
        m = (mode or "field").lower()
        is_lab = m == "lab"
        # The signature FIELD/LAB selector dial on the left.
        _mode_dial(draw, 106, 214, 54, is_lab)
        text_line((62, 276), "FIELD", font=_font(11, bold=True), fill=AMBER if not is_lab else MUT)
        text_line((138, 276), "LAB", font=_font(11, bold=True), fill=TEAL if is_lab else MUT)
        # Power-tier truth on the right.
        text_line((196, 150), "POWER TIER", font=_font(12, bold=True), fill=INK)
        text_line((196, 169), "LAB unlocks (confirm each)" if is_lab
                  else "FIELD hides strong tools", font=_font(8), fill=SUBTLE)
        powers = [c for c in _pt._REGISTRY.values() if c.tier == _pt.Tier.POWER]
        forbidden = [c for c in _pt._REGISTRY.values() if c.tier == _pt.Tier.FORBIDDEN]
        y = 188
        for c in powers[:5]:
            draw.ellipse((198, y + 3, 205, y + 10), fill=AMBER if is_lab else RED)
            tail = " unlocked" if is_lab else " LAB only"
            text_line((212, y), fit_text(draw, c.label, _font(9), 170) + " -" + tail,
                      font=_font(9), fill=INK if is_lab else SUBTLE)
            y += 16
        if forbidden:
            names = ", ".join(c.label.split("(")[0].strip() for c in forbidden)
            text_line((196, y + 3), "FORBIDDEN: " + fit_text(draw, names, _font(8), 190),
                      font=_font(8), fill=RED)
    elif page == "signals":
        text_line((20, 150), "SIGNAL DEX / everything seen or captured",
                  font=_font(12, bold=True), fill=INK)
        sig = (panels or {}).get("signals") if isinstance(panels, dict) else None
        if not isinstance(sig, dict) or not sig.get("total"):
            text_line((20, 180),
                      "0 catalogued yet" if isinstance(sig, dict) else "sampling...",
                      font=_font(13), fill=SUBTLE)
            text_line((20, 205), "WiFi/BLE/ISM/ADS-B/sub-GHz/IR fold in here",
                      font=_font(9), fill=SUBTLE)
            text_line((20, 222), "full list: `redux signals` (CLI) / web UI",
                      font=_font(9), fill=SUBTLE)
        else:
            text_line((20, 176), str(sig.get("total", 0)) + " SIGNALS", font=_font(20, bold=True), fill=TEAL)
            by = sig.get("by_domain", {}) if isinstance(sig.get("by_domain"), dict) else {}
            y = 206
            for medium, count in list(by.items())[:6]:
                text_line((20, y), fit_text(draw, str(medium).upper(), _font(11), 150),
                          font=_font(11), fill=SUBTLE)
                text_line((180, y), str(count), font=_font(12, bold=True), fill=INK)
                y += 18
    else:
        # HEALTH: live ring gauges over the doctor's glass-box findings.
        def _gnum(label, cap):
            for row in evidence["rows"]:
                if row.get("label") == label:
                    match = re.search(r"[-+]?\d*\.?\d+", row.get("value") or "")
                    if match:
                        val = float(match.group())
                        return val, max(0.0, min(1.0, val / cap))
            return None, 0.0
        tval, tf = _gnum("CPU temperature", 85)
        cval, cf = _gnum("CPU utilization", 100)
        mval, mf = _gnum("Memory utilization", 100)
        for cx, lbl, val, frac, col, unit in (
                (70, "TEMP", tval, tf, AMBER, "°"), (176, "CPU", cval, cf, GRN, "%"),
                (282, "MEM", mval, mf, TEAL, "%")):
            big = f"{val:.0f}{unit}" if val is not None else "--"
            _arc_gauge(draw, cx, 180, 25, frac, col, big, lbl)
        storage = data["storage_mb"]
        _arc_gauge(draw, 388, 180, 25, min(1.0, (storage or 0) / 4096), MAG,
                   str(storage) if storage is not None else "--", "FREE")
        draw.line((18, 214, 462, 214), fill=STROKE, width=1)
        text_line((20, 219), "DOCTOR / glass-box findings", font=_font(10, bold=True), fill=CY2)
        if data["stale"]:
            text_line((20, 244), "REPORT STALE / NO VERIFIED FINDINGS", font=_font(11), fill=AMBER)
        elif doctor["rows"]:
            for index, finding in enumerate(doctor["rows"]):
                y = 240 + index * 20
                color = colors[finding["status"]]
                badge = finding["status"].upper()
                _panel(draw, (20, y, 68, y + 15), radius=4, fill=None, outline=color, width=1)
                bw = draw.textlength(badge, font=_font(8, bold=True))
                draw.text((44 - bw / 2, y + 3), badge, font=_font(8, bold=True), fill=color)
                text_line((76, y + 2), finding["summary"], font=_font(10), fill=INK)
            if doctor["total"] > 3:
                visible = doctor["start"] + len(doctor["rows"])
                text_line((20, 296),
                          f"FINDINGS {doctor['start'] + 1}-{visible} / {doctor['total']}  SWIPE UP/DOWN",
                          font=_font(8), fill=SUBTLE)
        else:
            message = ("No actionable findings" if data["health"] == "ok"
                       else "No verified findings")
            text_line((20, 244), message, font=_font(11), fill=SUBTLE)

    if page == "doctor" and any(row["label"] == "Plugin diagnostics" for row in evidence["rows"]):
        text_line((20, 140), "PLUGIN INVENTORY UNAVAILABLE / SEE SYSTEM", font=_font(9), fill=AMBER)

    if page == "captures":
        draw.line((18, 238, 462, 238), fill=(36, 57, 69), width=1)
        status = capture["ledger"]
        scan = capture["scan_state"]
        indicator = RED if status != "AVAILABLE" else SUBTLE
        text_line((20, 247), "LEDGER / " + status,
                  font=_font(11, bold=True), fill=indicator)
        scan_label = "WORKER / " + scan
        if scan == "RECENT":
            scan_label += f" / {capture['scan_age_seconds']}s ago"
        text_line((20, 266), scan_label, font=_font(11),
                  fill=AMBER if scan in ("STALE", "PAUSED STORAGE", "INVALID")
                  else SUBTLE)
        if scan == "RECENT":
            text_line((20, 282),
                      "PASS " + compact_count(capture["scan_files"]) +
                      " FILES   READY " + compact_count(capture["last_ready"]) +
                      "   ERROR " + compact_count(capture["last_errors"]) +
                      "   DEFER " + compact_count(capture["last_deferred"]),
                      font=_font(9), fill=SUBTLE)
    elif page == "overview":
        draw.line((18, 238, 462, 238), fill=(36, 57, 69), width=1)
        text_line((20, 250), "DOCTOR / " + data["finding"], font=_font(12, bold=True), fill=accent)
        text_line((20, 272), data["detail"], font=_font(10), fill=SUBTLE)
        retry = f"{data['retry_seconds']}s" if data["retry_seconds"] is not None else "--"
        failures = value(data["failure_streak"])
        attempts = value(data["engine_attempts"])
        mount = ("OK" if data["capture_mount"] is True else
                 "MISSING" if data["capture_mount"] is False else "UNKNOWN")
        runtime_color = (AMBER if data["failure_streak"] not in (None, 0)
                         or data["capture_mount"] is False else SUBTLE)
        text_line((20, 285),
                  f"RETRY {retry} / FAILURES {failures} / STARTS {attempts} / REDUXCAP {mount}",
                  font=_font(8), fill=runtime_color)
    # Footer tabs share geometry with gesture_nav.TAB_TOP/TAB_WIDTH.
    # They are visual only until the physical touch path is opted in.
    from .gesture_nav import PAGES, TAB_TOP, TAB_WIDTH
    from .ui_state import navigation
    # Move the content intact: no glyph resampling on a small physical panel.
    content = img.crop((0, 147, 480, 296))
    draw.rectangle((0, 124, 479, 319), fill=BACKGROUND)
    img.paste(content, (0, 127))
    draw.rectangle((0, TAB_TOP, 479, 319), fill=(9, 15, 21))
    draw.line((0, TAB_TOP, 479, TAB_TOP), fill=STROKE, width=1)
    for index, tab in enumerate(navigation(page)):
        left = index * TAB_WIDTH
        selected = tab == page
        if selected:
            draw.rectangle((left + 2, TAB_TOP + 1, left + TAB_WIDTH - 3, 319), fill=(16, 28, 36))
            draw.rectangle((left + 16, TAB_TOP, left + TAB_WIDTH - 17, TAB_TOP + 2), fill=TEAL)
        label = _DOCK_LABELS.get(tab, tab.upper())
        font = _font(10, bold=selected)
        width = draw.textlength(label, font=font)
        draw.text((left + (TAB_WIDTH - width) / 2, TAB_TOP + 15), label,
                  font=font, fill=CY2 if selected else SUBTLE)
    if data["stale"]:
        text_line((330, 50), "NOT VERIFIED", font=_font(10, bold=True), fill=AMBER)
    return img


def rgb565_bytes(img, *, stride, order="rgb565"):
    """Pack each actual RGB pixel to little-endian 16-bit framebuffer rows."""
    if order not in ("rgb565", "bgr565"):
        raise ValueError("pixel order must be rgb565 or bgr565")
    width, height = img.size
    if width != 480 or height != 320 or stride < width * 2 or stride > 8192:
        raise ValueError("unsupported framebuffer geometry")
    rgb = img.convert("RGB").tobytes()
    output = bytearray(stride * height)
    for i in range(width * height):
        r, g, b = rgb[i * 3:i * 3 + 3]
        if order == "bgr565":
            r, b = b, r
        v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        p = (i // width) * stride + (i % width) * 2
        output[p] = v & 255
        output[p + 1] = v >> 8
    return bytes(output)


def write_frame(device, frame, *, opener=os.open, writer=os.write,
                closer=os.close, checker=os.fstat):
    """Only write the explicitly selected character device; no mmap or ioctl."""
    if not FB_PATTERN.fullmatch(device):
        raise ValueError("unsafe framebuffer device")
    fd = opener(device, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        if not stat.S_ISCHR(checker(fd).st_mode):
            raise ValueError("selected framebuffer is not a character device")
        offset = 0
        while offset < len(frame):
            n = writer(fd, frame[offset:])
            if n <= 0:
                raise OSError("framebuffer write made no progress")
            offset += n
    finally:
        closer(fd)


def _spawn_daemon_restart():
    """Fire-and-forget restart of the live daemon so a mode switch never blocks
    the display loop. Returns True if the restart was launched."""
    import subprocess
    try:
        subprocess.Popen(["systemctl", "restart", "redux-live.service"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _sample_signals_panel():
    """Sample the Signal Dex summary for the signals page. Bounded + fail-safe:
    any error returns None (the page then shows a clean hint). Called at most
    every 15s, only while that page is viewed, so it never affects idle heat."""
    try:
        from redux.dex.signal_dex import build_signal_dex
        from redux.geo import SightingStore, DEFAULT_DB_PATH
        from redux.sdr.subghz import SubGhzLibrary
        from redux.ir import IRLibrary
        view = build_signal_dex(
            sighting_store=SightingStore(DEFAULT_DB_PATH),
            subghz_lib=SubGhzLibrary("/captures/config/subghz.json"),
            ir_lib=IRLibrary("/captures/config/ir.json"))
        return view.summary
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Opt-in Redux native 480x320 framebuffer dashboard")
    parser.add_argument("--framebuffer", required=True,
                        help="actual verified /dev/fbN from redux-display-diag")
    parser.add_argument("--port", type=int, default=8080)
    from .gesture_nav import PAGES
    parser.add_argument("--page", choices=PAGES,
                        default="overview")
    parser.add_argument("--order", choices=("rgb565", "bgr565"), default="rgb565",
                        help="manually validated panel pixel order")
    parser.add_argument("--rotate", type=int, choices=(0, 180), default=0,
                        help="operator-confirmed software rotation")
    parser.add_argument("--touch-device",
                        help="opt-in, physically verified /dev/input/eventN (run mode only)")
    parser.add_argument("--touch-auto", action="store_true",
                        help="resolve the single XPT2046/ADS7846 node by name (run mode only); "
                             "display continues without touch if zero or several are present")
    parser.add_argument("--calibration-file", help="absolute five-point JSON file; run mode only")
    parser.add_argument("--confirm-touch-calibration", action="store_true",
                        help="operator confirms visible 480x320 panel and physical touch mapping")
    parser.add_argument("--swipe-axis", choices=("x", "y"), default="x",
                        help="actual raw horizontal swipe axis; use display diagnostics")
    parser.add_argument("--swipe-reverse", action="store_true",
                        help="invert swipe direction for rotated touch controllers")
    parser.add_argument("--swipe-threshold", type=int, default=300,
                        help="minimum raw axis movement for one swipe")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="read-only hardware/API checks")
    mode.add_argument("--once", action="store_true", help="paint one measured frame")
    mode.add_argument("--run", action="store_true", help="refresh every two seconds")
    args = parser.parse_args(argv)
    if args.touch_auto and args.touch_device:
        parser.error("--touch-auto and --touch-device are mutually exclusive")
    if args.touch_auto and not args.run:
        parser.error("--touch-auto requires --run; --once and --check never read touch")
    # Resolve the touch node by controller name when asked. A miss is not fatal:
    # the display runs without touch, exactly as an unset --touch-device would.
    if args.touch_auto and args.run:
        resolved, reason = resolve_touch_device()
        if resolved is not None:
            args.touch_device = resolved
        else:
            print(f"Touch auto-detect: {reason}; display continues without touch.",
                  file=sys.stderr)
    if args.touch_device and not args.run:
        parser.error("--touch-device requires --run; --once and --check never read touch")
    if args.calibration_file and (not args.run or not args.touch_device
                                  or not args.confirm_touch_calibration):
        parser.error("calibrated touch requires --run, --touch-device and explicit confirmation")
    if args.confirm_touch_calibration and not args.calibration_file:
        parser.error("--confirm-touch-calibration requires --calibration-file")
    if args.calibration_file and (args.swipe_axis != "x" or args.swipe_reverse
                                  or args.swipe_threshold != 300):
        parser.error("manual swipe orientation/threshold flags conflict with calibration")
    fb = framebuffer_info(args.framebuffer)
    if args.check:
        print(json.dumps({"framebuffer": fb,
                          "dashboard": read_status(args.port) is not None,
                          "physical_display_verified": False,
                          "touch_verified": False}, sort_keys=True))
        return 0 if fb["ok"] else 2
    if not fb["ok"]:
        print(f"Redux fbui preflight: {fb['reason']}", file=sys.stderr)
        return 2
    running = True
    touch_fd = None
    tracker = None
    reconnect = None
    fragment = b""
    system_offset = 0
    system_detail = None
    last_frame = None
    page = args.page
    doctor_offset = 0
    from .telemetry_history import TelemetryWindow
    history = TelemetryWindow()
    if args.touch_device:
        from . import touch_nav
        if args.calibration_file:
            from .gesture_nav import NavigationGestures
            try:
                calibration = calibration_file.load_calibration(args.calibration_file)
                tracker = NavigationGestures(
                    calibration, physically_verified=True, rotation=args.rotate)
            except (OSError, ValueError, TypeError) as exc:
                print(f"Touch calibration refused: {type(exc).__name__}", file=sys.stderr)
                return 2
        else:
            tracker = touch_nav.SwipeTracker(
                axis=args.swipe_axis, threshold=args.swipe_threshold,
                reverse=args.swipe_reverse,
            )
        try:
            touch_fd = touch_nav.open_touch(args.touch_device)
            reconnect = touch_nav.TouchReconnect(args.touch_device, touch_fd,
                opener=touch_nav.open_touch, closer=os.close)
        except OSError:
            print("Touch unavailable at startup; display continues. Restart after verifying the selected input.", file=sys.stderr)

    def stop(_signum, _frame):
        nonlocal running
        running = False

    previous = {}
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name)
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, stop)
    from ..core.mode import read_mode, toggle_mode
    mode_arm_until = 0.0          # header-tap mode switch: armed by the first tap
    signals_panel = None          # Signal Dex snapshot for the signals page
    signals_panel_at = 0.0        # last sample time (refreshed slowly, only when viewed)
    try:
        while running:
            if reconnect is not None and touch_fd is None:
                recovered = reconnect.poll()
                if recovered is not None:
                    touch_fd = recovered
                    fragment = b""
                    if args.calibration_file:
                        tracker = NavigationGestures(calibration, physically_verified=True, rotation=args.rotate)
                        tracker.awaiting_release = True
                    else:
                        tracker = touch_nav.SwipeTracker(axis=args.swipe_axis, threshold=args.swipe_threshold, reverse=args.swipe_reverse)
                        tracker.desynced = True
            report = read_status(args.port)
            sampled_at = time.time()
            current_mode = read_mode()
            # Only consult the clock when a switch is actually armed, so the common
            # (unarmed) path adds no monotonic() call to the refresh loop.
            mode_arming = mode_arm_until > 0.0 and time.monotonic() < mode_arm_until
            # Only genuine, independently updated live status counters advance
            # the bounded in-memory chart; the UI never synthesizes activity.
            history.ingest(report, now=sampled_at)
            if page == "doctor":
                current_doctor = status_view(report, now=sampled_at)
                if current_doctor["stale"]:
                    doctor_offset = 0
                else:
                    doctor_offset = doctor_window(current_doctor, doctor_offset)["start"]
                image = render(report, page=page, now=sampled_at,
                               doctor_offset=doctor_offset,
                               mode=current_mode, mode_arming=mode_arming)
            elif page == "system":
                image = render(report, page=page, now=sampled_at, system_offset=system_offset,
                               system_detail=system_detail, mode=current_mode, mode_arming=mode_arming)
            elif page == "trends":
                image = render(report, page=page, now=sampled_at,
                               trends=history.snapshot(now=sampled_at),
                               mode=current_mode, mode_arming=mode_arming)
            else:
                # Live Signal Dex count for the signals page: sample at most every
                # 15s, and ONLY while that page is on screen, so the catalog read
                # never touches idle heat/CPU.
                if page == "signals" and (sampled_at - signals_panel_at) >= 15.0:
                    signals_panel = _sample_signals_panel()
                    signals_panel_at = sampled_at
                panels = {"signals": signals_panel} if signals_panel is not None else None
                image = render(report, page=page, now=sampled_at, panels=panels,
                               mode=current_mode, mode_arming=mode_arming)
            if args.rotate == 180:
                from PIL import Image
                image = image.transpose(Image.Transpose.ROTATE_180)
            frame = rgb565_bytes(image, stride=fb["stride"], order=args.order)
            if frame != last_frame:
                write_frame(args.framebuffer, frame)
                last_frame = frame
            if args.once:
                break
            deadline = time.monotonic() + 2
            while running and time.monotonic() < deadline:
                remaining = min(0.2, max(0, deadline - time.monotonic()))
                if touch_fd is None:
                    time.sleep(remaining)
                    continue
                try:
                    available, _, _ = select.select([touch_fd], [], [], remaining)
                except OSError:
                    reconnect.disconnect()
                    touch_fd = None
                    fragment = b""
                    break
                if not available:
                    continue
                try:
                    chunk = os.read(touch_fd, 4096)
                except BlockingIOError:
                    continue
                except OSError:
                    reconnect.disconnect()
                    touch_fd = None
                    fragment = b""
                    break
                if not chunk:
                    reconnect.disconnect()
                    touch_fd = None
                    fragment = b""
                    break  # keep status display alive after input disconnect
                fragment += chunk
                if len(fragment) > 8192:
                    raise OSError("unexpected unbounded touch input")
                if args.calibration_file:
                    from .gesture_nav import consume_gestures
                    tracker.page = page
                    actions, fragment = consume_gestures(fragment, tracker)
                    if actions:
                        action, value = actions[-1]
                        if action == "detail" and page == "system":
                            system_detail = system_offset + value if system_detail is None else None
                        elif action == "scroll":
                            if page == "system":
                                system_detail = None
                                from .ui_state import model
                                system_offset = min(max(0, len(model(report)["rows"]) - 3), max(0, system_offset + value * 3))
                                break
                            if page != "doctor":
                                continue
                            count = len(status_view(report)["doctor_all"])
                            doctor_offset = min(max(0, count - 3),
                                                max(0, doctor_offset + value * 3))
                        elif action == "page":
                            system_detail = None
                            if value != page:
                                doctor_offset = 0
                            page = value
                        elif action == "swipe":
                            system_detail = None
                            page = touch_nav.navigate(page, value)
                            doctor_offset = 0
                        elif action == "mode":
                            # Header tap: first arms, a second within 4s commits
                            # the LAB<->FIELD switch (and restarts the daemon).
                            nowm = time.monotonic()
                            if nowm < mode_arm_until:
                                mode_arm_until = 0.0
                                toggle_mode(restart=_spawn_daemon_restart)
                            else:
                                mode_arm_until = nowm + 4.0
                        else:
                            continue
                        break  # redraw selected page or Doctor scroll immediately
                else:
                    directions, fragment = touch_nav.consume(fragment, tracker)
                    if directions:
                        page = touch_nav.navigate(page, directions[-1])
                        break  # redraw new page immediately
    finally:
        if reconnect is not None:
            reconnect.close()
        for sig, old in previous.items():
            signal.signal(sig, old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
