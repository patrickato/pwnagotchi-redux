"""Regression for the pinned pi-gen dependency contract and private build password."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    path = ROOT / "image" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

deps = load("pigen_dependencies")
creds = load("build_credentials")


def upstream(tmp_path, content=None):
    tree = tmp_path / "pi-gen"
    tree.mkdir()
    (tree / "depends").write_text(content if content is not None
        else "quilt\nqemu-arm-static:qemu-user-static\nbsdtar:libarchive-tools\n")
    return tree


def archworks(argv, **kwargs):
    assert argv == ["arch-test", "arm64"]
    return subprocess.CompletedProcess(argv, 0, "arm64\n", "")


def test_new_ubuntu_uses_real_arm64_emulator_not_fake_package(tmp_path):
    tree = upstream(tmp_path)
    tools = lambda command: ("/usr/bin/" + command if command == "qemu-aarch64" else None)
    report = deps.adapt(tree, machine="x86_64", which=tools, runner=archworks)
    assert report["changed"]
    assert (tree / "depends").read_text() == (
        "quilt\nqemu-aarch64:qemu-user\nbsdtar:libarchive-tools\n")


def test_older_qemu_is_left_unchanged(tmp_path):
    tree = upstream(tmp_path)
    original = (tree / "depends").read_text()
    report = deps.adapt(tree, machine="x86_64",
        which=lambda command: "/usr/bin/" + command if command == "qemu-arm-static" else None)
    assert report["changed"] is False
    assert (tree / "depends").read_text() == original


@pytest.mark.parametrize("bad", [
    "", "qemu-aarch64:qemu-user\n",
    "qemu-arm-static:qemu-user-static\nqemu-arm-static:qemu-user-static\n",
])
def test_upstream_drift_fails_without_rewrite(tmp_path, bad):
    tree = upstream(tmp_path, bad)
    with pytest.raises(ValueError, match="anchor"):
        deps.adapt(tree, machine="x86_64")
    assert (tree / "depends").read_text() == bad


def test_bad_binfmt_fails_before_rewriting(tmp_path):
    tree = upstream(tmp_path)
    original = (tree / "depends").read_text()
    def blocked(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no binfmt")
    with pytest.raises(ValueError, match="ARM64 chroot"):
        deps.adapt(tree, machine="x86_64",
            which=lambda command: "/usr/bin/" + command if command == "qemu-aarch64" else None,
            runner=blocked)
    assert (tree / "depends").read_text() == original


def config_tree(tmp_path):
    build = tmp_path / "work"
    config = build / "pi-gen" / "config"
    config.parent.mkdir(parents=True)
    config.write_text("DISABLE_FIRST_BOOT_USER_RENAME=1\nENABLE_SSH=0\n")
    return config, build / "operator-login.txt"


def test_credential_is_private_unique_and_resolves_pigen_guard(tmp_path):
    config, destination = config_tree(tmp_path)
    secret = "abcdef" * 8  # 48 hex characters = 192 bits
    report = creds.generate(config, destination, random_hex=lambda: secret)
    assert report["password_bits"] == 192
    assert config.stat().st_mode & 0o077 == 0
    assert destination.stat().st_mode & 0o077 == 0
    assert "Password: " + secret in destination.read_text()
    loaded = subprocess.run([
        "bash", "-c",
        'source "$1"; test -n "$FIRST_USER_PASS" && '+
        'test "$FIRST_USER_PASS" = "$2" && '+
        'test "$DISABLE_FIRST_BOOT_USER_RENAME" = 1',
        "bash", str(config), secret
    ], capture_output=True, text=True)
    assert loaded.returncode == 0, loaded.stderr
    with pytest.raises(ValueError, match="already exists"):
        creds.generate(config, destination, random_hex=lambda: secret)


@pytest.mark.parametrize("invalid", ["", "AAAA" * 12, "z" * 48, "a" * 47])
def test_invalid_generated_credentials_fail_closed(tmp_path, invalid):
    config, dest = config_tree(tmp_path)
    original = config.read_text()
    with pytest.raises(ValueError, match="192-bit"):
        creds.generate(config, dest, random_hex=lambda: invalid)
    assert not dest.exists() and config.read_text() == original


def test_credential_path_must_stay_inside_build(tmp_path):
    config, dest = config_tree(tmp_path)
    with pytest.raises(ValueError, match="workspace root"):
        creds.generate(config, tmp_path / "outside.txt", random_hex=lambda: "a" * 48)


def test_explicit_dev_login_is_short_local_only_and_private(tmp_path):
    config, destination = config_tree(tmp_path)
    report = creds.generate(config, destination, dev_login=True)
    assert report["dev_login"] is True
    assert config.stat().st_mode & 0o077 == 0
    assert destination.stat().st_mode & 0o077 == 0
    credential = destination.read_text()
    assert "Username: pi" in credential
    assert "Password: redux" in credential
    source = subprocess.run(
        ["bash", "-c",
         'source "$1"; printf "%s:%s:%s:%s" "$FIRST_USER_NAME" "$FIRST_USER_PASS" "$ENABLE_SSH" "${PUBKEY_ONLY_SSH:-0}"',
         "bash", str(config)], capture_output=True, text=True, timeout=5)
    assert source.returncode == 0, source.stderr
    assert source.stdout == "pi:redux:0:0"


def test_dev_login_never_accepts_injected_password(tmp_path):
    config, dest = config_tree(tmp_path)
    with pytest.raises(ValueError, match="cannot inject"):
        creds.generate(config, dest, dev_login=True, random_hex=lambda: "a" * 48)
    assert not dest.exists()


def test_dev_login_keeps_authorized_key_only_ssh(tmp_path):
    config, destination = config_tree(tmp_path)
    with config.open("a") as out:
        out.write("ENABLE_SSH=1\nPUBKEY_ONLY_SSH=1\n")
    creds.generate(config, destination, dev_login=True)
    result = subprocess.run(["bash", "-c",
        'source "$1"; printf "%s/%s/%s/%s" "$FIRST_USER_NAME" "$FIRST_USER_PASS" "$ENABLE_SSH" "$PUBKEY_ONLY_SSH"',
        "bash", str(config)], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "pi/redux/1/1"
