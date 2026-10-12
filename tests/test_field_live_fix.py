"""Regression for safe field-only repair of Bettercap's HOME and rfkill."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/field-live-fix.sh"


def test_field_live_fix_valid_shell_and_no_implicit_country():
    result = subprocess.run(["bash", "-n", str(SCRIPT)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    script = SCRIPT.read_text()
    assert "sudo" not in script.split("set -euo pipefail", 1)[-1].split("if [[", 1)[0]
    assert 'country=$1' in script
    assert 'iw reg set "$country"' in script
    assert "rfkill unblock wifi" in script
    assert "country=US" not in script
    assert 'state_dir' in script and '"/captures/live"' in script
    assert "systemctl stop redux-live.service" in script
    assert "systemctl daemon-reload" in script
    assert "systemctl restart redux-live.service" in script
    assert "Environment=HOME=/captures/live" in script


def test_field_live_fix_exits_without_args_before_mutation():
    result = subprocess.run(["bash", str(SCRIPT)], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode != 0
    assert "Usage:" in result.stderr


def test_bettercap_child_home_is_confined_to_live_capture_state():
    runtime = (ROOT / "redux/core/live_runtime.py").read_text()
    unit = (ROOT / "image/stage-redux/00-redux/files/redux-live.service").read_text()
    assert 'env={**os.environ, "HOME": str(self.config.state_dir)}' in runtime
    assert "Environment=HOME=/captures/live" in unit
    assert "ProtectHome=true" in unit
    assert "CAP_DAC_OVERRIDE" not in unit
    assert "CAP_FOWNER" not in unit
