"""Host-side boot payload export regressions; all artifacts are synthetic."""
from pathlib import Path
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "image"))
import boot_payload as payload

A = "01234567-89ab-cdef-0123-456789abcdef"
B = "fedcba98-7654-3210-fedc-ba9876543210"


def slot(tmp_path, which=None, uuid=None):
    root = tmp_path / (which or "lite")
    boot = root / "boot/firmware"
    boot.mkdir(parents=True)
    for name in payload._KERNEL_AND_FIRMWARE:
        (boot / name).write_bytes(name.encode())
    (boot / "config.txt").write_text("[all]\narm_64bit=1\n")
    cmd = f"console=tty1 root=PARTUUID={uuid or A} rootwait"
    if which:
        cmd += f" rauc.slot={which} panic=10"
    (boot / "cmdline.txt").write_text(cmd + "\n")
    if which == "A":
        ctrl = root / "boot/redux-control"
        ctrl.mkdir()
        (ctrl / "autoboot.txt").write_text(
            "[all]\ntryboot_a_b=1\nboot_partition=2\n"
            "[tryboot]\nboot_partition=3\n")
        (ctrl / "config.txt").write_text("[all]\narm_64bit=1\n")
        (ctrl / "start.elf").write_bytes(b"selector firmware")
    return root


def pair(tmp_path):
    return slot(tmp_path, "A", A), slot(tmp_path, "B", B)


def verify_pair(a, b):
    return payload.verify(a, profile="ab", other_root=b,
                          a_root_partuuid=A, b_root_partuuid=B)


def test_lite_files_probed_not_physically_certified(tmp_path):
    r = payload.verify(slot(tmp_path))
    assert r["profile"] == "lite"
    assert r["hardware_boot_verified"] is False
    assert len(r["boot_files"]) == len(payload._KERNEL_AND_FIRMWARE)


@pytest.mark.parametrize("filename", payload._KERNEL_AND_FIRMWARE)
def test_lite_missing_pi4_pi5_file_is_rejected(tmp_path, filename):
    root = slot(tmp_path)
    (root / "boot/firmware" / filename).unlink()
    with pytest.raises(ValueError, match="missing"):
        payload.verify(root)


def test_empty_linked_and_wrong_arm64_payload_rejected(tmp_path):
    root = slot(tmp_path)
    p = root / "boot/firmware/kernel8.img"
    p.write_bytes(b"")
    with pytest.raises(ValueError, match="empty"):
        payload.verify(root)
    p.unlink()
    p.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="linked"):
        payload.verify(root)
    p.unlink()
    p.write_bytes(b"kernel")
    (root / "boot/firmware/config.txt").write_text("arm_64bit=0\n")
    with pytest.raises(ValueError, match="ARM64"):
        payload.verify(root)


@pytest.mark.parametrize("cmd", [
    "console=tty1",
    f"root=PARTUUID={A} root=/dev/sda1",
    f"root=PARTUUID={A} rauc.slot=A",
    f"root=PARTUUID={A}\nrootwait",
    "root=anything",
])
def test_wrong_lite_cmdline_fails(tmp_path, cmd):
    root = slot(tmp_path)
    (root / "boot/firmware/cmdline.txt").write_text(cmd)
    with pytest.raises(ValueError):
        payload.verify(root)


def test_ab_slot_pair_verified_independently(tmp_path):
    a, b = pair(tmp_path)
    report = verify_pair(a, b)
    assert report["profile"] == "ab"
    assert report["slots"] == ["A", "B"]
    assert report["hardware_boot_verified"] is False


@pytest.mark.parametrize("where,name", [
    ("A", "kernel8.img"), ("B", "kernel_2712.img"),
    ("B", "bcm2712-rpi-5-b.dtb"), ("A", "start4.elf"),
])
def test_ab_missing_payload_blocks_export(tmp_path, where, name):
    a, b = pair(tmp_path)
    p = (a if where == "A" else b) / "boot/firmware" / name
    p.unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_pair(a, b)


@pytest.mark.parametrize("bad", ["root", "slot", "resizer", "panic"])
def test_ab_rejects_wrong_cmdline(tmp_path, bad):
    a, b = pair(tmp_path)
    cmd = f"root=PARTUUID={B} rauc.slot=B panic=10 console=tty1 rootwait"
    if bad == "root":
        cmd = cmd.replace(B, A)
    elif bad == "slot":
        cmd = cmd.replace("rauc.slot=B", "rauc.slot=A")
    elif bad == "resizer":
        cmd += " init=/usr/lib/raspi-config/init_resize.sh"
    else:
        cmd = cmd.replace("panic=10 ", "")
    (b / "boot/firmware/cmdline.txt").write_text(cmd + "\n")
    with pytest.raises(ValueError):
        verify_pair(a, b)


@pytest.mark.parametrize("failure", ["selector", "config", "firmware", "extra_token"])
def test_ab_rejects_selector_or_boot_drift(tmp_path, failure):
    a, b = pair(tmp_path)
    if failure == "selector":
        (a / "boot/redux-control/autoboot.txt").write_text("bad\n")
    elif failure == "config":
        (b / "boot/firmware/config.txt").write_text("arm_64bit=1\n#drift\n")
    elif failure == "firmware":
        (b / "boot/firmware/start4.elf").write_bytes(b"different")
    else:
        p = b / "boot/firmware/cmdline.txt"
        p.write_text(p.read_text().strip() + " debug\n")
    with pytest.raises(ValueError):
        verify_pair(a, b)


def test_ab_requires_real_distinct_uuid_and_mounted_other_slot(tmp_path):
    a, b = pair(tmp_path)
    with pytest.raises(ValueError):
        payload.verify(a, profile="ab", other_root=b,
                       a_root_partuuid=A, b_root_partuuid=A)
    with pytest.raises(ValueError, match="both mounted"):
        payload.verify(a, profile="ab", a_root_partuuid=A, b_root_partuuid=B)


def test_lite_hook_stops_export_before_unmount(tmp_path):
    tree = tmp_path / "pi-gen"
    stage = tree / "export-image"
    finalise = stage / "05-finalise/01-run.sh"
    finalise.parent.mkdir(parents=True)
    finalise.write_text('update-initramfs\nunmount "${ROOTFS_DIR}"\n')
    cmd = [sys.executable, str(ROOT / "image/prepare_boot_gate.py"),
           str(ROOT / "image"), str(tree), "--profile", "lite"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    script = finalise.read_text()
    assert script.index("redux-boot-payload.py") < script.index('unmount "${ROOTFS_DIR}"')
    assert (stage / "redux-boot-payload.py").exists()
    assert subprocess.run(cmd, capture_output=True).returncode != 0


def test_ab_hook_requires_live_two_slot_clone(tmp_path):
    tree = tmp_path / "pi-gen"
    stage = tree / "export-image"
    finalise = stage / "05-finalise/01-run.sh"
    finalise.parent.mkdir(parents=True)
    finalise.write_text('source "${STAGE_DIR}/redux-ota-clone.sh"\nunmount "${ROOTFS_DIR}"\n')
    clone = stage / "redux-ota-clone.sh"
    clone.write_text("echo no-validation\n")
    cmd = [sys.executable, str(ROOT / "image/prepare_boot_gate.py"),
           str(ROOT / "image"), str(tree), "--profile", "ab"]
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    clone.write_text('python3 "${STAGE_DIR}/redux-boot-payload.py" --profile ab\n')
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (stage / "redux-boot-payload.py").exists()
    assert "redux-boot-payload.py" not in finalise.read_text()


def test_build_wires_both_operator_profiles():
    build = (ROOT / "build.sh").read_text()
    clone = (ROOT / "image/ota-clone.sh").read_text()
    assert "image/prepare_boot_gate.py" in build
    assert "BOOT_GATE_PROFILE=lite" in build and "BOOT_GATE_PROFILE=ab" in build
    assert "--a-root-partuuid" in clone and "--b-root-partuuid" in clone
    assert "redux-boot-payload.py" in clone
