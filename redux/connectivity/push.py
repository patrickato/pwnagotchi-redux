"""Push-to-phone alerts — get a capture / tracker / sentinel alert to your phone.

Builds a notification for one of three services and sends it over an **injected**
HTTP opener, so the payload construction is fully tested with no network and no
account:

* **ntfy** (ntfy.sh or self-hosted) — POST the message to a topic URL; title,
  priority and tags ride in headers. The simplest, no-account option.
* **Pushover** — form POST with your app token + user key.
* **webhook** — POST a JSON body to any URL you control (Slack/Discord/n8n/…).

Glass-box + privacy-aware: the config is explicit, secrets are passed at call
time (never stored here), and the default message is a short event summary —
not a dump of captured material. Real data only: `notify_alert` summarizes an
actual alert; it invents nothing.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

SERVICES = ("ntfy", "pushover", "webhook")

# map a coarse 0..5 priority to each service's own scale
_NTFY_PRIO = {0: "min", 1: "low", 2: "default", 3: "default", 4: "high", 5: "urgent"}


@dataclass(frozen=True)
class PushConfig:
    service: str                      # one of SERVICES
    target: str                       # ntfy topic URL, pushover user key, or webhook URL
    token: str = ""                   # pushover app token (unused by ntfy/webhook)
    default_priority: int = 3

    def validate(self) -> Tuple[bool, str]:
        if self.service not in SERVICES:
            return False, f"unknown service '{self.service}' (choose {', '.join(SERVICES)})"
        if not self.target:
            return False, "no target configured (topic URL / user key / webhook URL)"
        if self.service == "pushover" and not self.token:
            return False, "pushover needs an app --token"
        return True, "ok"


@dataclass(frozen=True)
class PushRequest:
    """A ready-to-send HTTP request (what the builders produce)."""
    method: str
    url: str
    headers: Dict[str, str]
    data: bytes

    def to_urllib(self) -> urllib.request.Request:
        return urllib.request.Request(self.url, data=self.data or None,
                                      headers=dict(self.headers), method=self.method)


def _clip(s: str, n: int = 240) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def build_request(cfg: PushConfig, title: str, message: str, *,
                  priority: Optional[int] = None, tags: str = "") -> PushRequest:
    """Build the service-specific HTTP request. Message is clipped to a short
    summary (we notify, we don't exfiltrate)."""
    prio = cfg.default_priority if priority is None else priority
    title, message = _clip(title, 120), _clip(message, 240)
    if cfg.service == "ntfy":
        headers = {"Title": title, "Priority": _NTFY_PRIO.get(prio, "default")}
        if tags:
            headers["Tags"] = tags
        return PushRequest("POST", cfg.target, headers, message.encode("utf-8"))
    if cfg.service == "pushover":
        from urllib.parse import urlencode
        body = urlencode({"token": cfg.token, "user": cfg.target, "title": title,
                          "message": message, "priority": max(-2, min(2, prio - 3))})
        return PushRequest("POST", "https://api.pushover.net/1/messages.json",
                           {"Content-Type": "application/x-www-form-urlencoded"},
                           body.encode("utf-8"))
    # webhook
    body = json.dumps({"title": title, "message": message, "priority": prio,
                       "tags": tags}).encode("utf-8")
    return PushRequest("POST", cfg.target, {"Content-Type": "application/json"}, body)


@dataclass
class PushSender:
    """Sends a PushRequest via an injected opener (defaults to urllib). The opener
    is `opener(urllib.request.Request, timeout) -> response-with-.status/.getcode`."""
    config: PushConfig
    opener: Callable = field(default=urllib.request.urlopen)
    timeout: float = 10.0

    def send(self, title: str, message: str, *, priority: Optional[int] = None,
             tags: str = "") -> dict:
        ok, reason = self.config.validate()
        if not ok:
            return {"ok": False, "reason": f"refused: {reason}"}
        req = build_request(self.config, title, message, priority=priority, tags=tags)
        try:
            resp = self.opener(req.to_urllib(), timeout=self.timeout)
        except Exception as e:                      # network/URL/HTTP errors
            return {"ok": False, "reason": f"send failed: {type(e).__name__}: {e}"}
        code = getattr(resp, "status", None) or (resp.getcode() if hasattr(resp, "getcode") else None)
        ok = code is None or 200 <= int(code) < 300
        return {"ok": ok, "service": self.config.service, "status": code,
                "reason": "sent" if ok else f"service returned {code}"}


def summarize_alert(alert) -> Tuple[str, str, int]:
    """(title, message, priority) for a detect Alert — a short summary, not a
    content dump. Tolerant of duck-typed alerts (kind/severity/summary/reason)."""
    kind = str(getattr(alert, "kind", "") or getattr(alert, "type", "") or "alert")
    sev = getattr(alert, "severity", None)
    summary = (getattr(alert, "summary", "") or getattr(alert, "reason", "")
               or getattr(alert, "message", "") or "")
    title = f"redux: {kind}"
    try:
        prio = int(sev) if sev is not None else 4
    except (TypeError, ValueError):
        prio = 4
    return title, _clip(str(summary) or kind, 240), max(0, min(5, prio))


def notify_alert(alert, config: PushConfig, *, sender: Optional[PushSender] = None,
                 opener: Optional[Callable] = None) -> dict:
    """Summarize an alert and push it. Real data only — summarizes the actual
    alert, invents nothing."""
    s = sender or PushSender(config, opener=opener or urllib.request.urlopen)
    title, message, prio = summarize_alert(alert)
    return s.send(title, message, priority=prio, tags="warning")
