"""Whole-device simulator — boots the REAL live stack with a fake engine."""
import json

from redux.replay.device_sim import (
    ScheduledReplayTransport, snapshot_once, DEFAULT_SCENARIO, sim_radios,
)


def test_scheduled_transport_trickles_events_by_clock():
    t = [0.0]
    events = [
        {"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "a"}},
        {"tag": "wifi.ap.new", "time": 5.0, "data": {"mac": "b"}},
    ]
    tr = ScheduledReplayTransport(events, clock=lambda: t[0], loop=False)
    assert tr.events(clear=True) == []                       # nothing due at t=0
    t[0] = 2.0
    assert [e["data"]["mac"] for e in tr.events(clear=True)] == ["a"]   # only the 1.0 event
    t[0] = 6.0
    assert [e["data"]["mac"] for e in tr.events(clear=True)] == ["b"]   # now the 5.0 event
    assert tr.events(clear=True) == []                       # drained (no loop)
    # it records driver commands like the real transport
    tr.run("wifi.recon on")
    assert "wifi.recon on" in tr.commands


def test_scheduled_transport_loops_for_a_long_run():
    t = [0.0]
    tr = ScheduledReplayTransport([{"tag": "wifi.ap.new", "time": 1.0, "data": {"mac": "a"}}],
                                  clock=lambda: t[0], loop=True)
    t[0] = 2.0
    assert len(tr.events(clear=True)) == 1
    # after draining, the loop re-arms from the new 'now' — the event returns later
    t[0] = 4.0
    assert len(tr.events(clear=True)) == 1


def test_sim_radios_offer_an_injection_capture_iface():
    radios = sim_radios()
    assert any(r.iface == "wlan1sim" and r.inject for r in radios)
    assert any(r.onboard for r in radios)                    # an onboard radio too


def test_snapshot_once_boots_real_stack_and_replays_scenario():
    snap = snapshot_once(ticks=16, step=2.0)
    rt = snap["runtime"]
    assert rt["state"] == "running"                          # the real supervisor reached running
    assert rt["events_seen"] >= 10                           # the scenario was pumped through
    assert rt["handshake_events"] >= 1                       # incl. the dad24 handshake
    assert "doctor" in snap and "ui" in snap                 # Doctor + TFT model are populated
    assert snap.get("capture_engine") == "bettercap"
    # the capability option-space + readiness is surfaced for every UI to read
    assert "capabilities" in snap and snap["capabilities"]["total"] >= 1
    assert "summary" in snap["capabilities"]
    # the user's own test AP (dad24) shows up in the live state
    blob = json.dumps(snap.get("access_points", []))
    assert "dad24" in blob or "18:a5:e9:65:34:d2" in blob


def test_snapshot_once_honors_a_custom_scenario():
    scenario = [
        {"tag": "wifi.ap.new", "time": 0.5, "data": {"mac": "aa:bb:cc:dd:ee:ff", "essid": "OnlyOne", "channel": 1}},
    ]
    snap = snapshot_once(scenario=scenario, ticks=8, step=2.0)
    assert snap["runtime"]["state"] == "running"
    assert snap["runtime"]["events_seen"] >= 1
    assert "OnlyOne" in json.dumps(snap.get("access_points", []))
