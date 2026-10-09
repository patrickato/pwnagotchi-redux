"""Opt-in Redux touch kiosk: honest preflight before launching Chromium.

The Lite image does not ship a graphical session by default. This entry point
never creates one, configures hardware, changes radio state, or guesses fb0/fb1.
Start it as the locally logged-in, unprivileged user inside a prepared X11
session; an operator explicitly supplies the framebuffer verified on device.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

_FB = re.compile(r"/dev/fb[0-9]{1,2}\Z")
_ALLOWED = {(480, 320), (320, 480)}


def framebuffer_probe(device: str, *, sys_graphics: Path = Path("/sys/class/graphics"),
                      statter=os.stat) -> dict:
    """Read actual Linux framebuffer identity; never infer it from Pi type."""
    result = {"device": device, "ok": False, "resolution": None,
              "name": None, "reason": ""}
    if not _FB.fullmatch(device):
        result["reason"] = "explicit /dev/fbN device required"
        return result
    try:
        meta = statter(device)
        if not stat.S_ISCHR(meta.st_mode):
            result["reason"] = "selected framebuffer is not a character device"
            return result
        node = sys_graphics / Path(device).name
        label = (node / "name").read_text().strip()
        dimensions = (node / "virtual_size").read_text().strip()
        if not re.fullmatch(r"[0-9]{2,5},[0-9]{2,5}", dimensions):
            raise ValueError("unreadable virtual_size")
        width, height = (int(value) for value in dimensions.split(","))
        result.update(name=label[:80], resolution=[width, height])
        if (width, height) not in _ALLOWED:
            result["reason"] = (
                f"framebuffer is {width}x{height}; expected 480x320 or 320x480"
            )
            return result
        result["ok"] = True
        result["reason"] = "fb device and virtual resolution observed"
    except (OSError, ValueError) as exc:
        result["reason"] = f"framebuffer unavailable: {type(exc).__name__}"
    return result


def dashboard_probe(port: int, *, opener=urllib.request.urlopen) -> dict:
    """Only ever connect to Redux's literal loopback listener."""
    result = {"ok": False, "reason": ""}
    try:
        with opener(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            # Bound response memory: rejecting an unbounded/misrouted endpoint
            # is more useful than simply accepting any server on the port.
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("status payload exceeds 64 KiB")
            decoded = json.loads(data)
            if (not isinstance(decoded, dict)
                    or not isinstance(decoded.get("runtime"), dict)
                    or not isinstance(decoded.get("doctor"), dict)
                    or decoded["doctor"].get("overall") not in
                    ("ok", "attention", "degraded", "action", "unknown")):
                raise ValueError("not a Redux live dashboard response")
            result["ok"] = True
            result["reason"] = "Redux status endpoint responded on loopback"
    except (OSError, ValueError, UnicodeError, urllib.error.HTTPError,
            TimeoutError) as exc:
        result["reason"] = f"dashboard unavailable: {type(exc).__name__}"
    return result


def preflight(framebuffer: str, port: int = 8080, *,
              environ=None, uid=None, which=shutil.which,
              sys_graphics: Path = Path("/sys/class/graphics"),
              statter=os.stat, opener=urllib.request.urlopen) -> dict:
    env = os.environ if environ is None else environ
    effective_uid = os.geteuid() if uid is None else uid
    if not 1 <= port <= 65535:
        raise ValueError("dashboard port must be 1-65535")
    screen = framebuffer_probe(framebuffer, sys_graphics=sys_graphics,
                               statter=statter)
    browser = which("chromium") or which("chromium-browser")
    browser_ok = bool(browser) and os.path.isabs(browser)
    session_ok = bool(env.get("DISPLAY")) and effective_uid != 0
    dashboard = dashboard_probe(port, opener=opener)
    checks = {
        "framebuffer": screen,
        "browser": {"ok": browser_ok, "executable": browser if browser_ok else None,
                    "reason": "Chromium absolute executable found" if browser_ok else
                    "Chromium absent or not absolute; use the manual-x11 profile"},
        "session": {"ok": session_ok, "reason":
                    "unprivileged X11 DISPLAY configured" if session_ok else
                    "run as a non-root user inside a working X11 display session"},
        "dashboard": dashboard,
    }
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks,
            "touch_verified": False, "physical_display_verified": False,
            "reason": "Preflight does not prove that the panel displays pixels or touch works"}


def chromium_argv(browser: str, port: int, *, profile: Path) -> list[str]:
    if not browser or not os.path.isabs(browser):
        raise ValueError("Chromium executable must be an absolute path")
    if not profile.is_absolute():
        raise ValueError("browser profile must have an absolute path")
    return [
        browser, "--kiosk", "--no-first-run", "--no-default-browser-check",
        "--disable-session-crashed-bubble", "--disable-translate",
        "--disable-features=Translate", f"--user-data-dir={profile}",
        f"http://127.0.0.1:{port}/",
    ]


def supervise(command: list[str], *, spawn=subprocess.Popen,
              sleep=time.sleep, max_failures=5, restart_delay=3) -> int:
    """Run an opted-in local kiosk with bounded crash and shutdown behavior.

    Normal browser exit is a successful stop, not a crash. Unexpected exits
    are retried a finite number of times, after which systemd's own start
    limit can apply. Termination is polled to avoid hanging on an ignored
    signal, and the child is reaped before the supervisor exits.
    """
    if not 1 <= max_failures <= 10 or not 0 <= restart_delay <= 60:
        raise ValueError("invalid kiosk restart policy")
    if not command or not os.path.isabs(command[0]):
        raise ValueError("kiosk command must start with an absolute executable")
    failures = 0
    stopping = False
    child = None
    previous = {}

    def request_stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.getsignal(sig)
        signal.signal(sig, request_stop)
    try:
        while not stopping and failures < max_failures:
            try:
                child = spawn(command, start_new_session=False)
            except OSError as error:
                print(f"kiosk spawn failed: {type(error).__name__}", file=sys.stderr)
                return 1
            while not stopping:
                try:
                    code = child.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if stopping:
                break
            child = None  # successful wait reaped the exact child
            if code == 0:
                return 0
            failures += 1
            if failures < max_failures:
                sleep(restart_delay)
        return 0 if stopping else 1
    finally:
        try:
            if child is not None and child.poll() is None:
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    child.wait(timeout=2)
        finally:
            for sig, old in previous.items():
                signal.signal(sig, old)

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Redux optional 480x320 kiosk launcher")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="print real preflight report, without starting a browser")
    group.add_argument("--launch", action="store_true",
                       help="run Chromium only after a passing preflight")
    group.add_argument("--supervise", action="store_true",
                       help="opt-in bounded Chromium restart loop after preflight")
    parser.add_argument("--framebuffer", required=True,
                        help="verified /dev/fbN TFT device, never assumed")
    parser.add_argument("--port", type=int, default=8080)
    parsed = parser.parse_args(argv)
    try:
        result = preflight(parsed.framebuffer, port=parsed.port)
    except ValueError as exc:
        parser.error(str(exc))
    if parsed.check or not result["ok"]:
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 2
    # Browser is unprivileged. XDG_RUNTIME_DIR is ordinarily tmpfs managed by
    # logind and is intentionally preferred to the image's read-only root.
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or not Path(runtime).is_dir() or not os.access(runtime, os.W_OK):
        print("XDG_RUNTIME_DIR is missing or unwritable; refusing browser launch",
              file=sys.stderr)
        return 2
    runtime_dir = Path(runtime)
    try:
        root = runtime_dir.resolve(strict=True)
        meta = root.stat()
        if not root.is_dir() or root.is_symlink() or meta.st_uid != os.geteuid():
            raise ValueError("runtime directory ownership or type is unsafe")
        if meta.st_mode & 0o022:
            raise ValueError("runtime directory is group/world writable")
    except (OSError, ValueError) as error:
        print(f"Unsafe XDG_RUNTIME_DIR: {error}", file=sys.stderr)
        return 2
    profile = root / "redux-chromium"
    if profile.is_symlink():
        print("Refusing symlinked browser profile", file=sys.stderr)
        return 2
    if profile.exists():
        meta = profile.stat()
        if (not profile.is_dir() or meta.st_uid != os.geteuid()
                or meta.st_mode & 0o022):
            print("Refusing unsafe existing browser profile", file=sys.stderr)
            return 2
    args = chromium_argv(result["checks"]["browser"]["executable"],
                         parsed.port, profile=profile)
    if parsed.supervise:
        return supervise(args)
    os.execv(args[0], args)
    return 1  # unreachable unless an injected/mock exec returns


if __name__ == "__main__":
    raise SystemExit(main())
