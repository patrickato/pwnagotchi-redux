# Probe harvesting & aimed karma (`redux karma`)

When a device can't find a network it knows, it shouts for it — a **probe
request** naming an SSID from its preferred-network list (PNL). Harvesting those
probes tells you exactly what each nearby device will auto-join. The classic
"karma" response is to advertise one of those SSIDs so the client associates to
you (feeding the evil-portal / capture).

## The line this draws (and why)

- **Harvesting is passive** — it only listens to `client.probe` events and
  transmits nothing, so it runs in **any** mode, FIELD included. On its own it's
  pure recon intelligence: *"these three phones are looking for `CorpWiFi`,
  `Starbucks`, and `home-net`."*
- **Advertising is firing.** AGENTS.md forbids *indiscriminate* beacon spam —
  it can't be aimed and hits bystanders. So karma here is deliberately
  **aimed**:
  - the plan advertises only SSIDs that an **in-scope** client probed for
    (`scope.permits(bssid=client)`; LAB approves on sight);
  - an SSID wanted *only* by out-of-scope clients is **refused** with a reason —
    bystanders are never targeted;
  - the advertise list is **capped** (`MAX_ADVERTISE`), so an "aim" can't
    degrade into blasting the whole room.

That keeps it a targeted response to devices you're authorized to assess, not a
broadcast — which is the difference between karma and the beacon-spam this
project deliberately won't do.

## CLI

```
redux karma clients          # per-client PNL: who is looking for what (passive recon)
redux karma ssids            # probed SSIDs ranked by how many clients want them
redux karma plan             # the AIMED advertise plan, scope-gated and glass-box
```

```
$ redux karma plan --scope-file lab.json
{
  "armed": false,
  "advertise": [
    {"ssid": "CorpWiFi", "clients": ["aa:bb:cc:00:00:01","aa:bb:cc:00:00:02"],
     "reason": "advertise 'CorpWiFi' — requested by 2 in-scope client(s)"},
    {"ssid": "home-net", "clients": ["aa:bb:cc:00:00:01"],
     "reason": "advertise 'home-net' — requested by 1 in-scope client(s)"}
  ],
  "skipped": [
    {"ssid": "NeighborNet",
     "reason": "refused: only out-of-scope clients probed for 'NeighborNet' (1) — not aiming at bystanders"}
  ],
  "reason": "2 aimed SSID(s) planned; FIELD mode is advisory — switch to LAB (or arm explicitly) to advertise"
}
```

`armed` is `true` only in LAB (or with the relevant clients in scope and
explicitly armed). `plan.ssid_list` is the targeted set you'd hand the
responder/AP; it never includes a bystander-only SSID.

## How it's wired

- `redux/engine/bettercap_driver.py` maps `wifi.client.probe` →
  `client.probe` (carrying the probed SSID).
- `redux/portal/karma.py` — `ProbeHarvester` (passive, JSON-persisted PNL) and
  `karma_plan()` (the aimed, scope-gated planner).
- `live_runtime` subscribes the harvester to the event bus passively and
  persists it to `/captures/live/pnl.json`, so `redux karma` reads what the
  daemon saw.

Real data only: SSIDs come from observed probes, never invented; every include
and every refusal carries its reason.
