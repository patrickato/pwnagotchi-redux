"""Web status dashboard — the glass-box view in a browser (atlas P-11).

Serves two things from a running Beastcore: `GET /api/status` (the JSON snapshot)
and `GET /` (a tiny self-contained page that polls it and shows what the device is
doing and *why*). No external assets, no fake data — it renders exactly what the
system reports.

Exposure follows the repo rule: default `bind_scope=localhost`; a wider scope is a
deliberate choice, and `serve()` always logs the exact URL it bound.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict

_log = logging.getLogger("redux.web")

# bind_scope -> host to bind
_SCOPE_HOST = {
    "localhost": "127.0.0.1",
    "lan": "0.0.0.0",
    "tailscale": "0.0.0.0",   # intended to be reached only over the tailnet
    "auto": "127.0.0.1",
}


def status_payload(beastcore) -> Dict:
    """The snapshot the dashboard renders: Beastcore.status() + recent narration."""
    data = dict(beastcore.status())
    data["narration"] = [l.text for l in beastcore.narrator.lines(12)]
    return data


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>redux</title><style>
:root{--bg:#0b0e13;--fg:#e6edf3;--mut:#8b98a5;--acc:#4ec9b0;--warn:#e3b341;--crit:#f85149;--card:#141a22}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 ui-monospace,Menlo,Consolas,monospace}
header{padding:16px 18px;border-bottom:1px solid #222b36;display:flex;gap:12px;align-items:baseline}
h1{font-size:18px;margin:0;letter-spacing:.5px}.mut{color:var(--mut)}
main{max-width:820px;margin:0 auto;padding:18px;display:grid;gap:14px}
.card{background:var(--card);border:1px solid #222b36;border-radius:10px;padding:14px 16px}
.row{display:flex;flex-wrap:wrap;gap:10px}.kv{flex:1 1 150px}.k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.5px}
.v{font-size:18px;margin-top:2px}.creature{font-size:20px;color:var(--acc)}
.reason{color:var(--mut);font-size:13px;margin-top:4px}
ul{margin:6px 0 0;padding-left:18px}li{color:var(--mut);font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#1d2630;font-size:12px}
</style></head><body>
<header><h1>redux</h1><span class="mut" id="sub">glass-box status</span></header>
<main>
 <div class="card"><div class="creature" id="creature">…</div><div class="reason" id="mood"></div></div>
 <div class="card"><div class="row">
   <div class="kv"><div class="k">intent</div><div class="v" id="intent">—</div></div>
   <div class="kv"><div class="k">capture radio</div><div class="v" id="cap">—</div></div>
   <div class="kv"><div class="k">sightings</div><div class="v" id="sight">—</div></div>
   <div class="kv"><div class="k">alerts</div><div class="v" id="alerts">—</div></div>
 </div></div>
 <div class="card"><div class="k">brain recommends</div>
   <div class="v"><span class="badge" id="rec">—</span></div><div class="reason" id="recwhy"></div></div>
 <div class="card"><div class="k">recent narration</div><ul id="narr"></ul></div>
</main>
<script>
async function tick(){try{const r=await fetch('/api/status');const d=await r.json();
 document.getElementById('creature').textContent=d.creature||'…';
 document.getElementById('mood').textContent='mood: '+(d.mood||'');
 document.getElementById('intent').textContent=d.intent||'—';
 document.getElementById('cap').textContent=d.capture_iface||'none';
 document.getElementById('sight').textContent=d.sightings??'—';
 document.getElementById('alerts').textContent=d.recent_alerts??'—';
 const rc=d.recommendation||{};document.getElementById('rec').textContent=rc.intent||'(steady)';
 document.getElementById('recwhy').textContent=rc.reason||'';
 const ul=document.getElementById('narr');ul.innerHTML='';
 (d.narration||[]).slice().reverse().forEach(t=>{const li=document.createElement('li');li.textContent=t;ul.appendChild(li)});
}catch(e){document.getElementById('sub').textContent='disconnected'}}
tick();setInterval(tick,2000);
</script></body></html>"""


def render_page() -> str:
    return PAGE


def make_handler(status_provider: Callable[[], Dict]):
    """Build a request handler class that serves `/` and `/api/status`."""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/") == "/api/status" or self.path == "/api/status":
                self._send(200, json.dumps(status_provider()).encode(), "application/json")
            elif self.path == "/" or self.path == "":
                self._send(200, render_page().encode(), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *a):  # quiet by default
            pass
    return Handler


def resolve_host(bind_scope: str) -> str:
    return _SCOPE_HOST.get(bind_scope, "127.0.0.1")


def serve(beastcore, port: int = 8080, bind_scope: str = "localhost",
          interval: float = 2.0, pump: bool = True, _cycles=None):
    """Run the dashboard (foreground loop). The HTTP server runs in a daemon thread
    and serves a cached snapshot; this thread owns the SightingStore, so it is the
    only one that pumps and recomputes the snapshot (SQLite is single-thread). Logs
    the exact URL. `_cycles` bounds the loop for tests; otherwise runs until Ctrl-C."""
    host = resolve_host(bind_scope)
    holder = {"d": status_payload(beastcore)}
    httpd = ThreadingHTTPServer((host, port), make_handler(lambda: holder["d"]))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{host}:{port}/"
    _log.info("redux web dashboard on %s (bind_scope=%s)", url, bind_scope)
    print(f"redux web dashboard: {url}  (bind_scope={bind_scope})")
    n = 0
    try:
        while _cycles is None or n < _cycles:
            if pump:
                beastcore.pump()
            holder["d"] = status_payload(beastcore)   # store touched only in this thread
            n += 1
            if _cycles is None or n < _cycles:
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
    return httpd
