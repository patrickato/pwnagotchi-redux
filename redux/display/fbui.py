"""Small, real-telemetry, browser-free RGB565 Redux TFT dashboard.

Opt-in only: --framebuffer must name a verified Linux /dev/fbN. We never
probe arbitrary /dev/fb0 as a fallback, choose input devices, start Xorg,
or change Wi-Fi, kernel overlays or touch calibration. UI reads the existing
loopback live-status API and never represents a missing sample as healthy.
"""
from __future__ import annotations

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

FB_PATTERN = re.compile(r"/dev/fb[0-9]{1,2}\Z")
RESOLUTION = (480, 320)
MAX_STATUS = 65536
MAX_MEASURED_COUNT = (1 << 63) - 1
INK = (223, 236, 242)
SUBTLE = (126, 153, 171)
TEAL = (64, 218, 186)
AMBER = (247, 190, 76)
RED = (255, 111, 107)
BLUE = (101, 175, 245)
BACKGROUND = (10, 15, 24)


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
        if runtime_state == "storage_paused":
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


def _font(size=13, *, bold=False):
    from PIL import ImageFont
    root = "/usr/share/fonts/truetype/dejavu/"
    file = ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(root + file, size)
    except OSError:
        return ImageFont.load_default()


def render(report, *, page="overview", now=None, doctor_offset=0, trends=None):
    """Pillow RGB image, no placeholder activity, no radio-facing operations."""
    from PIL import Image, ImageDraw
    data = status_view(report, now=now)
    doctor = doctor_window(data, doctor_offset) if page == "doctor" else None
    from .gesture_nav import PAGES
    if page not in PAGES:
        raise ValueError("unknown framebuffer page")
    img = Image.new("RGB", RESOLUTION, BACKGROUND)
    px = img.load()
    # Subtle, ambient header glow; the layout is intentionally not a tile grid.
    for y in range(0, 124):
        for x in range(480):
            falloff = max(0, 100 - ((x - 65) ** 2 // 320 + (y - 28) ** 2 // 110))
            if falloff:
                px[x, y] = (10, 15 + falloff // 9, 24 + falloff // 6)
    draw = ImageDraw.Draw(img)
    colors = {"ok": TEAL, "attention": AMBER, "degraded": RED,
              "action": RED, "unknown": BLUE}
    accent = colors[data["health"]]
    draw.text((18, 12), "REDUX", font=_font(21, bold=True), fill=INK)
    draw.text((108, 18), "AUGUR / LIVE", font=_font(11), fill=SUBTLE)
    draw.ellipse((434, 21, 447, 34), fill=accent)
    draw.text((354, 40), data["health"].upper(), font=_font(11, bold=True), fill=accent)
    draw.line((18, 65, 462, 65), fill=(36, 57, 69), width=2)
    draw.text((20, 78), "ENGINE  " + data["engine"], font=_font(20, bold=True), fill=accent)
    draw.text((20, 113), "RADIO  " + data["iface"], font=_font(13), fill=SUBTLE)
    # Link-layer compatibility is not evidence of received RF frames.
    rf_color = RED if data["rf_link_type"] == 1 else SUBTLE
    draw.text((180, 113), data["rf_label"], font=_font(10, bold=True), fill=rf_color)
    draw.line((18, 147, 462, 147), fill=(36, 57, 69), width=1)

    def metric(x, label, value):
        draw.text((x, 164), label, font=_font(11), fill=SUBTLE)
        draw.text((x, 187), value, font=_font(23, bold=True), fill=INK)

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
    elif page == "trends":
        from .trend_chart import draw_trends
        draw_trends(draw, trends, font=_font, ink=INK, subtle=SUBTLE,
                    teal=TEAL, amber=AMBER, blue=BLUE)
    elif page == "radios":
        from .radio_panel import model as radio_model
        radios = radio_model(report, now=now)
        snapshot_only = radios["inventory"] == "SAVED SNAPSHOT"
        draw.text((20, 132),
                  "SAVED INVENTORY / HISTORICAL - NOT LIVE" if snapshot_only
                  else "INVENTORY / " + radios["inventory"],
                  font=_font(10, bold=True),
                  fill=AMBER if snapshot_only else SUBTLE)
        radio_capture = radios["capture"]
        draw.text((20, 153), "BETTERCAP / " + radio_capture["iface"],
                  font=_font(12, bold=True), fill=INK)
        draw.text((20, 173), radio_capture["state"] + " / " + radio_capture["framing"],
                  font=_font(10), fill=SUBTLE)
        draw.text((20, 191), "KERNEL TYPE " +
                  (str(radio_capture["link"]) if radio_capture["link"] is not None else "--") +
                  " / RX UNVERIFIED", font=_font(10), fill=AMBER)
        draw.line((18, 210, 462, 210), fill=(36, 57, 69), width=1)
        onboard = radios["onboard"]
        draw.text((20, 217), "ONBOARD / " + onboard["iface"] + " / " +
                  onboard["state"] + " / " + onboard["driver"],
                  font=_font(10, bold=True),
                  fill=INK if onboard["state"] == "OBSERVED" else AMBER)
        draw.text((20, 236), "MODE " + onboard["mode"] + " / LINK " +
                  (str(onboard["link"]) if onboard["link"] is not None else "--") +
                  " / RX UNVERIFIED", font=_font(9), fill=AMBER)
        draw.line((18, 252, 462, 252), fill=(36, 57, 69), width=1)
        usb = radios["usb"]
        draw.text((20, 258), "USB / " + usb["iface"] + " / " +
                  usb["state"] + " / " + usb["driver"],
                  font=_font(10, bold=True), fill=INK if usb["state"] == "OBSERVED" else AMBER)
        draw.text((20, 278), "MODE " + usb["mode"] + " / LINK " +
                  (str(usb["link"]) if usb["link"] is not None else "--") +
                  " / RX UNVERIFIED", font=_font(9), fill=AMBER)
    else:
        draw.text((20, 160), "DOCTOR / ACTIONABLE FINDINGS",
                  font=_font(12, bold=True), fill=SUBTLE)
        if data["stale"]:
            draw.text((20, 194), "REPORT STALE / NO VERIFIED FINDINGS",
                      font=_font(12), fill=AMBER)
        elif doctor["rows"]:
            for index, finding in enumerate(doctor["rows"]):
                y = 190 + index * 30
                color = colors[finding["status"]]
                draw.ellipse((20, y + 5, 28, y + 13), fill=color)
                draw.text((35, y), finding["status"].upper() + " / " + finding["summary"],
                          font=_font(11), fill=INK)
            if doctor["total"] > 3:
                visible = doctor["start"] + len(doctor["rows"])
                draw.text((35, 282),
                          f"FINDINGS {doctor['start'] + 1}-{visible} / {doctor['total']}"
                          "  SWIPE UP/DOWN",
                          font=_font(9), fill=SUBTLE)
        else:
            message = ("No actionable findings" if data["health"] == "ok"
                       else "No verified findings")
            draw.text((20, 194), message, font=_font(12), fill=SUBTLE)

    if page == "captures":
        draw.line((18, 238, 462, 238), fill=(36, 57, 69), width=1)
        status = capture["ledger"]
        scan = capture["scan_state"]
        indicator = RED if status != "AVAILABLE" else SUBTLE
        draw.text((20, 247), "LEDGER / " + status,
                  font=_font(11, bold=True), fill=indicator)
        scan_label = "WORKER / " + scan
        if scan == "RECENT":
            scan_label += f" / {capture['scan_age_seconds']}s ago"
        draw.text((20, 266), scan_label, font=_font(11),
                  fill=AMBER if scan in ("STALE", "PAUSED STORAGE", "INVALID")
                  else SUBTLE)
        if scan == "RECENT":
            draw.text((20, 282),
                      "PASS " + compact_count(capture["scan_files"]) +
                      " FILES   READY " + compact_count(capture["last_ready"]) +
                      "   ERROR " + compact_count(capture["last_errors"]) +
                      "   DEFER " + compact_count(capture["last_deferred"]),
                      font=_font(9), fill=SUBTLE)
    elif page == "overview":
        draw.line((18, 238, 462, 238), fill=(36, 57, 69), width=1)
        draw.text((20, 250), "DOCTOR / " + data["finding"], font=_font(12, bold=True), fill=accent)
        draw.text((20, 272), data["detail"], font=_font(10), fill=SUBTLE)
        retry = f"{data['retry_seconds']}s" if data["retry_seconds"] is not None else "--"
        failures = value(data["failure_streak"])
        attempts = value(data["engine_attempts"])
        mount = ("OK" if data["capture_mount"] is True else
                 "MISSING" if data["capture_mount"] is False else "UNKNOWN")
        runtime_color = (AMBER if data["failure_streak"] not in (None, 0)
                         or data["capture_mount"] is False else SUBTLE)
        draw.text((20, 285),
                  f"RETRY {retry} / FAILURES {failures} / STARTS {attempts} / REDUXCAP {mount}",
                  font=_font(8), fill=runtime_color)
    # Footer tabs share geometry with gesture_nav.TAB_TOP/TAB_WIDTH.
    # They are visual only until the physical touch path is opted in.
    from .gesture_nav import PAGES, TAB_TOP, TAB_WIDTH
    draw.rectangle((0, TAB_TOP, 479, 319), fill=(15, 24, 35))
    for index, tab in enumerate(PAGES):
        left = index * TAB_WIDTH
        selected = tab == page
        if selected:
            draw.rectangle((left + 2, TAB_TOP, left + TAB_WIDTH - 3, 319),
                           fill=(30, 60, 67))
            draw.rectangle((left + 6, TAB_TOP, left + TAB_WIDTH - 7, TAB_TOP + 2),
                           fill=TEAL)
        label = tab.upper()
        draw.text((left + 12, TAB_TOP + 6), label,
                  font=_font(11, bold=selected), fill=INK if selected else SUBTLE)
    if data["stale"]:
        draw.text((185, 43), "NOT VERIFIED", font=_font(10, bold=True), fill=AMBER)
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
    fragment = b""
    page = args.page
    doctor_offset = 0
    from .telemetry_history import TelemetryWindow
    history = TelemetryWindow()
    if args.touch_device:
        from . import touch_nav
        if args.calibration_file:
            from .calibration_file import load_calibration
            from .gesture_nav import NavigationGestures
            try:
                calibration = load_calibration(args.calibration_file)
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
        touch_fd = touch_nav.open_touch(args.touch_device)

    def stop(_signum, _frame):
        nonlocal running
        running = False

    previous = {}
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name)
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, stop)
    try:
        while running:
            report = read_status(args.port)
            sampled_at = time.time()
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
                               doctor_offset=doctor_offset)
            elif page == "trends":
                image = render(report, page=page, now=sampled_at,
                               trends=history.snapshot(now=sampled_at))
            else:
                image = render(report, page=page, now=sampled_at)
            if args.rotate == 180:
                from PIL import Image
                image = image.transpose(Image.Transpose.ROTATE_180)
            write_frame(args.framebuffer,
                        rgb565_bytes(image, stride=fb["stride"], order=args.order))
            if args.once:
                break
            deadline = time.monotonic() + 2
            while running and time.monotonic() < deadline:
                remaining = min(0.2, max(0, deadline - time.monotonic()))
                if touch_fd is None:
                    time.sleep(remaining)
                    continue
                available, _, _ = select.select([touch_fd], [], [], remaining)
                if not available:
                    continue
                try:
                    chunk = os.read(touch_fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise OSError("touch device disconnected")
                fragment += chunk
                if len(fragment) > 8192:
                    raise OSError("unexpected unbounded touch input")
                if args.calibration_file:
                    from .gesture_nav import consume_gestures
                    actions, fragment = consume_gestures(fragment, tracker)
                    if actions:
                        action, value = actions[-1]
                        if action == "scroll":
                            if page != "doctor":
                                continue
                            count = len(status_view(report)["doctor_all"])
                            doctor_offset = min(max(0, count - 3),
                                                max(0, doctor_offset + value * 3))
                        elif action == "page":
                            if value != page:
                                doctor_offset = 0
                            page = value
                        elif action == "swipe":
                            page = touch_nav.navigate(page, value)
                            doctor_offset = 0
                        else:
                            continue
                        break  # redraw selected page or Doctor scroll immediately
                else:
                    directions, fragment = touch_nav.consume(fragment, tracker)
                    if directions:
                        page = touch_nav.navigate(page, directions[-1])
                        break  # redraw new page immediately
    finally:
        if touch_fd is not None:
            os.close(touch_fd)
        for sig, old in previous.items():
            signal.signal(sig, old)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
