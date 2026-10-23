"""Bounded read-only deeper Pi 4 Bettercap diagnostics."""
from pathlib import Path
import subprocess

from conftest import run_field_script_unprivileged

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/field-engine-check.sh"


def test_script_is_valid_shell_and_has_no_radio_changes():
    result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True,
                            text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    s = SCRIPT.read_text()
    assert "sleep 22" in s
    assert "journalctl -u redux-live.service" in s
    assert "tcp_8081" in s
    assert "captures/live/live.json" in s
    assert "bettercap.log" in s
    for forbidden in ("systemctl restart", "systemctl stop", "rfkill unblock",
                      "iw reg set", "reboot", "api.rest.password"):
        assert forbidden not in s


def test_requires_root_before_any_probes():
    result = run_field_script_unprivileged(SCRIPT)
    assert result.returncode == 2
    assert "sudo" in result.stderr
