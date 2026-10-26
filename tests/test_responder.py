"""Responder hash harvesting — NetNTLM parse + gated, bounded run."""
import subprocess

from redux.netrecon.responder import (
    ResponderHarvest, parse_responder_hashes, to_crack_lines,
    HASHCAT_NETNTLMV1, HASHCAT_NETNTLMV2,
)

# a real-shaped NetNTLMv2 line (hashcat -m 5600)
V2 = ("alice::CORP:1122334455667788:"
      "0123456789abcdef0123456789abcdef:"
      "0101000000000000" + "ab" * 40)
# a real-shaped NetNTLMv1 line (hashcat -m 5500)
V1 = ("bob::WORKGROUP:" + "aa" * 24 + ":" + "bb" * 24 + ":1122334455667788")


def test_parse_v2():
    hs = parse_responder_hashes(V2, client_ip="192.168.1.9")
    assert len(hs) == 1
    h = hs[0]
    assert h.username == "alice" and h.domain == "CORP"
    assert h.kind == "netntlmv2" and h.hashcat_mode == HASHCAT_NETNTLMV2
    assert h.client_ip == "192.168.1.9" and h.hash_line == V2


def test_parse_v1():
    hs = parse_responder_hashes(V1)
    assert len(hs) == 1 and hs[0].kind == "netntlmv1"
    assert hs[0].hashcat_mode == HASHCAT_NETNTLMV1


def test_parse_strips_responder_session_prefix():
    line = f"[SMB] NTLMv2-SSP Hash : {V2}"
    hs = parse_responder_hashes(line)
    assert len(hs) == 1 and hs[0].username == "alice"


def test_parse_dedupes_same_user():
    hs = parse_responder_hashes(V2 + "\n" + V2 + "\n")
    assert len(hs) == 1


def test_parse_skips_junk():
    assert parse_responder_hashes("not a hash\nrandom::line:too:short") == []


def test_to_crack_lines_groups_by_mode():
    hs = parse_responder_hashes(V1 + "\n" + V2)
    grouped = to_crack_lines(hs)
    assert set(grouped) == {HASHCAT_NETNTLMV1, HASHCAT_NETNTLMV2}
    assert grouped[HASHCAT_NETNTLMV2] == [V2]


def test_plan_is_glassbox():
    assert ResponderHarvest("wlan0").plan() == ["responder", "-I", "wlan0"]
    assert ResponderHarvest("wlan0", analyze_only=True).plan() == ["responder", "-I", "wlan0", "-A"]


def test_field_refuses_poisoning_without_authorization():
    hs, reason = ResponderHarvest("wlan0", runner=lambda *a, **k: None).harvest()
    assert hs == [] and "refused" in reason


def test_lab_runs_and_parses_harvested_hashes():
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kw.get("timeout", 1))   # daemon, time-boxed
    h = ResponderHarvest("wlan0", runner=runner, seconds=5)
    hashes, reason = h.harvest(lab=True, log_reader=lambda: V1 + "\n" + V2)
    assert calls and calls[0] == ["responder", "-I", "wlan0"]
    assert len(hashes) == 2 and "harvested 2" in reason


def test_analyze_only_is_always_allowed():
    h = ResponderHarvest("wlan0", runner=lambda *a, **k: None, analyze_only=True)
    hashes, reason = h.harvest(log_reader=lambda: V2)          # no authorization needed
    assert len(hashes) == 1 and "passive" in reason


def test_scope_gate_blocks_when_lan_not_armed():
    class _Scope:
        def authorize(self, *, ip=None):
            return (False, "scope empty")
    h = ResponderHarvest("wlan0", runner=lambda *a, **k: None)
    hashes, reason = h.harvest(scope=_Scope())
    assert hashes == [] and "refused" in reason
