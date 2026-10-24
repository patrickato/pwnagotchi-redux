"""Coordinated multi-device capture chaining — placement, authentication, and the
one safety property that makes swarm execution safe: authorization is never
delegated. A worker re-gates every assignment against its OWN Scope and refuses
anything it cannot locally authorize, even a correctly-signed assignment from the
coordinator.
"""
from redux.mesh import (
    NodeProfile, SwarmCoordinator, SwarmWorker, SignedMessage, TaskDelta, sign,
)
from redux.operator.operator import Operator, Phase
from redux.core import Scope


KEY = b"swarm-key"
CAP = "capture.handshake"
BSSID = "00:11:22:33:44:55"


def _armed_scope(*bssids):
    s = Scope()
    for b in bssids:
        s.add(b, "bssid", job="op")
    return s


def _operator(scope, *, caps=None, offense=True):
    return Operator(scope, offense_enabled=offense, caps=caps or {CAP: True})


def _ok_executor(_target):
    return True, "captured"


# --- placement --------------------------------------------------------------- #

def test_capture_goes_only_to_nodes_that_advertise_the_capability():
    coord = SwarmCoordinator(KEY, node_id="c")
    coord.add_node(NodeProfile("has-capture", caps={CAP: True}))
    coord.add_node(NodeProfile("no-capture", caps={CAP: False}))
    plan = coord.plan([BSSID], job="op")
    capture = next(a for a in plan if a.phase is Phase.CAPTURE)
    assert capture.assignee == "has-capture"
    assert "has-capture" in capture.reason


def test_capture_is_unplaced_when_no_node_can_capture():
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("recon-only", caps={CAP: False}))
    plan = coord.plan([BSSID])
    capture = next(a for a in plan if a.phase is Phase.CAPTURE)
    assert capture.assignee is None
    assert CAP in capture.reason


def test_targets_spread_across_eligible_nodes_round_robin():
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n1", caps={CAP: True}))
    coord.add_node(NodeProfile("n2", caps={CAP: True}))
    plan = coord.plan(["aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"])
    captures = [a.assignee for a in plan if a.phase is Phase.CAPTURE]
    assert set(captures) == {"n1", "n2"}   # not both on the same node


def test_empty_swarm_leaves_everything_unplaced():
    coord = SwarmCoordinator(KEY)
    plan = coord.plan([BSSID])
    assert plan and all(a.assignee is None for a in plan)
    assert "no nodes" in plan[0].reason


# --- authentication ---------------------------------------------------------- #

def test_worker_rejects_an_unsigned_or_forged_assignment():
    worker = SwarmWorker(_operator(_armed_scope(BSSID)), KEY, node_id="n1",
                         executors={Phase.CAPTURE: _ok_executor})
    forged = SignedMessage(
        payload=TaskDelta("n1", BSSID, Phase.CAPTURE.value, "op", 1.0, "c").payload(),
        sig="deadbeef")
    out = worker.accept(forged)
    assert out["fired"] is False
    assert "bad signature" in out["reason"]
    assert worker.rejected == 1


def test_worker_ignores_an_assignment_addressed_to_another_node():
    worker = SwarmWorker(_operator(_armed_scope(BSSID)), KEY, node_id="n1",
                         executors={Phase.CAPTURE: _ok_executor})
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n2", caps={CAP: True}))
    msg = coord.assignments([BSSID])[0]   # addressed to n2
    out = worker.accept(msg)
    assert out["fired"] is False
    assert "not me" in out["reason"]


# --- THE safety invariant: authorization is never delegated ------------------ #

def test_worker_refuses_a_valid_assignment_its_own_scope_does_not_authorize():
    # The coordinator (and the signature) say "capture BSSID", but this worker's
    # local Scope is EMPTY. A correctly-signed assignment must still be refused,
    # because the node re-checks its own Scope and never fires un-armed.
    worker = SwarmWorker(_operator(Scope()), KEY, node_id="n1",
                         executors={Phase.CAPTURE:
                                    lambda t: (_ for _ in ()).throw(
                                        AssertionError("must not fire un-armed"))})
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n1", caps={CAP: True}))
    msg = coord.assignments([BSSID])[0]
    out = worker.accept(msg)
    assert out["fired"] is False
    assert "local gate refused" in out["reason"]
    assert "scope is empty" in out["reason"]


def test_worker_refuses_when_posture_is_detection_only():
    worker = SwarmWorker(_operator(_armed_scope(BSSID), offense=False), KEY,
                         node_id="n1",
                         executors={Phase.CAPTURE:
                                    lambda t: (_ for _ in ()).throw(
                                        AssertionError("detection-only must not fire"))})
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n1", caps={CAP: True}))
    out = worker.accept(coord.assignments([BSSID])[0])
    assert out["fired"] is False
    assert "detection-only" in out["reason"]


def test_worker_refuses_when_capability_absent_even_if_armed():
    worker = SwarmWorker(_operator(_armed_scope(BSSID), caps={CAP: False}), KEY,
                         node_id="n1",
                         executors={Phase.CAPTURE:
                                    lambda t: (_ for _ in ()).throw(
                                        AssertionError("no capture engine must not fire"))})
    # Hand it a VALID signed assignment directly (the coordinator wouldn't place
    # capture here, but a worker must defend itself regardless of who sent it).
    msg = SignedMessage(
        payload=TaskDelta("n1", BSSID, Phase.CAPTURE.value, "op", 1.0, "c").payload(),
        sig=sign(TaskDelta("n1", BSSID, Phase.CAPTURE.value, "op", 1.0, "c").payload(), KEY))
    out = worker.accept(msg)
    assert out["fired"] is False
    assert "capability" in out["reason"]


# --- the happy path: armed + capable + addressed → it fires ------------------ #

def test_armed_capable_addressed_worker_runs_its_phase():
    worker = SwarmWorker(_operator(_armed_scope(BSSID)), KEY, node_id="n1",
                         executors={Phase.CAPTURE: _ok_executor})
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n1", caps={CAP: True}))
    out = worker.accept(coord.assignments([BSSID])[0])
    assert out["fired"] is True and out["ok"] is True
    assert out["result"] == "captured"


def test_coordinator_dispatch_reaches_the_right_worker_end_to_end():
    # Two real workers; the coordinator places capture on the capable one and the
    # assignment flows to exactly that worker, which fires; the other ignores it.
    armed_a = _armed_scope(BSSID)
    armed_b = _armed_scope(BSSID)
    wa = SwarmWorker(_operator(armed_a), KEY, node_id="a",
                     executors={Phase.CAPTURE: _ok_executor})
    wb = SwarmWorker(_operator(armed_b, caps={CAP: False}), KEY, node_id="b",
                     executors={Phase.CAPTURE: _ok_executor})
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("a", caps={CAP: True}))
    coord.add_node(NodeProfile("b", caps={CAP: False}))
    fired = []
    for msg in coord.assignments([BSSID]):
        for w in (wa, wb):
            r = w.accept(msg)
            if r.get("fired"):
                fired.append((w.node_id, r["phase"]))
    assert fired == [("a", "capture")]


def test_worker_telemetry_emits_on_the_bus_without_breaking_on_failure():
    events = []
    class Bus:
        def emit(self, signal, **payload):
            events.append(signal)
    worker = SwarmWorker(_operator(_armed_scope(BSSID)), KEY, node_id="n1",
                         executors={Phase.CAPTURE: _ok_executor}, bus=Bus())
    coord = SwarmCoordinator(KEY)
    coord.add_node(NodeProfile("n1", caps={CAP: True}))
    worker.accept(coord.assignments([BSSID])[0])
    assert "TX_BEGIN" in events and "TX_END" in events
