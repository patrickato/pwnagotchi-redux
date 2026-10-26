"""Real kill-chain executor wiring — the full chain threads context end to end
(capture → crack → JOIN → scan → cred → loot) with injected fakes, no radio."""
from redux.core import Scope
from redux.operator import Operator, Phase, build_chain_executors
from redux.netrecon import ReplayScanner, ReplayCredTester, Host, Port, JoinResult


ARMED = "18:a5:e9:65:34:d2"       # dad24's BSSID
SUBNET = "192.168.1.0/24"


def _scope():
    s = Scope()
    s.add(ARMED, "bssid", job="lab")
    s.add(SUBNET, "cidr", job="lab")      # arm the LAN the JOIN lands on
    return s


class _FakeJoiner:
    def join(self, ssid, psk):
        return JoinResult(True, "wlan0", ssid, ip="192.168.1.37", subnet=SUBNET,
                          gateway="192.168.1.1", reason=f"joined {ssid}")


def _backends(**over):
    capture = over.get("capture", lambda t: (True, "handshake captured", f"/captures/{t}.pcapng"))
    crack = over.get("crack", lambda hs, t: (True, "key recovered", "hunter2hunter2", "dad24"))
    scanner = ReplayScanner({SUBNET: [Host("192.168.1.50", ports=(Port(22, service="ssh"),))]})
    tester = ReplayCredTester({("192.168.1.50", 22): ("root", "toor")})
    return dict(capture=capture, crack=crack, joiner=_FakeJoiner(),
                scanner=scanner, tester=tester,
                usernames=("root",), passwords=("toor",))


def _run(scope=None, **over):
    scope = scope or _scope()
    execs, contexts = build_chain_executors(scope=scope, **_backends(**over))
    op = Operator(scope, offense_enabled=True,
                  caps={"capture.handshake": True, "radio.wifi.uplink": True})
    out = op.run([ARMED], execs, recon=lambda: (True, "seen"))
    return out, contexts[ARMED]


def test_full_chain_threads_context_capture_to_loot():
    out, ctx = _run()
    executed = [s["phase"] for s in out["steps"] if s["executed"]]
    assert executed == ["recon", "capture", "crack", "join", "pivot_scan", "pivot_cred", "loot"]
    # the carried context flowed down the chain
    assert ctx["handshake"].endswith(".pcapng")
    assert ctx["psk"] == "hunter2hunter2" and ctx["ssid"] == "dad24"
    assert ctx["subnet"] == SUBNET
    assert [h.ip for h in ctx["hosts"]] == ["192.168.1.50"]
    assert len(ctx["creds"]) == 1 and ctx["creds"][0].username == "root"
    assert ctx["loot"]["hosts"] == 1 and ctx["loot"]["credentials"] == 1


def test_join_blocks_pivot_when_no_key_recovered():
    # crack fails to recover a key → JOIN has nothing, chain stops before the LAN
    out, ctx = _run(crack=lambda hs, t: (False, "wordlist exhausted", None, None))
    phases = [s["phase"] for s in out["steps"] if s["executed"]]
    assert "crack" in phases and "join" not in phases     # no key → no join → no pivot
    assert "subnet" not in ctx


def test_pivot_refused_when_subnet_not_in_scope_field_behavior():
    # a scope WITHOUT the joined subnet armed (FIELD): scan is refused, honestly
    s = Scope()
    s.add(ARMED, "bssid", job="lab")       # AP armed, but NOT the 192.168.1.0/24 LAN
    out, ctx = _run(scope=s)
    scan = next(x for x in out["steps"] if x["phase"] == "pivot_scan")
    assert scan["executed"] is True and ("refused" in scan["result"].lower()
                                         or "not authorized" in scan["result"].lower())
    assert not ctx.get("hosts")


def test_forensic_triage_hook_runs_on_loot():
    triaged = {"/loot/dump.bin": {"binwalk": "LZMA at 0x40", "strings": 12}}
    out, ctx = _run()
    # re-run with a triage callable + a file in context
    s = _scope()
    execs, contexts = build_chain_executors(
        scope=s, triage=lambda files: triaged, **_backends())
    contexts.setdefault(ARMED, {})["files"] = ["/loot/dump.bin"]
    op = Operator(s, offense_enabled=True,
                  caps={"capture.handshake": True, "radio.wifi.uplink": True})
    op.run([ARMED], execs, recon=lambda: (True, "seen"))
    assert contexts[ARMED]["loot"]["triage"] == triaged
