"""Native UI preview tests; actual Pillow render, no display or input access."""
from __future__ import annotations

import json
import os
import stat

import pytest
from PIL import Image

from redux.display import fbui, preview


def test_offline_preview_uses_actual_renderer_and_never_claims_live_hardware():
    frames = preview.build()
    assert list(frames) == ["overview", "captures", "doctor", "radios", "trends"]
    for data in frames.values():
        assert data.startswith(bytes.fromhex("89504e470d0a1a0a"))
        assert len(data) < 2_000_000


def test_page_layout_is_distinct_and_rotation_changes_pixels():
    frames = preview.build()
    assert frames["overview"] != frames["doctor"]
    assert frames["captures"] != frames["doctor"]
    rotated = preview.build(pages=("overview",), rotate=180)
    assert rotated["overview"] != frames["overview"]
    with pytest.raises(ValueError):
        preview.build(pages=("made-up",))
    with pytest.raises(ValueError):
        preview.build(rotate=90)


def test_private_export_creates_exact_four_files(tmp_path):
    names = preview.publish(str(tmp_path), preview.build())
    assert names == ["redux-overview.png", "redux-captures.png", "redux-doctor.png", "redux-radios.png", "redux-trends.png"]
    for name in names:
        target = tmp_path / name
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        with Image.open(target) as img:
            assert img.size == (480, 320)
            assert img.format == "PNG"
            img.verify()
    assert set(p.name for p in tmp_path.iterdir()) == set(names)


def test_existing_file_or_symlink_is_never_overwritten(tmp_path):
    victim = tmp_path / "do-not-touch"
    victim.write_bytes(b"original")
    (tmp_path / "redux-overview.png").symlink_to(victim)
    with pytest.raises(FileExistsError):
        preview.publish(str(tmp_path), preview.build())
    assert victim.read_bytes() == b"original"
    assert (tmp_path / "redux-overview.png").is_symlink()
    assert sorted(x.name for x in tmp_path.iterdir()) == [
        "do-not-touch", "redux-overview.png"]


def test_multi_page_export_rolls_back_only_new_files_on_conflict(tmp_path):
    (tmp_path / "redux-doctor.png").write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        preview.publish(str(tmp_path), preview.build())
    assert (tmp_path / "redux-doctor.png").read_bytes() == b"keep me"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["redux-doctor.png"]


def test_output_dir_symlink_refused(tmp_path):
    other = tmp_path / "real"
    other.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(other, target_is_directory=True)
    with pytest.raises(OSError):
        preview.publish(str(alias), preview.build())
    assert list(other.iterdir()) == []


def test_private_status_snapshot_requires_valid_small_regular_file(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "runtime": {"state": "running", "updated_utc": 100},
        "doctor": {"overall": "ok"},
    }))
    assert preview._snapshot(str(path))["runtime"]["state"] == "running"
    assert fbui.status_view(preview._snapshot(str(path)), now=150)["stale"]
    path.write_text(json.dumps({"runtime": {}, "doctor": []}))
    with pytest.raises(ValueError, match="live status"):
        preview._snapshot(str(path))
    path.write_text("x" * (fbui.MAX_STATUS + 1))
    with pytest.raises(ValueError):
        preview._snapshot(str(path))
    alias = tmp_path / "symlink.json"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        preview._snapshot(str(alias))


def test_no_status_probes_or_pi_devices_touched_by_offline_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fbui, "read_status", lambda *_a: pytest.fail("network opened"))
    monkeypatch.setattr(fbui, "write_frame", lambda *_a, **_k: pytest.fail("fb written"))
    monkeypatch.setattr(fbui, "framebuffer_info", lambda *_a: pytest.fail("fb probed"))
    assert preview.main(["--output-dir", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "offline_unverified"
    assert result["physical_display_verified"] is False
    assert result["touch_verified"] is False
    assert len(result["files"]) == 5


def test_live_unavailable_fails_without_fake_offline_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(fbui, "read_status", lambda *_a: None)
    with pytest.raises(SystemExit) as failure:
        preview.main(["--output-dir", str(tmp_path), "--live"])
    assert failure.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_status_json_cli_renders_single_page_and_is_not_physical_acceptance(tmp_path, capsys):
    dest = tmp_path / "dest"
    dest.mkdir()
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "runtime": {"state": "degraded", "updated_utc": 100},
        "doctor": {"overall": "degraded", "findings": []},
    }))
    assert preview.main(["--output-dir", str(dest), "--status-json", str(status),
                         "--page", "doctor"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "saved_status"
    assert result["files"] == ["redux-doctor.png"]
    assert result["physical_display_verified"] is False
