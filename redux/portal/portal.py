"""Captive-portal server for authorized testing.

The default page is a plain, brand-neutral "network access" form — it does not
impersonate any real organization. Submissions are recorded (for the operator's
authorized review) with a timestamp; the server refuses to run unless the
engagement is explicitly authorized, and binds to the least-exposed scope by
default.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs

from redux.web.status_page import resolve_host  # reuse the bind-scope hosts

_log = logging.getLogger("redux.portal")

# Generic, brand-neutral captive page. Imitates no real org by design.
DEFAULT_TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Network Access</title><style>
body{font-family:system-ui,sans-serif;background:#0b0e13;color:#e6edf3;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}
form{background:#141a22;border:1px solid #222b36;border-radius:12px;padding:28px;width:300px}
h1{font-size:18px;margin:0 0 4px}p{color:#8b98a5;font-size:13px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;margin:6px 0;padding:10px;border-radius:8px;
border:1px solid #2a3542;background:#0e141b;color:#e6edf3}
button{width:100%;margin-top:12px;padding:10px;border:0;border-radius:8px;background:#4ec9b0;color:#06221c;font-weight:600}
</style></head><body>
<form method="POST" action="/">
  <h1>Network Access</h1>
  <p>Sign in to continue to the internet.</p>
  <input name="username" placeholder="Username" autocomplete="off">
  <input name="password" type="password" placeholder="Password" autocomplete="off">
  <button type="submit">Connect</button>
</form></body></html>"""

_THANKS = b"<!doctype html><title>Connecting</title><p>Connecting\xe2\x80\xa6</p>"


@dataclass(frozen=True)
class Submission:
    ts: float
    fields: Dict[str, str]
    client: str = ""


@dataclass
class CaptivePortal:
    """Holds the page + the submissions captured during an authorized engagement."""
    engagement: str
    template: str = DEFAULT_TEMPLATE
    submissions: List[Submission] = field(default_factory=list)

    def page(self) -> bytes:
        return self.template.encode()

    def record(self, fields: Dict[str, str], client: str = "", now: Optional[float] = None) -> Submission:
        sub = Submission(ts=time.time() if now is None else now, fields=dict(fields), client=client)
        self.submissions.append(sub)
        _log.info("portal[%s]: submission with fields %s from %s",
                  self.engagement, sorted(fields.keys()), client or "?")
        return sub


def make_handler(portal: CaptivePortal):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, portal.page())

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            fields = {k: v[0] for k, v in parse_qs(raw).items()}
            client = self.client_address[0] if self.client_address else ""
            portal.record(fields, client=client)
            self._send(200, _THANKS)

        def log_message(self, *a):  # quiet; we log through record()
            pass
    return Handler


def serve(portal: CaptivePortal, *, authorized: bool = False, port: int = 8088,
          bind_scope: str = "localhost", _serve: bool = True) -> ThreadingHTTPServer:
    """Start the portal — but ONLY for an explicitly authorized engagement.

    Refuses (ValueError) unless authorized=True, so it can't be casually stood
    up. Binds least-exposed by default and logs the exact URL."""
    if not authorized:
        raise ValueError(
            "captive portal refused: pass authorized=True to confirm this is an engagement "
            "you are permitted to run (authorized client testing only)")
    host = resolve_host(bind_scope)
    httpd = ThreadingHTTPServer((host, port), make_handler(portal))
    url = f"http://{host}:{httpd.server_address[1]}/"
    _log.info("redux captive portal for engagement '%s' on %s (bind_scope=%s) — authorized test",
              portal.engagement, url, bind_scope)
    print(f"redux captive portal: {url}  (engagement={portal.engagement}, bind_scope={bind_scope})")
    if _serve:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
