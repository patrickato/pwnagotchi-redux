"""Source export gate binds operator-selected country to mounted root."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image"))
SPEC = importlib.util.spec_from_file_location(
    "redux_country_export", ROOT / "image/prepare_boot_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture(tmp_path, profile="lite"):
    source = ROOT / "image"
    tree = tmp_path / "pi-gen"
    export = tree / "export-image"
    stage = export / "05-finalise"
    stage.mkdir(parents=True)
    if profile == "lite":
        (stage / "01-run.sh").write_text(
            '#!/bin/bash\nunmount "${ROOTFS_DIR}"\n')
    else:
        (stage / "01-run.sh").write_text(
            '#!/bin/bash\nsource "${STAGE_DIR}/redux-ota-clone.sh"\n')
        (export / "redux-ota-clone.sh").write_text(
            '#!/bin/bash\npython3 "${STAGE_DIR}/redux-boot-payload.py" '
            '--profile ab --root "$ROOTFS_DIR" --other-root "$other"\n')
    return source, tree, stage / "01-run.sh", export / "redux-ota-clone.sh"


def test_lite_staging_requires_selected_country_before_unmount(tmp_path):
    source, tree, final, _ = fixture(tmp_path)
    gate.prepare(source, tree, "lite", expected_country="US")
    script = final.read_text()
    assert script.index("redux-root-payload.py") < script.index("redux-boot-payload.py")
    assert script.index("redux-root-payload.py") < script.index('unmount "${ROOTFS_DIR}"')
    assert '--root "${ROOTFS_DIR}" --expected-country US' in script
    assert (tree / "export-image/redux-root-payload.py").is_file()
    # root_payload.py imports unit_readiness by bare name at runtime, so its
    # dependency must be staged beside it or the finalise step dies with
    # ModuleNotFoundError: No module named 'unit_readiness'.
    assert (tree / "export-image/unit_readiness.py").is_file()


def test_ab_staging_requires_country_for_both_mounted_roots(tmp_path):
    source, tree, _, clone = fixture(tmp_path, "ab")
    gate.prepare(source, tree, "ab", expected_country="CA")
    script = clone.read_text()
    assert '--root "$ROOTFS_DIR" --other-root "$other" --expected-country CA' in script
    assert script.index("redux-root-payload.py") < script.index("redux-boot-payload.py")


@pytest.mark.parametrize("bad", ["us", "US; reboot", "ZZ", "", "USA", "AA"])
def test_country_export_rejects_invalid_code_before_staging(tmp_path, bad):
    source, tree, _, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="invalid operator-selected"):
        gate.prepare(source, tree, "lite", expected_country=bad)
    assert not (tree / "export-image/redux-root-payload.py").exists()


def test_general_build_without_country_preserves_fail_closed_runtime(tmp_path):
    source, tree, final, _ = fixture(tmp_path)
    gate.prepare(source, tree, "lite")
    script = final.read_text()
    assert "redux-root-payload.py" in script
    assert "--expected-country" not in script


def test_build_wrapper_passes_country_to_export_gate():
    build = (ROOT / "build.sh").read_text()
    assert 'boot_gate_args+=(--expected-country "$REDUX_REGULATORY_COUNTRY")' in build
    assert '"${boot_gate_args[@]}"' in build
