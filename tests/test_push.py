"""Push-to-phone: service-specific request building + send via an injected opener."""
import json
from types import SimpleNamespace

from redux.connectivity.push import (
    PushConfig, PushSender, build_request, summarize_alert, notify_alert, SERVICES,
)


# ---- config validation --------------------------------------------------- #

def test_config_validation():
    assert PushConfig("ntfy", "https://ntfy.sh/mytopic").validate()[0]
    assert not PushConfig("nope", "x").validate()[0]
    assert not PushConfig("ntfy", "").validate()[0]
    assert not PushConfig("pushover", "userkey").validate()[0]   # missing token
    assert PushConfig("pushover", "userkey", token="apptoken").validate()[0]


# ---- request building ---------------------------------------------------- #

def test_build_ntfy_request():
    cfg = PushConfig("ntfy", "https://ntfy.sh/redux")
    r = build_request(cfg, "Title", "body text", priority=5, tags="warning")
    assert r.method == "POST" and r.url == "https://ntfy.sh/redux"
    assert r.headers["Title"] == "Title" and r.headers["Priority"] == "urgent"
    assert r.headers["Tags"] == "warning"
    assert r.data == b"body text"


def test_build_pushover_request():
    cfg = PushConfig("pushover", "USERKEY", token="APPTOKEN")
    r = build_request(cfg, "T", "M", priority=4)
    assert r.url.endswith("/messages.json")
    body = r.data.decode()
    assert "token=APPTOKEN" in body and "user=USERKEY" in body
    assert "priority=1" in body                      # 4 - 3 = +1 on pushover scale


def test_build_webhook_request():
    cfg = PushConfig("webhook", "https://example.com/hook")
    r = build_request(cfg, "T", "M", priority=2)
    d = json.loads(r.data)
    assert d["title"] == "T" and d["message"] == "M" and d["priority"] == 2
    assert r.headers["Content-Type"] == "application/json"


def test_message_is_clipped_not_dumped():
    cfg = PushConfig("ntfy", "https://ntfy.sh/t")
    r = build_request(cfg, "x", "A" * 1000)
    text = r.data.decode("utf-8")
    assert len(text) <= 240 and text.endswith("…")


# ---- sending (injected opener) ------------------------------------------- #

class _Resp:
    def __init__(self, status): self.status = status


def test_send_success():
    sent = {}
    def opener(req, timeout=None):
        sent["url"] = req.full_url
        return _Resp(200)
    s = PushSender(PushConfig("ntfy", "https://ntfy.sh/redux"), opener=opener)
    res = s.send("hi", "there")
    assert res["ok"] and res["status"] == 200
    assert sent["url"] == "https://ntfy.sh/redux"


def test_send_refuses_bad_config():
    s = PushSender(PushConfig("ntfy", ""), opener=lambda *a, **k: _Resp(200))
    res = s.send("t", "m")
    assert not res["ok"] and "refused" in res["reason"]


def test_send_reports_network_failure():
    def boom(req, timeout=None):
        raise OSError("no route to host")
    s = PushSender(PushConfig("ntfy", "https://ntfy.sh/t"), opener=boom)
    res = s.send("t", "m")
    assert not res["ok"] and "send failed" in res["reason"]


def test_send_reports_http_error_status():
    s = PushSender(PushConfig("webhook", "https://x/y"), opener=lambda *a, **k: _Resp(500))
    res = s.send("t", "m")
    assert not res["ok"] and "500" in res["reason"]


# ---- alert summarization ------------------------------------------------- #

def test_summarize_alert_is_a_summary():
    alert = SimpleNamespace(kind="rogue_ap", severity=5, summary="evil twin of CorpWiFi")
    title, msg, prio = summarize_alert(alert)
    assert "rogue_ap" in title and msg == "evil twin of CorpWiFi" and prio == 5


def test_summarize_alert_defaults_are_safe():
    title, msg, prio = summarize_alert(SimpleNamespace())
    assert title and msg and 0 <= prio <= 5


def test_notify_alert_sends_summary():
    captured = {}
    def opener(req, timeout=None):
        captured["data"] = req.data
        return _Resp(200)
    alert = SimpleNamespace(kind="tracker", severity=4, summary="a tag is following you")
    res = notify_alert(alert, PushConfig("ntfy", "https://ntfy.sh/t"), opener=opener)
    assert res["ok"]
    assert b"following you" in captured["data"]
