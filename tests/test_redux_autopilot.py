"""Offline-only regression tests for the opt-in Codex autopilot wrapper."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "redux-autopilot.py"
INSTALLER = ROOT / "scripts" / "install-redux-autopilot.sh"


def module():
    spec = importlib.util.spec_from_file_location("redux_autopilot", SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_default_plan_is_offline_and_does_not_run_agent(monkeypatch, capsys):
    instance = module()
    monkeypatch.setattr(instance, "invoke", lambda *args, **kw: pytest.fail("network or subprocess attempted"))
    monkeypatch.setattr(instance, "cycle", lambda: pytest.fail("agent attempted"))
    assert instance.main(["plan"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pi_access"] is False
    assert data["sudo"] is False
    assert data["radio_changes"] is False
    assert data["card_changes"] is False
    assert "non_force_push" in data["publishing"]


def test_cycle_requires_explicit_local_opt_in():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "cycle"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "requires --enable-agent" in result.stderr


def test_source_path_allowlist_is_strict(tmp_path):
    instance = module()
    filename = tmp_path / "redux" / "new_worker.py"
    filename.parent.mkdir()
    filename.write_text("# source only\n")
    assert instance.allowable(tmp_path, ["redux/new_worker.py"])[0]
    assert not instance.allowable(tmp_path, ["scripts/redux-autopilot.py"])[0]
    assert not instance.allowable(tmp_path, [".github/workflows/new.yml"])[0]
    assert not instance.allowable(tmp_path, ["AGENTS.md"])[0]
    assert not instance.allowable(tmp_path, ["redux/../.ssh/id_ed25519"])[0]
    assert not instance.allowable(tmp_path, ["redux/missing.py"])[0]
    assert not instance.allowable(tmp_path, ["redux/new_worker.py"] * 36)[0]
    link = tmp_path / "tests" / "linked.py"
    link.parent.mkdir()
    link.symlink_to(filename)
    assert not instance.allowable(tmp_path, ["tests/linked.py"])[0]
    # Final component need not itself be a symlink to escape an allowed tree.
    linked_directory = tmp_path / "redux" / "alias"
    linked_directory.symlink_to(tmp_path)
    assert not instance.allowable(tmp_path, ["redux/alias/other.py"])[0]
    assert instance.allowable(tmp_path, ["redux/alias/other.py"])[1] == "symlinked_parent"
    filename.write_bytes(b"x" * (instance.MAX_SINGLE_BYTES + 1))
    assert not instance.allowable(tmp_path, ["redux/new_worker.py"])[0]


def test_git_porcelain_parsing_refuses_deletions_and_renames(tmp_path):
    instance = module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Autopilot Fixture"], cwd=tmp_path, check=True)
    directory = tmp_path / "redux"
    directory.mkdir()
    original = directory / "original.py"
    original.write_text("ORIGINAL = 1\n")
    subprocess.run(["git", "add", "--", "redux/original.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    original.write_text("ORIGINAL = 2\n")
    assert instance.changed_names(tmp_path) == ["redux/original.py"]
    original.unlink()
    with pytest.raises(ValueError, match="rename_copy_delete_or_conflict"):
        instance.changed_names(tmp_path)


def test_failed_preflight_does_not_try_to_access_pi(tmp_path, monkeypatch, capsys):
    instance = module()
    monkeypatch.setattr(instance, "STATE", tmp_path / "private")
    monkeypatch.setattr(instance.shutil, "which",
                        lambda name: None if name == "codex" else "/usr/bin/" + name)
    monkeypatch.setattr(instance, "invoke",
                        lambda *args, **kw: pytest.fail("subprocess attempted"))
    assert instance.cycle() == 2
    record = json.loads(capsys.readouterr().out)
    assert record["reason"] == "codex_cli_unavailable"
    assert record["scope"] == "offline_source_only"
    assert (tmp_path / "private" / "last.json").stat().st_mode & 0o777 == 0o600


def test_installer_has_valid_shell_syntax():
    result = subprocess.run(["bash", "-n", str(INSTALLER)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_codex_approval_flags_are_top_level_not_exec_options():
    instance = module()
    argv = instance.CODEX_COMMAND
    assert argv == ("codex", "--sandbox", "workspace-write",
                    "--ask-for-approval", "never", "exec", "-")
    assert argv.index("--ask-for-approval") < argv.index("exec")
    assert "dangerously-bypass-approvals-and-sandbox" not in argv
