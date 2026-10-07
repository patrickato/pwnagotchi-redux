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


def boot_module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, REPO / "boot" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_power_sysfs_real_units_and_absent_supply(tmp_path):
    power = boot_module("power")
    for name, value in {"type": "Battery", "capacity": "12", "status": "Discharging", "voltage_now": "3650000"}.items():
        (tmp_path / name).write_text(value)
    reading = power.read_sysfs(tmp_path)
    assert reading.percent == 12 and reading.voltage_v == 3.65
    assert reading.status == "Discharging"
    (tmp_path / "present").write_text("0")
    assert power.sample({"supply": str(tmp_path)}).percent is None
    (tmp_path / "present").unlink()
    (tmp_path / "capacity").write_text("nan")
    assert power.sample({"supply": str(tmp_path)}).status == "Unknown"


def test_geekworm_gauge_protocol_never_infers_charging():
    power = boot_module("power")
    class Bus:
        def read_word_data(self, address, register):
            assert address == 0x36
            # Actual gauge order: 0xbe00 = 3.8V, 0x0c80 = 12.5%.
            return {2: 0x00be, 4: 0x800c}[register]
    reading = power.read_geekworm(Bus())
    assert reading.percent == 12.5
    assert reading.voltage_v == pytest.approx(3.8)
    assert reading.status == "Unknown"
    assert power.read_geekworm(Bus(), False).status == "Discharging"
    assert power.read_geekworm(Bus(), True).status == "Not charging"


def test_low_battery_requires_uninterrupted_confirmed_discharge():
    power = boot_module("power")
    low = power.Reading(5, 3.3, "Discharging", "fixture", "measured fixture")
    policy = power.LowBattery(10, 30)
    assert not policy.decision(low, 0)[0]
    assert not policy.decision(low, 29)[0]
    assert policy.decision(low, 30)[0]
    unknown = power.Reading(None, None, "Unknown", "fixture", "missing fixture")
    assert not policy.decision(unknown, 31)[0]
    assert not policy.decision(low, 60)[0]
    charging = power.Reading(5, 3.3, "Charging", "fixture", "measured fixture")
    assert not policy.decision(charging, 90)[0]
    assert not policy.decision(low, 100)[0]
    high = power.Reading(11, 3.6, "Discharging", "fixture", "measured fixture")
    assert not policy.decision(high, 130)[0]
    assert not policy.decision(low, 160)[0]
    assert not policy.decision(low, 159)[0]  # Clock reversal resets confirmation.


@pytest.mark.parametrize("threshold,interval", [(0,30),(100,30),(10,0),(10,float("nan"))])
def test_low_battery_invalid_policy(threshold, interval):
    with pytest.raises(ValueError):
        boot_module("power").LowBattery(threshold, interval)


def test_poweroff_commits_reason_before_sync_and_systemd(monkeypatch, tmp_path):
    power = boot_module("power")
    events = []
    original = power.atomic_checkpoint
    def checkpoint(path, record):
        events.append("checkpoint")
        original(tmp_path / "shutdown.json", record)
    monkeypatch.setattr(power, "atomic_checkpoint", checkpoint)
    def run(argv, **kwargs):
        assert argv == ["systemctl", "poweroff", "--no-block"]
        assert kwargs["check"] and kwargs["timeout"] == 15
        events.append("systemd")
    power.shutdown("measured low capacity", run=run, sync=lambda: events.append("sync"))
    assert events == ["checkpoint", "sync", "systemd"]
    assert "measured low capacity" in (tmp_path / "shutdown.json").read_text()


def test_tft_real_state_and_bounds():
    pytest.importorskip("PIL")
    power = boot_module("power")
    tft = boot_module("battery_tft")
    unknown = tft.panel(power.Reading(None,None,"Unknown","fixture","unavailable"))
    known = tft.panel(power.Reading(50,3.8,"Discharging","fixture","measured"))
    assert unknown.tobytes() != known.tobytes()
    variable = [480,320,480,320,0,0,16,0,11,5,0,5,6,0,0,5,0] + [0]*23
    variable[22:24] = [49, 74]  # Physical millimetres are not nonstandard format flags.
    fixed = tft.Fixed(line_length=960, smem_len=960*320, type=0, visual=2)
    rows = tft.rows(known, variable, fixed)
    assert len(rows) == 48 and all(len(data) == 320 for _, data in rows)
    assert rows[-1][0] == 47*960
    variable[6] = 24
    with pytest.raises(ValueError, match="16/32"):
        tft.rows(known, variable, fixed)
    variable[6] = 16
    fixed.smem_len = 40
    with pytest.raises(ValueError, match="exceeds"):
        tft.rows(known, variable, fixed)


@pytest.mark.parametrize("stable,candidate", [("A","B"),("B","A")])
def test_ota_trial_commit_and_rollback(stable, candidate):
    ota = boot_module("ota")
    initial = ota.initial_state(stable)
    staged, commit = ota.transition(initial, stable, stable, "set-primary", candidate)
    assert commit is None and staged["pending"] == candidate
    assert initial["pending"] is None  # Pure transition never mutates prior record.
    with pytest.raises(ValueError, match="observed trial"):
        ota.transition(staged, stable, candidate, "set-state", candidate, "good", trial=False)
    healthy, commit = ota.transition(staged, stable, candidate, "set-state", candidate, "good", trial=True)
    assert commit == candidate and healthy["pending"] is None
    reverted, commit = ota.transition(staged, stable, stable, "set-state", stable, "good")
    assert reverted["states"][candidate] == "bad" and reverted["pending"] is None
    assert "rollback or trial abandoned" in reverted["reason"]
    recovered, commit = ota.transition(staged, candidate, candidate, "set-state", candidate, "good")
    assert recovered["states"][candidate] == "good" and "interrupted" in recovered["reason"]


def test_ota_refuses_active_slot_and_lost_fallback():
    ota = boot_module("ota")
    state = ota.initial_state("A")
    for action, slot, value in [("set-primary","A",None),("set-state","A","bad"),("set-primary","C",None)]:
        with pytest.raises(ValueError):
            ota.transition(state,"A","A",action,slot,value)
    staged,_ = ota.transition(state,"A","A","set-primary","B")
    with pytest.raises(ValueError):
        ota.transition(staged,"A","A","set-primary","B")


def test_ota_cmdline_and_selector_strictness():
    ota = boot_module("ota")
    text = "console=tty1 root=PARTUUID=old rauc.slot=A init=/usr/lib/raspi-config/init_resize.sh quiet"
    rewritten = ota.patch_cmdline(text,"B","01234567-89ab-cdef-0123-456789abcdef")
    assert "init=" not in rewritten and "root=PARTUUID=old" not in rewritten
    assert "panic=10" in rewritten and ota.slot_from_cmdline(rewritten) == "B"
    for slot in ("A","B"):
        assert ota.stable_from_autoboot(ota.autoboot(slot)) == slot
    with pytest.raises(ValueError):
        ota.stable_from_autoboot(ota.autoboot("A") + "# operator customization\n")
    with pytest.raises(ValueError):
        ota.slot_from_cmdline("rauc.slot=A rauc.slot=B")


def test_ota_topology_and_generated_mounts(monkeypatch, tmp_path):
    ota = boot_module("ota")
    labels = {1:"REDUXCTRL",2:"REDUXBOOTA",3:"REDUXBOOTB",4:"REDUXROOTA",5:"REDUXROOTB",6:"REDUXCAP"}
    parts = {n:{"label":label,"uuid":f"00000000-0000-0000-0000-{n:012d}"} for n,label in labels.items()}
    devices = ota.validate_topology(parts,"B",parts[5]["uuid"])
    context = {"current":"B","devices":devices}
    conf = ota.system_conf(context)
    assert "parent=rootfs.1" in conf and f"device={devices['5']}" in conf
    monkeypatch.setattr(ota,"RUNTIME",tmp_path / "runtime")
    ota.generate(tmp_path / "generator",context)
    firmware = (tmp_path / "generator/boot-firmware.mount").read_text()
    assert devices["3"] in firmware and "Options=ro" in firmware
    with pytest.raises(ValueError, match="root does not match"):
        ota.validate_topology(parts,"B",parts[4]["uuid"])
    parts[6]["label"] = "someone-elses-data"
    with pytest.raises(ValueError, match="label"):
        ota.validate_topology(parts,"B",parts[5]["uuid"])


def test_ota_manifest_requires_complete_signed_pair():
    ota = boot_module("ota")
    manifest = f"[update]\ncompatible={ota.COMPATIBLE}\n[bundle]\nformat=verity\n[image.boot]\nfilename=boot.tar\nhooks=post-install\n[image.rootfs]\nfilename=rootfs.tar\n"
    ota.validate_manifest(manifest)
    for bad in [manifest.replace("[image.rootfs]","[image.captures]"),manifest.replace("hooks=post-install","hooks="),manifest.replace("format=verity","format=plain")]:
        with pytest.raises(ValueError):
            ota.validate_manifest(bad)


def test_ota_corrupt_checkpoint_cannot_change_selector(tmp_path):
    ota = boot_module("ota")
    path = tmp_path / "state.json"
    assert ota.load_state(path,"A")["pending"] is None
    path.write_text('{"schema":1,"pending":[],"states":{"A":"good","B":"bad"}}')
    with pytest.raises(ValueError, match="invalid OTA state"):
        ota.load_state(path,"A")


def test_ota_interrupted_health_commit_keeps_old_checkpoint(monkeypatch,tmp_path):
    ota = boot_module("ota")
    state, _ = ota.transition(ota.initial_state("A"),"A","A","set-primary","B")
    path = tmp_path / "state.json"
    ota.atomic_checkpoint(path,state)
    monkeypatch.setattr(ota,"STATE",path)
    monkeypatch.setattr(ota,"RUNTIME",tmp_path / "runtime")
    control = tmp_path / "autoboot.txt"
    control.write_text(ota.autoboot("A"))
    monkeypatch.setattr(ota,"CONTROL",control)
    original = Path.read_text
    monkeypatch.setattr(Path,"read_text",lambda p,*a,**k: "rauc.slot=B" if str(p)=="/proc/cmdline" else original(p,*a,**k))
    monkeypatch.setattr(ota,"dt_integer",lambda n: {"tryboot":1,"partition":3}[n])
    monkeypatch.setenv("REDUX_HEALTH_COMMIT","1")
    def failed_commit(slot):
        raise OSError("simulated selector write interruption")
    monkeypatch.setattr(ota,"commit_selector",failed_commit)
    with pytest.raises(OSError):
        ota.backend(["set-state","B","good"])
    assert ota.load_state(path,"A") == state
    assert control.read_text() == ota.autoboot("A")


def test_ota_export_hook_is_after_initramfs_and_before_unmount(tmp_path):
    tree = tmp_path / "pi-gen"
    finalise = tree / "export-image/05-finalise/01-run.sh"
    finalise.parent.mkdir(parents=True)
    (tree / "export-image/04-set-partuuid").mkdir()
    finalise.write_text('update-initramfs -k all -c\nunmount "${ROOTFS_DIR}"\nzerofree "$ROOT_DEV"\n')
    result = invoke([sys.executable, str(REPO / "image/prepare_ota.py"), str(REPO / "image"), str(tree)])
    assert result.returncode == 0, result.stderr
    script = finalise.read_text()
    assert script.index("update-initramfs") < script.index("redux-ota-clone") < script.index("unmount")
    assert 'mklabel gpt' in (tree / "export-image/prerun.sh").read_text()
    assert 'p4' in (tree / "export-image/04-set-partuuid/00-run.sh").read_text()
    repeated = invoke([sys.executable, str(REPO / "image/prepare_ota.py"), str(REPO / "image"), str(tree)])
    # Reject duplicate injection instead of running a clone twice.
    assert repeated.returncode != 0


def test_ota_discovery_follows_the_root_disk_not_device_number(monkeypatch,tmp_path):
    ota = boot_module("ota")
    disk = tmp_path / "devices/nvme0n1"
    disk.mkdir(parents=True)
    sysfs = tmp_path / "sysfs"
    sysfs.mkdir()
    labels = ["REDUXCTRL","REDUXBOOTA","REDUXBOOTB","REDUXROOTA","REDUXROOTB","REDUXCAP"]
    uuids = {n:f"00000000-0000-0000-0000-{n:012d}" for n in range(1,7)}
    for n in range(1,7):
        node = disk / f"nvme0n1p{n}"
        node.mkdir()
        (node / "partition").write_text(str(n))
        (sysfs / node.name).symlink_to(node)
    def probe(argv):
        if "-t" in argv:
            return "/dev/nvme0n1p5"
        n = int(argv[-1][-1])
        return f"PART_ENTRY_UUID={uuids[n]}\nPART_ENTRY_NAME={labels[n-1]}"
    monkeypatch.setattr(ota,"command",probe)
    context = ota.discover(f"root=PARTUUID={uuids[5]} rauc.slot=B",sysfs)
    assert context["current"] == "B" and context["devices"]["5"].endswith(uuids[5])


def invoke(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, timeout=20, **kwargs)


@pytest.fixture
def prepared(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("build.sh", "image", "redux", "boot"):
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
      mkdir -p "$dir/export-image"
      cat > "$dir/export-image/prerun.sh" <<'LAYOUT'
IMG_SIZE=$((BOOT_PART_START + BOOT_PART_SIZE + ROOT_PART_SIZE))
echo "Creating loop device..."
ROOT_DEV="${LOOP_DEV}p2"
LAYOUT
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


def layout_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("layout", REPO / "image/export_layout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_captures_layout_adds_separate_partition_before_loop_attachment():
    source = 'IMG_SIZE=$((BOOT_PART_START + BOOT_PART_SIZE + ROOT_PART_SIZE))\necho "Creating loop device..."\nROOT_DEV="${LOOP_DEV}p2"\n'
    result = layout_module().captures_layout(source)
    assert result.index('mkpart primary ext4') < result.index('Creating loop device')
    assert 'CAPTURE_DEV="${LOOP_DEV}p3"' in result
    assert 'mkfs.ext4 -L REDUXCAP' in result
    assert 'chmod 0700' in result
    assert '512 * 1024 * 1024' in result
    with pytest.raises(ValueError, match="unsupported"):
        layout_module().captures_layout(result)


@pytest.mark.parametrize("size", [0, 63, 32769, "512"])
def test_capture_size_rejects_unbounded_or_invalid_inputs(size):
    with pytest.raises(ValueError, match="partition"):
        layout_module().captures_layout("", size)


def test_overlay_stage_preserves_capture_writes_and_hardens_service(tmp_path):
    root = tmp_path / "root"
    (root / "etc/systemd/system").mkdir(parents=True)
    (root / "etc/fstab").write_text('ROOTDEV / ext4 defaults 0 1\nBOOTDEV /boot/firmware vfat defaults 0 2\n')
    (root / "etc/systemd/system/redux.service").write_text('[Unit]\n[Service]\nStateDirectory=redux\n')
    stage = REPO / "image/stage-redux/10-overlay"
    result = invoke(["bash", "-c", 'on_chroot() { cat >/dev/null; }; export -f on_chroot; bash 00-run.sh'], cwd=stage, env=dict(os.environ, ROOTFS_DIR=str(root)))
    assert result.returncode == 0, result.stderr
    fstab = (root / "etc/fstab").read_text()
    assert 'LABEL=REDUXCAP /captures ext4 rw,' in fstab
    assert '/boot/firmware vfat ro,' in fstab
    assert 'recurse=0' in (root / "etc/overlayroot.conf").read_text()
    unit = (root / "etc/systemd/system/redux.service").read_text()
    assert 'RequiresMountsFor=/captures' in unit
    assert 'StateDirectory=' not in unit
    assert 'ReadWritePaths=/captures/redux' in unit


def boot_policy_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("boot_policy", REPO / "boot/boot_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flash_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('flash', REPO / 'image/flash.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flash_validates_compressed_release_and_full_readback(tmp_path):
    import hashlib
    import io
    import lzma
    flash = flash_module()
    raw = bytearray(4096)
    raw[510:512] = b'\x55\xaa'
    path = tmp_path / 'fixture.img.xz'
    path.write_bytes(lzma.compress(bytes(raw)))
    release_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    size, digest = flash.inspect_image(path, release_hash)
    assert size == 4096 and digest == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match='checksum'):
        flash.inspect_image(path, '0' * 64)

    class ShortWriter(io.BytesIO):
        def write(self, data):
            return super().write(data[:13])

    disk = ShortWriter()
    flash.copy_stream(io.BytesIO(raw), disk, size, digest)
    assert disk.getvalue() == raw
    flash.verify_stream(io.BytesIO(raw), size, digest)
    with pytest.raises(ValueError, match='truncated'):
        flash.verify_stream(io.BytesIO(raw[:-1]), size, digest)
    with pytest.raises(ValueError, match='mismatch'):
        flash.verify_stream(io.BytesIO(b'x' * size), size, digest)
    with pytest.raises(ValueError, match='changed'):
        flash.copy_stream(io.BytesIO(bytes(raw) + b'x'), io.BytesIO(), size, digest)


@pytest.mark.parametrize('change', [dict(rm=False), dict(ro=True), dict(type='part'), dict(size=512), dict(children=[dict(path='/dev/sdb1', mountpoints=['/media/card'])])])
def test_flash_refuses_unsafe_disks(change):
    disk = dict(path='/dev/sdb', type='disk', rm=True, ro=False, size=8192, mountpoints=[None])
    flash = flash_module()
    assert flash.select_disk({'blockdevices': [disk]}, '/dev/sdb', 4096) == disk
    disk.update(change)
    with pytest.raises(ValueError):
        flash.select_disk({'blockdevices': [disk]}, '/dev/sdb', 4096)


def test_trim_selects_real_storage_modules_and_builtin_support():
    policy = boot_policy_module()
    selected = policy.select_modules(['kernel/fs/ext4/ext4.ko', 'kernel/fs/overlayfs/overlay.ko', 'kernel/drivers/mmc/core/mmc_block.ko', 'kernel/drivers/nvme/host/nvme.ko.xz', 'kernel/drivers/net/ethernet/other.ko'])
    assert selected == ['ext4', 'overlay', 'mmc_block', 'nvme']
    with pytest.raises(ValueError, match="cannot trim"):
        policy.select_modules(['ext4.ko'])
    assert 'NetworkManager.service' not in policy.MASK_UNITS
    assert 'redux.service' not in policy.MASK_UNITS
    assert all(policy.MASK_UNITS.values())


def test_boot_budget_stage_uses_target_inventory_and_installs_actual_timing_tool(tmp_path):
    policy = boot_policy_module()
    root = tmp_path / 'root'
    for name in ('6.12.109+rpt-rpi-v8', '6.12.109+rpt-rpi-2712'):
        modules = root / 'lib/modules' / name
        modules.mkdir(parents=True)
        (modules / 'modules.builtin').write_text('kernel/fs/ext4/ext4.ko\nkernel/fs/overlayfs/overlay.ko\nkernel/drivers/mmc/core/mmc_block.ko\n')
        (modules / 'nvme.ko').touch()
    policy.write_policy(root)
    assert (root / 'etc/initramfs-tools/conf.d/redux-modules').read_text() == 'MODULES=list\n'
    assert 'nvme' in (root / 'etc/initramfs-tools/modules').read_text()
    manifest = (root / 'usr/share/redux/boot-policy.txt').read_text()
    assert 'rpt-rpi-2712' in manifest and 'rpt-rpi-v8' in manifest
    assert 'redux boot needs local storage, not an uplink' in manifest
