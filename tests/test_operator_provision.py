"""Secure headless operator provisioning is explicit, local and key-only."""
import base64
import importlib.util
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "image/operator_provision.py"
SPEC = importlib.util.spec_from_file_location("redux_operator_provision", SOURCE)
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)

BLOB = ("AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg4QERITFBUWFxgZGhscHR4f")


def fixture(tmp_path, *, body=None):
    key = tmp_path / "operator.pub"
    key.write_text(body if body is not None
                   else "ssh-ed25519 " + BLOB + " operator@lab\n")
    return key


def test_valid_key_generates_passwordless_ssh_config(tmp_path):
    key = fixture(tmp_path)
    text = p.config_fragment(key)
    assert "FIRST_USER_PASS=''" in text
    assert "ENABLE_SSH=1" in text
    assert "PUBKEY_ONLY_SSH=1" in text
    assert "DISABLE_FIRST_BOOT_USER_RENAME=1" in text
    # Import the generated shell fragment without ever interpolating its body.
    cmd = ["bash", "-c",
           'source "$1"; printf "%s\\n%s\\n%s" "$ENABLE_SSH" "$PUBKEY_ONLY_SSH" "$PUBKEY_SSH_FIRST_USER"',
           "bash", str(tmp_path / "provision.sh")]
    (tmp_path / "provision.sh").write_text(text)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n1\n" + key.read_text().strip()


def test_public_key_with_shell_metacharacters_in_comment_remains_literal(tmp_path):
    body = "ssh-ed25519 " + BLOB + " abc'$(touch SHOULD_NOT_EXIST);\\backtick\n"
    key = fixture(tmp_path, body=body)
    output = p.config_fragment(key)
    assert shlex.quote(body.strip()) in output
    generated = tmp_path / "generated.sh"
    generated.write_text(output)
    result = subprocess.run(["bash", "-c",
                             'source "$1"; printf "%s" "$PUBKEY_SSH_FIRST_USER"',
                             "bash", str(generated)],
                            capture_output=True, text=True, cwd=tmp_path, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == body.strip()
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


@pytest.mark.parametrize("bad", [
    "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n",
    "ssh-ed25519 " + BLOB + "\nssh-ed25519 " + BLOB + "\n",
    "command=evil ssh-ed25519 " + BLOB + "\n",
    "ssh-ed25519 !!!\n",
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGFsbA==\n",
    "ssh-ed25519 " + BLOB + "\x00\n",
])
def test_refuses_private_keys_multiple_keys_and_malformed_data(tmp_path, bad):
    with pytest.raises(ValueError):
        p.public_key(fixture(tmp_path, body=bad))


def test_refuses_symlink_key_file(tmp_path):
    original = fixture(tmp_path)
    shortcut = tmp_path / "shortcut.pub"
    shortcut.symlink_to(original)
    with pytest.raises(ValueError, match="regular"):
        p.public_key(shortcut)


def test_cli_emits_only_shell_fragment_and_refuses_missing_key(tmp_path):
    key = fixture(tmp_path)
    ok = subprocess.run([sys.executable, str(SOURCE), str(key)],
                        capture_output=True, text=True, timeout=5)
    assert ok.returncode == 0, ok.stderr
    assert "PUBKEY_SSH_FIRST_USER=" in ok.stdout
    missing = subprocess.run([sys.executable, str(SOURCE), str(tmp_path / "absent")],
                             capture_output=True, text=True, timeout=5)
    assert missing.returncode != 0


def test_image_builder_explicit_key_provisioning_and_safe_default():
    build = (ROOT / "build.sh").read_text()
    config = (ROOT / "image/config").read_text()
    assert "REDUX_OPERATOR_PUBKEY_FILE" in build
    assert "image/operator_provision.py" in build
    assert "DISABLE_FIRST_BOOT_USER_RENAME=1" in build
    assert "DISABLE_FIRST_BOOT_USER_RENAME=1" in config
    assert "ENABLE_SSH=0" in config
    assert "FIRST_USER_PASS=" not in config
