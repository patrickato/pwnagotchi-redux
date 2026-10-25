"""Tamper-evident chain-of-authorization log (engine + signing + CLI + scope hook)."""
import json
from pathlib import Path

import pytest

from redux.core.authlog import (
    AuthLog, VerifyResult, generate_key, load_key, record_event,
    default_authlog_path, _entry_hash, GENESIS_PREV,
)


def _canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


# --- chain integrity -------------------------------------------------------- #

def test_records_chain_and_verifies_intact(tmp_path):
    log = AuthLog(str(tmp_path / "a.jsonl"))
    a = log.record("scope.add", {"value": "aa:bb:cc:dd:ee:ff"})
    b = log.record("fire.deauth", {"bssid": "aa:bb:cc:dd:ee:ff"})
    assert a.seq == 0 and a.prev == GENESIS_PREV
    assert b.seq == 1 and b.prev == a.hash          # chained
    r = log.verify()
    assert r.ok and r.count == 2 and r.broken_seq is None


def test_empty_log_verifies_ok(tmp_path):
    r = AuthLog(str(tmp_path / "none.jsonl")).verify()
    assert r.ok and r.count == 0


def test_altering_an_entry_is_caught_as_hash_mismatch(tmp_path):
    p = tmp_path / "a.jsonl"
    log = AuthLog(str(p))
    log.record("scope.add", {"value": "real"})
    log.record("scope.add", {"value": "second"})
    lines = p.read_text().splitlines()
    d = json.loads(lines[0]); d["detail"] = {"value": "FORGED"}      # keep stale hash
    lines[0] = _canon(d)
    p.write_text("\n".join(lines) + "\n")
    r = log.verify()
    assert not r.ok and r.broken_seq == 0 and "hash mismatch" in r.reason


def test_rehashing_a_tampered_entry_breaks_the_chain_at_the_next_one(tmp_path):
    p = tmp_path / "a.jsonl"
    log = AuthLog(str(p))
    log.record("scope.add", {"value": "real"})
    log.record("scope.add", {"value": "second"})
    lines = p.read_text().splitlines()
    d = json.loads(lines[0]); d["detail"] = {"value": "FORGED"}
    d["hash"] = _entry_hash(d["seq"], d["ts"], d["event"], d["detail"], d["prev"])  # recompute
    lines[0] = _canon(d)
    p.write_text("\n".join(lines) + "\n")
    r = log.verify()
    assert not r.ok and r.broken_seq == 1 and "prev-hash" in r.reason


def test_corrupt_line_is_caught(tmp_path):
    p = tmp_path / "a.jsonl"
    log = AuthLog(str(p))
    log.record("scope.add", {"value": "x"})
    p.write_text(p.read_text() + "{ this is not json\n")
    r = log.verify()
    assert not r.ok and "corrupt" in r.reason.lower()


def test_removing_an_entry_is_caught_as_out_of_order(tmp_path):
    p = tmp_path / "a.jsonl"
    log = AuthLog(str(p))
    for v in ("one", "two", "three"):
        log.record("scope.add", {"value": v})
    lines = p.read_text().splitlines()
    del lines[1]                                    # drop the middle entry
    p.write_text("\n".join(lines) + "\n")
    r = log.verify()
    assert not r.ok and r.broken_seq == 1           # seq jumps 0 -> 2


# --- signing ---------------------------------------------------------------- #

def test_signed_entries_verify_with_key_and_fail_with_wrong_key(tmp_path):
    key = generate_key(str(tmp_path / "k.key"))
    log = AuthLog(str(tmp_path / "a.jsonl"), key=key)
    e = log.record("scope.add", {"value": "x"})
    assert e.sig
    assert log.verify().ok                          # self key
    assert not AuthLog(str(tmp_path / "a.jsonl"), key=b"\x00" * 32).verify().ok


def test_key_configured_but_entry_unsigned_fails(tmp_path):
    p = tmp_path / "a.jsonl"
    AuthLog(str(p)).record("scope.add", {"value": "x"})   # unsigned
    r = AuthLog(str(p), key=b"\x01" * 32).verify()
    assert not r.ok and "unsigned" in r.reason


def test_generate_key_refuses_to_clobber_without_force(tmp_path):
    kp = str(tmp_path / "k.key")
    first = generate_key(kp)
    with pytest.raises(FileExistsError):
        generate_key(kp)
    assert load_key(kp) == first                    # original key untouched
    replaced = generate_key(kp, overwrite=True)
    assert replaced != first and load_key(kp) == replaced


def test_load_key_absent_is_none(tmp_path):
    assert load_key(str(tmp_path / "nope.key")) is None


# --- CLI -------------------------------------------------------------------- #

def _ns(**kw):
    from types import SimpleNamespace
    base = dict(log=None, key=None, authlog_cmd=None, json=False, detail=None,
                event=None, force=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_authlog_record_verify_show(tmp_path, capsys):
    from redux.cli import cmd_authlog
    log = str(tmp_path / "a.jsonl")
    key = str(tmp_path / "k.key")
    assert cmd_authlog(_ns(log=log, key=key, authlog_cmd="init-key")) == 0
    capsys.readouterr()
    assert cmd_authlog(_ns(log=log, key=key, authlog_cmd="record",
                           event="engagement.start", detail=["client=acme", "ticket=42"])) == 0
    assert "signed" in capsys.readouterr().out
    assert cmd_authlog(_ns(log=log, key=key, authlog_cmd="verify")) == 0
    assert "OK" in capsys.readouterr().out
    assert cmd_authlog(_ns(log=log, authlog_cmd="show")) == 0
    out = capsys.readouterr().out
    assert "engagement.start" in out and "client=acme" in out


def test_cmd_authlog_verify_detects_tamper_and_returns_nonzero(tmp_path, capsys):
    from redux.cli import cmd_authlog
    p = tmp_path / "a.jsonl"
    AuthLog(str(p)).record("scope.add", {"value": "real"})
    lines = p.read_text().splitlines()
    d = json.loads(lines[0]); d["detail"] = {"value": "FORGED"}
    lines[0] = _canon(d)
    p.write_text("\n".join(lines) + "\n")
    assert cmd_authlog(_ns(log=str(p), authlog_cmd="verify")) == 1
    assert "FAIL" in capsys.readouterr().out


# --- scope hook ------------------------------------------------------------- #

def test_scope_add_is_recorded_in_the_auth_log(tmp_path, monkeypatch):
    from redux.cli import cmd_scope
    from types import SimpleNamespace
    authlog = tmp_path / "authlog.jsonl"
    monkeypatch.setenv("REDUX_AUTHLOG", str(authlog))
    monkeypatch.delenv("REDUX_AUTHLOG_KEY", raising=False)
    assert default_authlog_path() == str(authlog)
    args = SimpleNamespace(file=str(tmp_path / "scope.json"), scope_cmd="add",
                           target="aa:bb:cc:dd:ee:ff", kind=None, label="lab ap",
                           job="homelab", expires_days=0.0)
    assert cmd_scope(args) == 0
    entries = AuthLog(str(authlog)).entries()
    assert len(entries) == 1
    assert entries[0].event == "scope.add"
    assert entries[0].detail["value"] == "aa:bb:cc:dd:ee:ff"
    assert entries[0].detail["job"] == "homelab"
    assert AuthLog(str(authlog)).verify().ok
