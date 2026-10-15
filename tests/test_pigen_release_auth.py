"""Signed Debian archive bootstrap: pinned script adapter, hardware free."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "image/pigen_release_auth.py"
spec = importlib.util.spec_from_file_location("pigen_release_auth", SOURCE)
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


def fixture(tmp_path, text=None):
    file = tmp_path / "scripts/common"
    file.parent.mkdir(parents=True)
    file.write_text(text if text is not None else (
        "bootstrap(){\n"
        "\tlocal BOOTSTRAP_ARGS=()\n"
        "\tBOOTSTRAP_ARGS+=(--arch arm64)\n"
        "\tBOOTSTRAP_ARGS+=(--include gnupg)\n"
        "\tBOOTSTRAP_ARGS+=(\"$@\")\n"
        "}\n"
    ))
    return file


def test_pinned_bootstrap_forces_signature_verification(tmp_path):
    file = fixture(tmp_path)
    original = file.read_text()
    result = auth.adapt(tmp_path)
    assert result["signed_release_required"]
    staged = file.read_text()
    assert staged.count("--force-check-gpg") == 1
    assert staged.replace(
        "\tBOOTSTRAP_ARGS+=(--force-check-gpg)\n", ""
    ) == original
    assert "--no-check-gpg" not in staged


def test_unknown_upstream_bootstrap_drift_fails_without_rewrite(tmp_path):
    file = fixture(tmp_path, "bootstrap(){\n  BOOTSTRAP_ARGS+=(--arch arm64)\n}\n")
    original = file.read_bytes()
    with pytest.raises(ValueError, match="drifted"):
        auth.adapt(tmp_path)
    assert file.read_bytes() == original


@pytest.mark.parametrize("danger", ("--no-check-gpg", "--allow-unauthenticated",
                                     "--force-check-gpg"))
def test_existing_signature_override_fails_closed(tmp_path, danger):
    file = fixture(tmp_path)
    original = file.read_text() + f"# {danger}\n"
    file.write_text(original)
    with pytest.raises(ValueError, match="override"):
        auth.adapt(tmp_path)
    assert file.read_text() == original


def test_missing_or_linked_upstream_script_refused(tmp_path):
    with pytest.raises(ValueError, match="missing or linked"):
        auth.adapt(tmp_path)
    target = fixture(tmp_path)
    copy = tmp_path / "real"
    target.rename(copy)
    target.symlink_to(copy)
    with pytest.raises(ValueError, match="linked"):
        auth.adapt(tmp_path)


def test_full_build_wires_gate_before_nexmon_or_image_build():
    build = (SOURCE.parent.parent / "build.sh").read_text()
    assert (build.index('pigen_release_auth.py')
            > build.index('pigen_dependencies.py'))
    assert (build.index('pigen_release_auth.py')
            < build.index('NEXMON_DIR="$BUILD_DIR/nexmon"'))
    assert (build.index('host_preflight.py" --workspace "$BUILD_DIR"')
            < build.index('checkout_source "$PI_GEN_URL"'))
