# Push-to-phone alerts (`redux push`)

Get a capture, a tracker warning, or a sentinel alert to your phone while you're
away from the device. Three services, your choice:

- **ntfy** (ntfy.sh or self-hosted) — no account; just POST to a topic URL. Title,
  priority and tags ride in headers.
- **Pushover** — your app token + user key.
- **webhook** — POST a JSON body to any URL you control (Slack/Discord/n8n/…).

```
redux push --service ntfy --target https://ntfy.sh/my-redux-topic test
redux push --service ntfy --target https://ntfy.sh/my-redux-topic send "redux" "handshake captured" --priority 4
redux push --service pushover --target <userkey> --token <apptoken> test
redux push --service webhook  --target https://hooks.example/… send "redux" "tracker following you" --priority 5
```

## Privacy & honesty

- Secrets (Pushover token/user, webhook URL) are passed **at call time and never
  stored** by this module.
- The message is a **short summary** (clipped), not a dump of captured material —
  `notify_alert()` summarizes an actual alert (kind + one-line reason), it
  invents nothing.
- Failures are glass-box: a bad config is refused with a reason; a network/HTTP
  error is reported, never swallowed or faked as success.

## Wiring

`redux/connectivity/push.py` — `PushConfig`, `build_request` (per-service HTTP
request construction), `PushSender` (sends via an **injected** opener, so it's
fully tested with no network), `summarize_alert`/`notify_alert` (alert → push).
`redux push test|send` CLI.

A future hook can call `notify_alert(alert, cfg)` from the sentinel/offense path
so high-severity events reach your phone automatically; the building blocks are
all here and tested.
