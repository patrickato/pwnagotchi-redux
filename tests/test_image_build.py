"""Exercise image preparation/staging without APT, mounts or Pi hardware.

Git is replaced by a local fixture, so no test downloads upstream sources.
The real shell scripts run and we inspect the resulting pi-gen tree/rootfs.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Linux image scripts")


def invoke(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=20, **kwargs)


@pytest.fixture
def prepared(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("build.sh", "image", "redux"):
        source = REPO / name
        if source.is_dir():
            shutil.copytree(source, repo / name)
        else:
            shutil.copy2(source, repo / name)
    shim = tmp_path / "bin"
    shim.mkdir()
    git = shim / "git"
    git.write_text('''#!/usr/bin/env bash
set -eu
if [[ $1 == init ]]; then mkdir -p "$3"; exit; fi
[[ $1 == -C ]] || exit 3
dir=$2; shift 2
case $1 in
  fetch) printf '%s' "${@: -1}" > "$dir/revision" ;;
  checkout)
    if [[ $dir == */pi-gen ]]; then
      mkdir -p "$dir/stage0/02-firmware" "$dir/stage2"
      echo 'exit 91' > "$dir/build.sh"
    fi ;;
  rev-parse) if [[ -f $dir/revision ]]; then cat "$dir/revision"; else echo test-redux-revision; fi ;;
  status) if [[ -f $dir/dirty ]]; then echo '?? redux/new.py'; fi ;;
  remote|sparse-checkout) ;;
  *) exit 4 ;;
esac
''')
    git.chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
    result = invoke(["bash", str(repo / "build.sh"), "--prepare-only"], env=env)
    assert result.returncode == 0, result.stderr
    assert "no image has been built" in result.stdout
    return repo, env


def test_preparation_exports_only_redux_and_targets_pi4(prepared):
    repo, env = prepared
    tree = repo / "build/image/pi-gen"
    assert (tree / "stage2/SKIP_IMAGES").exists()
    assert (tree / "stage-redux/EXPORT_IMAGE").exists()
    assert (tree / "stage-redux/00-redux/files/redux/core/boot.py").exists()
    config = invoke(["bash", "-c", 'source "$1"; printf "%s|%s|%s" "$RELEASE" "$STAGE_LIST" "$ENABLE_SSH"', "bash", str(tree / "config")], env=env)
    assert config.stdout == "bookworm|stage0 stage1 stage2 stage-redux|0"
    country = invoke(["bash", "-c", 'source "$1"; [[ ! -v WPA_COUNTRY ]]', "bash", str(tree / "config")], env=env)
    assert country.returncode == 0  # Blank-but-set makes upstream raspi-config fail.
    kernel_packages = (tree / "stage0/02-firmware/01-packages").read_text().splitlines()
    assert kernel_packages == ["initramfs-tools", "raspi-firmware", "linux-image-rpi-v8", "linux-image-rpi-2712", "linux-headers-rpi-v8", "linux-headers-rpi-2712"]
    # A rerun must not modify a tree containing mounts or valuable build output.
    repeated = invoke(["bash", str(repo / "build.sh"), "--prepare-only"], env=env)
    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr


def test_stage_installs_service_and_source_before_chroot(prepared, tmp_path):
    repo, env = prepared
    stage = repo / "build/image/pi-gen/stage-redux/00-redux"
    root = tmp_path / "rootfs"
    (root / "boot/firmware").mkdir(parents=True)
    (root / "boot/firmware/config.txt").write_text("[all]\n")
    env = dict(env, ROOTFS_DIR=str(root), CHROOT_INPUT=str(tmp_path / "chroot-input"))
    result = invoke(["bash", "-c", '''
on_chroot() {
    test -f "$ROOTFS_DIR/opt/redux/redux/core/boot.py" || return 9
    test -f "$ROOTFS_DIR/usr/local/src/install-nexmon.sh" || return 9
    cat > "$CHROOT_INPUT"
}
export -f on_chroot
bash ./00-run.sh
'''], cwd=stage, env=env)
    assert result.returncode == 0, result.stderr
    unit = (root / "etc/systemd/system/redux.service").read_text()
    assert "ExecStart=/usr/bin/python3 -m redux.core.boot" in unit
    assert "User=redux" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "arm_64bit=1" in (root / "boot/firmware/config.txt").read_text()
    chroot = Path(env["CHROOT_INPUT"]).read_text()
    assert "systemctl enable redux.service" in chroot
    assert "systemctl mask bettercap.service" in chroot


def test_untracked_runtime_code_marks_image_provenance_dirty(prepared, tmp_path):
    repo, env = prepared
    (repo / "dirty").touch()
    (repo / "redux/new.py").write_text("# Local source fixture, not shipped telemetry.\n")
    build = tmp_path / "dirty-build"
    env = dict(env, REDUX_BUILD_DIR=str(build))
    result = invoke(["bash", str(repo / "build.sh"), "--prepare-only"], env=env)
    assert result.returncode == 0, result.stderr
    payload = build / "pi-gen/stage-redux/00-redux/files"
    assert (payload / "redux/new.py").exists()
    assert (payload / "redux-revision").read_text().splitlines() == [
        "test-redux-revision", "dirty working tree",
    ]


def test_prerun_copies_lite_root_only_when_missing(tmp_path):
    root = tmp_path / "root"
    marker = tmp_path / "copied"
    env = dict(os.environ, ROOTFS_DIR=str(root), COPY_MARKER=str(marker))
    args = ["bash", "-c", 'copy_previous() { touch "$COPY_MARKER"; }; export -f copy_previous; bash "$1"', "bash", str(REPO / "image/stage-redux/prerun.sh")]
    assert invoke(args, env=env).returncode == 0
    assert marker.exists()
    marker.unlink()
    root.mkdir()
    assert invoke(args, env=env).returncode == 0
    assert not marker.exists()


@pytest.mark.parametrize("kernel,driver_present,headers_present,reason", [
    ("6.12.75+rpt-rpi-v8", True, True, None),
    ("6.12.75+rpt-rpi-2712", True, True, None),
    ("6.18.40+rpt-rpi-v8", True, True, None),
    ("6.12.75+rpt-rpi-v7l", True, True, "non-arm64"),
    ("6.18.40-microsoft-standard-WSL2", True, True, "non-arm64"),
    ("6.12.75+rpt-rpi-v8", False, True, "driver or headers"),
    ("6.12.75+rpt-rpi-2712", True, False, "driver or headers"),
])
def test_nexmon_selects_installed_target_and_refuses_incompatible_pair(tmp_path, kernel, driver_present, headers_present, reason):
    source = tmp_path / "nexmon"
    modules = tmp_path / "modules" / kernel
    series = ".".join(kernel.split(".")[:2])
    expected_driver = source / f"patches/driver/brcmfmac_{series}.y-nexmon"
    if driver_present:
        expected_driver.mkdir(parents=True)
    if headers_present:
        (modules / "build").mkdir(parents=True)
    selector = REPO / "image/stage-redux/00-redux/files/target-kernel.sh"
    result = invoke(["bash", "-c", 'source "$1"; nexmon_driver_for "$2" "$3" "$4"', "bash", str(selector), kernel, str(source), str(modules)])
    if reason is None:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(expected_driver)
    else:
        assert result.returncode != 0
        assert reason in result.stderr
        assert not result.stdout


def test_kernel_hold_filters_dpkg_known_but_uninstalled_packages():
    selector = REPO / "image/stage-redux/00-redux/files/target-kernel.sh"
    result = invoke(["bash", "-c", '''
source "$1"
printf '%s\t%s\n' linux-image-rpi-v7l installed \
    linux-image-target installed linux-image-target-unsigned not-installed \
    linux-image-old config-files | nexmon_installed_kernel_packages
''', "bash", str(selector)])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["linux-image-rpi-v7l", "linux-image-target"]
