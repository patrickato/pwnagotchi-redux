"""First-boot probes are read-only and conservatively interpret real states."""
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from redux import boot_readiness as b


def mount(line):
    return b.parse_mountinfo(line)


MOUNTS = """21 1 0:18 / / rw,relatime - overlay overlay rw,lowerdir=/media/root-ro
22 21 179:4 / /media/root-ro ro,relatime - ext4 /dev/mmcblk0p4 ro
23 21 179:1 / /boot/firmware ro,relatime - vfat /dev/mmcblk0p1 ro
24 21 179:6 / /captures rw,nodev - ext4 /dev/mmcblk0p6 rw
"""


def service_good(unit, state):
    return b.finding("ok", unit + state)


def dashboard_good(port):
    assert port == 8080
    return {"ok": True, "reason": "fixture authenticated Redux Doctor"}


def fixtures(tmp_path, *, mounts=MOUNTS, board="Raspberry Pi 4 Model B Rev 1.4", manifests=True):
    mountfile = tmp_path / "mountinfo"
    mountfile.write_text(mounts)
    model = tmp_path / "model"
    model.write_text(board)
    index = tmp_path / "revision"
    index.mkdir()
    if manifests:
        for name in b._MANIFESTS:
            (index / name).write_text("fixture")
    label_dir = tmp_path / "by-label"
    label_dir.mkdir()
    label = label_dir / "REDUXCAP"
    label.symlink_to("/dev/mmcblk0p6")
    return {"mountinfo": mountfile, "model_file": model,
            "manifests": index, "label_path": label, "machine": "aarch64",
            "release": "fixture-rpi", "service": service_good,
            "dashboard": dashboard_good, "realpath": lambda src: str(label.readlink()) if str(src) == str(label) else src}


def test_mountinfo_parses_real_fields_not_substrings_and_unescapes():
    mounts = mount(MOUNTS + "25 21 0:30 / /hello\\040world rw - tmpfs tmpfs rw\n")
    assert mounts["/captures"]["filesystem"] == "ext4"
    assert mounts["/captures"]["source"] == "/dev/mmcblk0p6"
    assert "/hello world" in mounts
    assert mounts["/boot/firmware"]["options"] == ["ro", "relatime"]
    assert mount("garbage with /captures in it") == {}


def test_mountinfo_stacked_mounts_favor_last_topmost():
    contents = MOUNTS + "35 21 0:9 / /captures rw - tmpfs tmpfs rw\n"
    assert mount(contents)["/captures"]["filesystem"] == "tmpfs"


def test_missing_capture_mount_is_action_not_healthy(tmp_path):
    opts = fixtures(tmp_path, mounts=MOUNTS.replace("24 21 179:6 / /captures rw,nodev - ext4 /dev/mmcblk0p6 rw\n", ""))
    report = b.snapshot(**opts)
    assert report["overall"] == "action"
    assert report["checks"]["captures"]["status"] == "action"
    assert not report["physical_pi_qualified"]


@pytest.mark.parametrize("edited", [
    MOUNTS.replace("/captures rw,nodev", "/captures ro,nodev"),
    MOUNTS.replace("/captures rw,nodev - ext4", "/captures rw,nodev - tmpfs"),
    MOUNTS + "35 21 0:9 / /captures rw - tmpfs tmpfs rw\n",
])
def test_readonly_tmpfs_and_overmounted_capture_fail(tmp_path, edited):
    report = b.snapshot(**fixtures(tmp_path, mounts=edited))
    assert report["checks"]["captures"]["status"] == "action"


def test_mismatched_label_device_refused(tmp_path):
    opts = fixtures(tmp_path)
    opts["label_path"].unlink()
    opts["label_path"].symlink_to("/dev/mmcblk0p5")
    report = b.snapshot(**opts)
    assert report["checks"]["captures"]["status"] == "action"
    assert "REDUXCAP" in report["checks"]["captures"]["detail"]


def test_label_missing_is_not_marked_proven(tmp_path):
    opts = fixtures(tmp_path)
    opts["label_path"].unlink()
    report = b.snapshot(**opts)
    assert report["checks"]["captures"]["status"] == "unknown"
    assert report["overall"] == "unverified"


def test_correct_dev_image_is_observed_but_never_physical_certified(tmp_path):
    report = b.snapshot(**fixtures(tmp_path))
    assert report["overall"] == "software_checks_ok"
    assert report["counts"] == {"ok": 12, "action": 0, "unknown": 0}
    assert report["checks"]["captures"]["status"] == "ok"
    assert report["checks"]["root_lower"]["status"] == "ok"
    assert report["physical_pi_qualified"] is False
    assert report["image_boot_and_radio_qualified"] is False


def test_boot_partition_rw_refused(tmp_path):
    edited = MOUNTS.replace("/boot/firmware ro,relatime", "/boot/firmware rw,relatime")
    report = b.snapshot(**fixtures(tmp_path, mounts=edited))
    assert report["checks"]["boot_firmware"]["status"] == "action"


def test_root_without_overlay_refused(tmp_path):
    edited = MOUNTS.replace("/ / rw,relatime - overlay overlay", "/ / rw,relatime - ext4 /dev/mmcblk0p4")
    report = b.snapshot(**fixtures(tmp_path, mounts=edited))
    assert report["checks"]["root"]["status"] == "action"


@pytest.mark.parametrize("board", ["Raspberry Pi 5 Model B", "Raspberry Pi 4 Model B Rev 1.2"])
def test_supported_pi_models_are_reported_as_observed_only(tmp_path, board):
    report = b.snapshot(**fixtures(tmp_path, board=board))
    assert report["checks"]["board"]["status"] == "ok"


def test_unknown_board_or_architecture_not_accepted(tmp_path):
    opts = fixtures(tmp_path, board="Raspberry Pi 3 Model B")
    opts["machine"] = "armv7l"
    report = b.snapshot(**opts)
    assert report["checks"]["board"]["status"] == "action"
    assert report["checks"]["architecture"]["status"] == "action"


def test_missing_manifest_does_not_claim_firmware_hash_verified(tmp_path):
    opts = fixtures(tmp_path, manifests=False)
    report = b.snapshot(**opts)
    assert report["checks"]["image_manifest"]["status"] == "action"
    assert "hash-verified" not in report["checks"]["image_manifest"]["detail"]


def test_unreadable_proc_mountinfo_is_unknown_not_success(tmp_path):
    opts = fixtures(tmp_path)
    opts["mountinfo"] = tmp_path / "does-not-exist"
    report = b.snapshot(**opts)
    assert all(report["checks"][key]["status"] == "unknown"
               for key in ("root", "root_lower", "captures", "boot_firmware"))


def test_dashboard_absence_never_claims_capture(tmp_path):
    opts = fixtures(tmp_path)
    opts["dashboard"] = lambda _port: {"ok": False, "reason": "connection refused"}
    report = b.snapshot(**opts)
    assert report["overall"] == "unverified"
    assert report["checks"]["dashboard"]["status"] == "unknown"


@pytest.mark.parametrize("actual,expect,should_pass", [
    (("loaded", "active", "enabled"), "active", True),
    (("loaded", "inactive", "enabled"), "active", False),
    (("not-found", "inactive", "disabled"), "active", False),
    (("masked", "inactive", "masked"), "masked", True),
    (("loaded", "active", "masked"), "masked", False),
    (("loaded", "inactive", "disabled"), "masked", False),
])
def test_service_parses_real_systemd_states(actual, expect, should_pass):
    load, active, unitfile = actual
    unit = "bettercap.service" if expect == "masked" else "redux-live.service"
    calls = []
    def runner(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 0,
            f"LoadState={load}\nActiveState={active}\nUnitFileState={unitfile}\n", "")
    result = b.service_probe(unit, expect, runner=runner)
    assert result["status"] == ("ok" if should_pass else "action")
    assert calls[0][0][-1] == unit
    assert calls[0][1]["timeout"] == 3
    assert calls[0][1]["capture_output"] is True


def test_systemctl_unavailable_cannot_be_reported_as_healthy():
    def unavailable(*a, **k):
        raise FileNotFoundError("systemctl")
    result = b.service_probe("redux.service", "active", runner=unavailable)
    assert result["status"] == "unknown"
    assert b.service_probe("redux.service", "active",
        runner=lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""))["status"] == "unknown"
    with pytest.raises(ValueError):
        b.service_probe("ssh.service", "active")


def test_cli_exit_codes_and_strict_unknown(monkeypatch, capsys):
    for overall, code in (("software_checks_ok", 0), ("unverified", 3), ("action", 2)):
        monkeypatch.setattr(b, "snapshot", lambda: {"counts": {
            "action": int(overall == "action"), "unknown": int(overall == "unverified")}})
        assert b.main(["--strict"]) == code
        assert '"counts"' in capsys.readouterr().out
    monkeypatch.setattr(b, "snapshot", lambda: {"counts": {"action": 0, "unknown": 1}})
    assert b.main([]) == 0


def test_oversized_kernel_mount_inventory_cannot_validate_partial_mounts(tmp_path):
    opts = fixtures(tmp_path)
    opts["mountinfo"].write_text(MOUNTS + "#" * (512 * 1024))
    report = b.snapshot(**opts)
    assert report["checks"]["captures"]["status"] == "unknown"
    assert report["overall"] == "unverified"

def test_image_stages_operator_launcher_without_automatic_enable():
    stage = (Path(__file__).resolve().parents[1] /
             "image/stage-redux/00-redux/00-run.sh").read_text()
    assert "redux-boot-check" in stage
    assert 'exec /usr/bin/python3 -m redux.boot_readiness "$@"' in stage
    assert "systemctl enable redux-boot-check" not in stage
