"""Pin-contract guard is read-only, fail-closed, and detects exporter drift."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "image/pinned_pigen_contract.py"
SPEC = importlib.util.spec_from_file_location("redux_pigen_contract", SOURCE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)


def fixture(tmp_path):
    tree = tmp_path / "pi-gen"
    for folder in contract._STAGES:
        (tree / folder).mkdir(parents=True, exist_ok=True)
    (tree / "stage2/EXPORT_IMAGE").touch()
    for name, anchors in contract._CONTRACT.items():
        file = tree / name
        file.parent.mkdir(parents=True, exist_ok=True)
        # Minimal script excerpts from the pinned upstream exporter.
        file.write_text("#!/bin/bash -e\n" + "\n".join(anchors) + "\n")
    return tree


def test_contract_accepts_matching_pinned_exporter_anchors(tmp_path):
    result = contract.verify(fixture(tmp_path))
    assert result["overall"] == "contract_ok"
    assert result["blockers"] == []
    assert result["source_fetched"] is True
    assert result["image_built"] is False
    assert result["hardware_boot_verified"] is False


@pytest.mark.parametrize("missing", list(contract._CONTRACT))
def test_missing_upstream_script_blocks_before_patch(tmp_path, missing):
    tree = fixture(tmp_path)
    (tree / missing).unlink()
    report = contract.verify(tree)
    assert missing in report["blockers"]
    assert report["overall"] == "blocked"


@pytest.mark.parametrize("path", list(contract._CONTRACT))
def test_upstream_anchor_drift_blocks_before_nexmon_fetch(tmp_path, path):
    tree = fixture(tmp_path)
    script = tree / path
    first = contract._CONTRACT[path][0]
    script.write_text(script.read_text().replace(first, "upstream changed the export hook"))
    assert path in contract.verify(tree)["blockers"]


def test_duplicate_patch_anchor_refused(tmp_path):
    tree = fixture(tmp_path)
    path = tree / "export-image/prerun.sh"
    with path.open("a") as fd:
        fd.write(contract._CONTRACT["export-image/prerun.sh"][0] + "\n")
    assert "export-image/prerun.sh" in contract.verify(tree)["blockers"]


def test_finalise_order_must_preserve_initramfs_before_unmount(tmp_path):
    tree = fixture(tmp_path)
    path = tree / "export-image/05-finalise/01-run.sh"
    first, unmount, zerofree = contract._CONTRACT["export-image/05-finalise/01-run.sh"][:3]
    original = path.read_text()
    path.write_text(original.replace(first, "TEMP").replace(unmount, first)
                    .replace("TEMP", unmount))
    assert "export-image/05-finalise/01-run.sh" in contract.verify(tree)["blockers"]


def test_missing_stage_marker_refused(tmp_path):
    tree = fixture(tmp_path)
    (tree / "stage2/EXPORT_IMAGE").unlink()
    assert "stage2/EXPORT_IMAGE" in contract.verify(tree)["blockers"]


def test_symlinked_upstream_export_script_refused(tmp_path):
    tree = fixture(tmp_path)
    file = tree / "export-image/prerun.sh"
    destination = tmp_path / "outside.sh"
    destination.write_text(file.read_text())
    file.unlink()
    file.symlink_to(destination)
    assert "export-image/prerun.sh" in contract.verify(tree)["blockers"]


def test_probe_cli_returns_json_and_nonzero_on_drift(tmp_path):
    tree = fixture(tmp_path)
    argv = [sys.executable, str(SOURCE), str(tree)]
    proc = subprocess.run(argv, text=True, capture_output=True, timeout=10)
    assert proc.returncode == 0
    assert json.loads(proc.stdout)["overall"] == "contract_ok"
    (tree / "stage2/EXPORT_IMAGE").unlink()
    proc = subprocess.run(argv, text=True, capture_output=True, timeout=10)
    assert proc.returncode == 2
    assert "stage2/EXPORT_IMAGE" in json.loads(proc.stdout)["blockers"]


def test_build_checks_pinned_exporter_before_fetching_nexmon():
    build = (SOURCE.parent.parent / "build.sh").read_text()
    anchor = 'checkout_source "$PI_GEN_URL" "$PI_GEN_REV" "$BUILD_DIR/pi-gen"'
    guard = 'python3 "$REPO_DIR/image/pinned_pigen_contract.py" "$BUILD_DIR/pi-gen"'
    next_fetch = 'git -C "$NEXMON_DIR" fetch'
    assert build.index(anchor) < build.index(guard) < build.index(next_fetch)
