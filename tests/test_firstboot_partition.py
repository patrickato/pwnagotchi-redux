"""Stock pi-gen firstboot resize paths must never touch Redux captures."""
from pathlib import Path
import importlib.util
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "image/stage-redux/10-overlay/files/disable-resize.py"
SPEC = importlib.util.spec_from_file_location("redux_disable_resize", SOURCE)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def root(tmp_path, token="init=/usr/lib/raspberrypi-sys-mods/firstboot"):
    r = tmp_path / "root"
    boot = r / "boot/firmware"
    boot.mkdir(parents=True)
    (boot / "cmdline.txt").write_text(
        f"console=tty1 root=ROOTDEV rootwait quiet {token}\n")
    init = r / "etc/init.d"
    init.mkdir(parents=True)
    (init / "resize2fs_once").write_text("#!/bin/sh\n")
    return r


@pytest.mark.parametrize("stock", [
    "init=/usr/lib/raspberrypi-sys-mods/firstboot",
    "init=/usr/lib/raspi-config/init_resize.sh",
])
def test_stock_firstboot_removed_without_root_device_change(tmp_path, stock):
    r = root(tmp_path, stock)
    outcome = policy.disable(r)
    assert outcome["removed_init"] == stock
    cmdline = (r / "boot/firmware/cmdline.txt").read_text()
    assert cmdline == "console=tty1 root=ROOTDEV rootwait quiet\n"
    assert (r / "etc/init.d/resize2fs_once").exists()  # masking is a later stage action


@pytest.mark.parametrize("corrupt", [
    "root=ROOTDEV init=/bin/sh",
    "root=ROOTDEV firstboot",
    "root=ROOTDEV",
    "init=/usr/lib/raspberrypi-sys-mods/firstboot",
    "root=ROOTDEV init=/usr/lib/raspberrypi-sys-mods/firstboot firstboot",
])
def test_unknown_initializer_or_missing_expected_resize_fails_without_mutation(tmp_path, corrupt):
    r = root(tmp_path)
    path = r / "boot/firmware/cmdline.txt"
    path.write_text(corrupt + "\n")
    with pytest.raises(ValueError):
        policy.disable(r)
    assert path.read_text() == corrupt + "\n"


def test_missing_legacy_script_fails_before_cmdline_mutation(tmp_path):
    r = root(tmp_path)
    (r / "etc/init.d/resize2fs_once").unlink()
    previous = (r / "boot/firmware/cmdline.txt").read_text()
    with pytest.raises(ValueError, match="resize2fs_once"):
        policy.disable(r)
    assert (r / "boot/firmware/cmdline.txt").read_text() == previous


def test_preexisting_unit_refused_and_no_mutation(tmp_path):
    r = root(tmp_path)
    service = r / "etc/systemd/system/resize2fs_once.service"
    service.parent.mkdir(parents=True)
    service.symlink_to("/dev/null")
    with pytest.raises(ValueError, match="unexpectedly"):
        policy.disable(r)
    assert "firstboot" in (r / "boot/firmware/cmdline.txt").read_text()


def test_lite_and_ab_export_gate_enforce_no_resize_tokens():
    source = (SOURCE.parents[3] / "boot_payload.py").read_text()
    assert "firstboot/resize token remains" in source
    assert "resize2fs_once must be masked" in source
