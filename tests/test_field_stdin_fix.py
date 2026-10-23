"""Check no-typing disposable Pi Bettercap stdin field repair."""
from pathlib import Path
import subprocess

from conftest import run_field_script_unprivileged

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/field-stdin-fix.sh"


def test_field_stdin_repair_script_is_valid_and_bounded():
    syntax = subprocess.run(["bash", "-n", str(SCRIPT)],
                            text=True, capture_output=True, timeout=5)
    assert syntax.returncode == 0, syntax.stderr
    script = SCRIPT.read_text()
    assert "/opt/redux/redux/core/live_runtime.py" in script
    assert "stdin=subprocess.DEVNULL" in script
    assert "stdin=subprocess.PIPE" in script
    assert "systemctl stop redux-live.service" in script
    assert "systemctl restart redux-live.service" in script
    assert "input_pipe.close()" in script
    assert "python3 -m py_compile" in script
    assert "redux-report --full" in script
    assert "rfkill unblock" not in script
    assert "iw reg set" not in script
    assert "git pull" not in script
    assert "sudo sed -i" not in script
    assert "reboot" not in script.split("echo '== Updated Redux field report =='")[-1]


def test_stdin_repair_requires_root_before_mutation():
    result = run_field_script_unprivileged(SCRIPT)
    assert result.returncode == 2
    assert "sudo" in result.stderr


def test_runtime_child_uses_open_pipe_and_closes_after_exit():
    source = (ROOT / "redux/core/live_runtime.py").read_text()
    assert "stdin=subprocess.PIPE, stdout=self.log_handle" in source
    assert "stdin=subprocess.DEVNULL, stdout=self.log_handle" not in source
    segment = source[source.index("def _stop_engine_child"):
                     source.index("def _drop(self):")]
    assert segment.index("input_pipe.close()") > segment.index("child.wait(timeout=0)")
