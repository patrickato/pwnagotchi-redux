"""Offline contracts for the single-scoped capture audit; never contact Pi."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"scripts/redux-one-artifact-field-audit.py"
AUDITOR=ROOT/"scripts/capture-artifact-audit.py"


def module():
    spec=importlib.util.spec_from_file_location("redux_one_artifact_audit",SOURCE)
    obj=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def test_plan_is_no_io_no_ssh_and_one_attempt(capsys, monkeypatch):
    mod=module()
    monkeypatch.setattr(mod,"one_shot",lambda *a,**kw:
                        pytest.fail("offline planning contacted hardware"))
    assert mod.main(["--plan"])==0
    p=json.loads(capsys.readouterr().out)
    assert p["ready_row_max"]==1
    assert p["single_ssh_attempt"] is True
    assert p["strict_known_hosts"] is True
    assert p["sudo"] is False
    assert p["raw_capture_export"] is False
    assert p["prepared_artifact_export"] is False
    assert p["publication_to_github"] is False


def test_audit_requires_distinct_operator_approval(capsys, monkeypatch):
    mod=module()
    monkeypatch.setattr(mod,"one_shot",lambda *a,**kw:
                        pytest.fail("no approval, no hardware"))
    assert mod.main(["--audit-once"])==3
    status=json.loads(capsys.readouterr().out)
    assert status["status"]=="unknown"
    assert status["physical_acceptance"]=="UNKNOWN"


def test_ssh_is_one_noninteractive_sandboxed_connection_with_no_data_export(monkeypatch):
    mod=module()
    monkeypatch.setattr(mod,"reviewed_payload",lambda: b"fixed reviewed code")
    monkeypatch.setattr(mod,"approved_host_key",
                        lambda: ("192.168.56.22","/home/operator/private-key"))
    events=[]
    monkeypatch.setattr(mod,"single_attempt_token",
                        lambda: events.append("consume_before_ssh"))
    expected={"schema":1,"format":mod.FORMAT,"status":"verified_bytes",
              "reason":"verified_sha256_inode","checked":1,"ready_rows":1,
              "physical_acceptance":"UNKNOWN","handshake_authenticity":"UNKNOWN",
              "network_changes":False,"pi_file_writes_requested":False}
    def fake_ssh(argv,**kwargs):
        events.append("ssh")
        assert argv[0]=="ssh"
        assert argv.count("pi@192.168.56.22")==1
        assert argv.count("-i")==1
        assert argv[argv.index("-i")+1]=="/home/operator/private-key"
        assert "StrictHostKeyChecking=yes" in argv
        assert "BatchMode=yes" in argv
        assert "PasswordAuthentication=no" in argv
        assert "ClearAllForwardings=yes" in argv
        assert "ConnectionAttempts=1" in argv
        assert all("sudo" not in v and "tcpdump" not in v for v in argv)
        assert kwargs["input"]==b"fixed reviewed code"
        assert kwargs["timeout"]==70
        assert kwargs["capture_output"] is True
        return SimpleNamespace(returncode=0,stdout=json.dumps(expected).encode(),
                               stderr=b"sensitive error ignored")
    result=mod.one_shot(run=fake_ssh)
    assert result==expected
    assert events==["consume_before_ssh","ssh"]


def test_consumed_one_shot_marker_cannot_be_reused(tmp_path, monkeypatch):
    mod=module()
    monkeypatch.setattr(mod,"STATE",tmp_path/"private")
    mod.single_attempt_token()
    assert (mod.STATE/"consumed.json").stat().st_mode & 0o777==0o600
    assert mod.STATE.stat().st_mode & 0o777==0o700
    with pytest.raises(FileExistsError):
        mod.single_attempt_token()


def test_failed_ssh_is_unknown_and_no_second_connection(monkeypatch):
    mod=module()
    monkeypatch.setattr(mod,"reviewed_payload",lambda:b"code")
    monkeypatch.setattr(mod,"approved_host_key",lambda:("192.168.56.22","/key"))
    times=[]
    monkeypatch.setattr(mod,"single_attempt_token",lambda:times.append("consume"))
    def fail(argv,**kwargs):
        times.append("ssh")
        raise subprocess.TimeoutExpired(argv,70,output=b"secret")
    result=mod.one_shot(run=fail)
    assert result["status"]=="unknown"
    assert result["reason"]=="execution_unavailable"
    assert times==["consume","ssh"]


@pytest.mark.parametrize("update", [
    {"status":"verified_bytes","reason":"artifact_integrity_problem"},
    {"status":"verified_bytes","checked":0},
    {"ready_rows":-1},
    {"handshake_authenticity":"PASS"},
    {"raw_capture":"secret"},
    {"network_changes":True},
    {"reason":"private device path"},
])
def test_remote_response_rejects_private_or_forged_data(update):
    mod=module()
    valid={"schema":1,"format":mod.FORMAT,"status":"verified_bytes",
           "reason":"verified_sha256_inode","checked":1,"ready_rows":1,
           "physical_acceptance":"UNKNOWN","handshake_authenticity":"UNKNOWN",
           "network_changes":False,"pi_file_writes_requested":False}
    valid.update(update)
    with pytest.raises((ValueError,TypeError)):
        mod.classify(json.dumps(valid),0)


def test_duplicate_json_keys_and_nonfinite_values_fail_closed():
    mod=module()
    baseline='{"schema":1,"schema":1}'
    with pytest.raises(ValueError):
        mod.classify(baseline,0)
    with pytest.raises(ValueError):
        mod.classify('{"schema":NaN}',0)


def test_auditor_is_bounded_and_never_sends_bytes_back():
    mod=module()
    source=AUDITOR.read_text(encoding="utf8")
    payload=(mod.REMOTE_PRELUDE+"\nAUDITOR_SOURCE="+repr(source)+
             "\n"+mod.REMOTE_EPILOGUE).encode()
    assert len(payload)<=mod.MAX_SOURCE_BYTES
    assert "def mount_identity" in payload.decode()
    assert "max_rows=1" in payload.decode()
    assert "status" in payload.decode()
    assert 'exec(compile(AUDITOR_SOURCE' in payload.decode()
    assert "subprocess" not in mod.REMOTE_PRELUDE
    assert "/captures/jobs.db" not in json.dumps(mod.plan())
    assert "session-collect" not in mod.REMOTE_PRELUDE


def scratch(tmp_path):
    captures=tmp_path/"captures"
    captures.mkdir()
    ready=captures/"ready"
    ready.mkdir()
    db=captures/"jobs.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE artifacts (source_sha TEXT, status TEXT, output_sha TEXT, output_path TEXT)")
    return captures,ready,db


def prepared(db,ready,content):
    sha=hashlib.sha256(content).hexdigest()
    path=ready/(sha+".hc22000")
    path.write_bytes(content)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO artifacts VALUES (?,?,?,?)",("f"*64,"ready",sha,str(path)))
    return path


def locally_run_synthetic_remote(tmp_path, *, row_count):
    """Exercise the actual in-memory remote payload with no Pi paths."""
    mod=module()
    captures,ready,db=scratch(tmp_path)
    for i in range(row_count):
        prepared(db,ready,("synthetic-only-%d"%i).encode())
    remote_prelude=mod.REMOTE_PRELUDE.replace(
        'ROOT = Path("/captures")',
        'ROOT = Path('+repr(str(captures))+')')
    # Synthetic fixtures deliberately fake the physical mount gate; the
    # deployed program retains its immutable /captures REDUXCAP check.
    remote_prelude += '\ndef mount_identity():\n    return (1, 1, "1:1")\n'
    auditor=AUDITOR.read_text("utf8")
    payload=(remote_prelude+"\nAUDITOR_SOURCE="+repr(auditor)+
             "\n"+mod.REMOTE_EPILOGUE).encode()
    assert len(payload)<=mod.MAX_SOURCE_BYTES
    out=subprocess.run([sys.executable,"-I","-c",mod.BOOTSTRAP],
                       input=payload,capture_output=True,timeout=10,check=False)
    assert out.returncode==0, out.stderr.decode()[:180]
    return mod.classify(out.stdout.decode(),0)


@pytest.mark.parametrize("rows,expected_status,expected_reason,checked", [
    (0,"unknown","no_ready_row",0),
    (1,"verified_bytes","verified_sha256_inode",1),
    (2,"unknown","row_scope_exceeded",0),
])
def test_actual_memory_only_remote_bundle_on_synthetic_ledger(
        tmp_path,rows,expected_status,expected_reason,checked):
    result=locally_run_synthetic_remote(tmp_path,row_count=rows)
    assert result["status"]==expected_status
    assert result["reason"]==expected_reason
    assert result["checked"]==checked
    assert result["handshake_authenticity"]=="UNKNOWN"
    assert result["pi_file_writes_requested"] is False


def test_unverified_mount_refuses_ledger_even_when_ready_fixture_exists(tmp_path):
    mod=module()
    captures,ready,db=scratch(tmp_path)
    prepared(db,ready,b"test only")
    prelude=mod.REMOTE_PRELUDE.replace(
        'ROOT = Path("/captures")','ROOT = Path('+repr(str(captures))+')')
    # No overriding the label/mount check: the disposable test path
    # cannot accidentally be authenticated as physical REDUXCAP.
    payload=(prelude+"\nAUDITOR_SOURCE="+repr(AUDITOR.read_text("utf8"))+
             "\n"+mod.REMOTE_EPILOGUE).encode()
    out=subprocess.run([sys.executable,"-I","-c",mod.BOOTSTRAP],
                       input=payload,capture_output=True,timeout=10,check=False)
    assert out.returncode==0
    result=mod.classify(out.stdout.decode(),0)
    assert result["status"]=="unknown"
    assert result["reason"]=="mount_unverified"
    assert result["checked"]==0


def test_unprivileged_capture_directory_is_unknown_before_opening_ledger(tmp_path):
    """Simulate read denial; no sudo, hashing, or file-content interpretation."""
    mod=module()
    captures,ready,db=scratch(tmp_path)
    prepared(db,ready,b"synthetic secretless content")
    prelude=mod.REMOTE_PRELUDE.replace(
        'ROOT = Path("/captures")','ROOT = Path('+repr(str(captures))+')')
    prelude += (
        '\ndef mount_identity():\n    return (1, 1, "1:1")\n'
        '\nos.access=lambda path, flags: False\n'
    )
    audit_source='def audit(*args, **kwargs):\n    raise AssertionError("denied preflight must not invoke audit")\n'
    payload=(prelude+"\nAUDITOR_SOURCE="+repr(audit_source)+
             "\n"+mod.REMOTE_EPILOGUE).encode()
    out=subprocess.run([sys.executable,"-I","-c",mod.BOOTSTRAP],
                       input=payload,capture_output=True,timeout=10,check=False)
    assert out.returncode==0, out.stderr.decode()[:100]
    verdict=mod.classify(out.stdout.decode(),0)
    assert verdict["status"]=="unknown"
    assert verdict["reason"]=="read_permission_unavailable"
    assert verdict["ready_rows"]==0 and verdict["checked"]==0
    assert verdict["pi_file_writes_requested"] is False


@pytest.mark.parametrize("issues,expected", [
    ({"schema_missing":1},"ledger_schema_missing"),
    ({"output_directory_untrusted":1},"output_directory_unavailable"),
    ({"audit_unavailable":1},"audit_unavailable"),
])
def test_uninspected_ledger_or_directory_never_claims_artifact_damage(
        tmp_path,issues,expected):
    mod=module()
    captures,ready,db=scratch(tmp_path)
    prelude=mod.REMOTE_PRELUDE.replace(
        'ROOT = Path("/captures")','ROOT = Path('+repr(str(captures))+')')
    prelude += '\ndef mount_identity():\n    return (1, 1, "1:1")\n'
    audit_source=('def audit(*args, **kwargs):\n    return '+
                  repr({"result":"attention","checked":0,
                        "ready_rows":0,"issues":issues})+'\n')
    payload=(prelude+"\nAUDITOR_SOURCE="+repr(audit_source)+
             "\n"+mod.REMOTE_EPILOGUE).encode()
    out=subprocess.run([sys.executable,"-I","-c",mod.BOOTSTRAP],
                       input=payload,capture_output=True,timeout=10,check=False)
    assert out.returncode==0, out.stderr.decode()[:100]
    verdict=mod.classify(out.stdout.decode(),0)
    assert verdict["status"]=="unknown"
    assert verdict["reason"]==expected
    assert verdict["checked"]==0



def test_actual_ingest_ownership_contract_requires_distinct_privileged_audit():
    """A nonroot read attempt is not enough for 0700/0600 root-owned outputs."""
    source=(ROOT/"redux/crack/ingest.py").read_text()
    unit=(ROOT/"systemd/redux-capture-ingest.service").read_text()
    launch=(ROOT/"scripts/redux-one-artifact-field-audit.py").read_text()
    assert "User=root" in unit.splitlines()
    assert "os.chmod(settings.output_dir, 0o700)" in source
    assert "os.chmod(settings.database, 0o600)" in source
    assert '"pi@"+host' in launch
    assert '"sudo",' not in launch
    # Fail if source evolves: a real privileged audit requires a separately
    # reviewed, fixed command profile, never silent changes to file modes.
    assert '"read_permission_unavailable"' in launch
