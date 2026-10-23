"""Audit worker throughput: wider runtime cap + backlog draining."""
from __future__ import annotations

import json

import pytest

import redux.crack.audit as audit
from redux.crack.audit import AuditSettings


def _settings(tmp_path, **kw):
    return AuditSettings(True, tmp_path / "w", tmp_path / "s.json",
                         tmp_path / "a", tmp_path / "db", **kw)


def test_runtime_cap_widened_and_default_raised(tmp_path):
    assert _settings(tmp_path).max_runtime_seconds == 1800         # raised from 600
    _settings(tmp_path, max_runtime_seconds=86400).validate()      # 24h overnight run is allowed
    _settings(tmp_path, max_runtime_seconds=1800).validate()
    for bad in (29, 86401):
        with pytest.raises(ValueError):
            _settings(tmp_path, max_runtime_seconds=bad).validate()


def _cfg(tmp_path):
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text(
        f'[pipeline]\ndatabase = "{tmp_path/"db"}"\n'
        f'[audit]\nenabled = true\nwordlist = "{tmp_path/"w"}"\n'
        f'scope_file = "{tmp_path/"s.json"}"\nresults_dir = "{tmp_path/"a"}"\n')
    (tmp_path / "w").write_text("x\n")
    (tmp_path / "s.json").write_text("{}")
    return cfg


def _fake_worker(monkeypatch, statuses):
    seq = iter(statuses)
    class FakeWorker:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def once(self): return next(seq)
    monkeypatch.setattr(audit, "AuditWorker", FakeWorker)


def test_main_drains_a_backlog(tmp_path, monkeypatch, capsys):
    _fake_worker(monkeypatch, [{"status": "recovered"}, {"status": "exhausted"}, {"status": "idle"}])
    assert audit.main(["--config", str(_cfg(tmp_path))]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "batch" and out["processed"] == 2 and len(out["runs"]) == 3


def test_main_respects_max_jobs(tmp_path, monkeypatch, capsys):
    # never-idle stream: --max-jobs bounds how many we drain in one run
    _fake_worker(monkeypatch, [{"status": "exhausted"}] * 10)
    audit.main(["--config", str(_cfg(tmp_path)), "--max-jobs", "3"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "batch" and out["processed"] == 3 and len(out["runs"]) == 3


def test_main_single_run_keeps_simple_shape(tmp_path, monkeypatch, capsys):
    _fake_worker(monkeypatch, [{"status": "idle"}])
    audit.main(["--config", str(_cfg(tmp_path))])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "idle"   # one run -> unchanged single-dict shape
