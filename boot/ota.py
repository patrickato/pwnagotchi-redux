"""RAUC custom backend for Raspberry Pi tryboot, with explicit health commit."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from redux.core.boot import atomic_checkpoint

SLOTS = {"A": {"boot": 2, "root": 4}, "B": {"boot": 3, "root": 5}}
COMPATIBLE = "pwnagotchi-redux-pi4-pi5-arm64-ab-v1"
RUNTIME = Path("/run/redux-ota")
CONTROL = Path("/boot/redux-control/autoboot.txt")
STATE = Path("/captures/rauc/boot-state.json")


def command(arguments):
    return subprocess.check_output(arguments, text=True, timeout=30).strip()


def slot_from_cmdline(text):
    slots = [word.split("=", 1)[1] for word in text.split() if word.startswith("rauc.slot=")]
    if len(slots) != 1 or slots[0] not in SLOTS:
        raise ValueError("exactly one valid rauc.slot=A/B is required")
    return slots[0]


def autoboot(stable):
    if stable not in SLOTS:
        raise ValueError("invalid stable slot")
    alternate = "B" if stable == "A" else "A"
    return (f"[all]\ntryboot_a_b=1\nboot_partition={SLOTS[stable]['boot']}\n"
            f"[tryboot]\nboot_partition={SLOTS[alternate]['boot']}\n")


def stable_from_autoboot(text):
    for slot in SLOTS:
        if text == autoboot(slot):
            return slot
    raise ValueError("unexpected boot selector; refusing to rewrite operator configuration")


def patch_cmdline(text, slot, root_uuid):
    if slot not in SLOTS or not re.fullmatch(r"[a-fA-F0-9-]{8,36}", root_uuid):
        raise ValueError("invalid slot/root partition UUID")
    words = [w for w in text.split() if not w.startswith(("root=", "rauc.slot=", "init="))]
    # The stock resize init rewrites partition tables and cannot run on an A/B disk.
    words += [f"root=PARTUUID={root_uuid}", f"rauc.slot={slot}", "panic=10"]
    return " ".join(dict.fromkeys(words)) + "\n"


def validate_topology(parts, current, root_uuid):
    if set(parts) != set(range(1, 7)):
        raise ValueError("A/B disk must contain exactly six GPT partitions")
    if parts[SLOTS[current]["root"]]["uuid"].lower() != root_uuid.lower():
        raise ValueError("command-line root does not match boot slot")
    expected = {1: "REDUXCTRL", 2: "REDUXBOOTA", 3: "REDUXBOOTB",
                4: "REDUXROOTA", 5: "REDUXROOTB", 6: "REDUXCAP"}
    uuids = set()
    for number, info in parts.items():
        if info["label"] != expected[number] or not re.fullmatch(r"[a-fA-F0-9-]{36}", info["uuid"]):
            raise ValueError("unexpected GPT partition label/UUID")
        if info["uuid"].lower() in uuids:
            raise ValueError("partition UUIDs must be unique")
        uuids.add(info["uuid"].lower())
    return {str(n): f"/dev/disk/by-partuuid/{info['uuid'].lower()}" for n, info in parts.items()}


def discover(cmdline, sysfs=Path("/sys/class/block"), device_dir=Path("/dev")):
    current = slot_from_cmdline(cmdline)
    roots = [w[14:] for w in cmdline.split() if w.startswith("root=PARTUUID=")]
    if len(roots) != 1:
        raise ValueError("A/B root must have one PARTUUID")
    root_uuid = roots[0]
    root_dev = Path(command(["blkid", "-t", f"PARTUUID={root_uuid}", "-o", "device"]))
    if not root_dev.name or not (sysfs / root_dev.name / "partition").exists():
        raise ValueError("root is not a physical disk partition")
    parent = (sysfs / root_dev.name).resolve().parent
    parts = {}
    for entry in sysfs.iterdir():
        if not (entry / "partition").exists() or entry.resolve().parent != parent:
            continue
        number = int((entry / "partition").read_text())
        metadata = dict(line.split("=", 1) for line in command(["blkid", "-p", "-o", "export", str(device_dir / entry.name)]).splitlines() if "=" in line)
        parts[number] = {"uuid": metadata.get("PART_ENTRY_UUID", ""), "label": metadata.get("PART_ENTRY_NAME", "")}
    return {"current": current, "devices": validate_topology(parts, current, root_uuid)}


def system_conf(context):
    devices = context["devices"]
    config = (f"[system]\ncompatible={COMPATIBLE}\nbootloader=custom\n"
              "bundle-formats=verity\nstatusfile=/captures/rauc/status.raucs\n"
              "mountprefix=/run/rauc\n\n[keyring]\npath=/etc/rauc/keyring.pem\n\n"
              "[handlers]\nbootloader-custom-backend=/usr/local/libexec/redux-rauc-backend\n")
    config += "pre-install=/usr/local/libexec/redux-rauc-preinstall\n"
    for index, (slot, numbers) in enumerate(SLOTS.items()):
        config += (f"\n[slot.rootfs.{index}]\ndevice={devices[str(numbers['root'])]}\n"
                   f"type=ext4\nbootname={slot}\n"
                   f"\n[slot.boot.{index}]\ndevice={devices[str(numbers['boot'])]}\n"
                   f"type=vfat\nparent=rootfs.{index}\n")
    return config


def generate(destination, context):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    atomic_checkpoint(RUNTIME / "context.json", context)
    (RUNTIME / "system.conf").write_text(system_conf(context))
    for name, point, number, kind, options in [
            ("boot-firmware.mount", "/boot/firmware", SLOTS[context["current"]]["boot"], "vfat", "ro,nodev,nosuid"),
            ("boot-redux\\x2dcontrol.mount", "/boot/redux-control", 1, "vfat", "ro,nodev,nosuid"),
            ("captures.mount", "/captures", 6, "ext4", "rw,nodev,nosuid,noatime,errors=remount-ro")]:
        (destination / name).write_text(
            f"[Unit]\nBefore=local-fs.target\n\n[Mount]\nWhat={context['devices'][str(number)]}\n"
            f"Where={point}\nType={kind}\nOptions={options}\n")
        wants = destination / "local-fs.target.requires"
        wants.mkdir(exist_ok=True)
        (wants / name).symlink_to("../" + name)


def initial_state(stable):
    return {"schema": 1, "pending": None, "states": {stable: "good", ("B" if stable == "A" else "A"): "bad"},
            "reason": "factory boot: stable selector is authoritative"}


def load_state(path, stable):
    if not path.exists():
        return initial_state(stable)
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
            or type(data.get("pending")) not in {str, type(None)} or not isinstance(data.get("states"), dict)
            or data.get("pending") not in {None, "A", "B"}
            or set(data.get("states", {})) != set(SLOTS)
            or any(v not in {"good", "bad"} for v in data["states"].values())):
        raise ValueError("invalid OTA state; stable firmware selector remains unchanged")
    return data


def transition(state, stable, current, action, slot=None, value=None, trial=False):
    state = json.loads(json.dumps(state))
    if action in {"set-primary", "set-state"} and slot not in SLOTS:
        raise ValueError("unknown boot slot")
    if action == "set-primary":
        if current != stable or slot == current or state["pending"] is not None:
            raise ValueError("only the inactive slot can be staged from a committed boot")
        state["pending"] = slot
        state["states"][slot] = "good"
        state["reason"] = f"signed install staged {slot}; {stable} remains default until health commit"
    elif action == "set-state":
        if value not in {"good", "bad"}:
            raise ValueError("boot state must be good/bad")
        if value == "bad":
            if slot == stable:
                raise ValueError("refusing to invalidate the only committed fallback")
            state["states"][slot] = "bad"
            if state["pending"] == slot:
                state["pending"] = None
            state["reason"] = f"slot {slot} marked bad; committed {stable} retained"
        elif slot == current == stable:
            if state["pending"] is not None:
                if state["pending"] == stable:
                    state["reason"] = "firmware selector commit recovered; state recording was interrupted"
                else:
                    state["states"][state["pending"]] = "bad"
                    state["reason"] = "pending candidate did not commit; stable boot resumed (rollback or trial abandoned)"
                state["pending"] = None
            state["states"][slot] = "good"
        elif slot == current and slot == state["pending"] and trial:
            state["pending"] = None
            state["states"][slot] = "good"
            state["reason"] = f"trial {slot} passed health; committing firmware default"
            return state, slot
        else:
            raise ValueError("only an observed trial may commit the pending slot")
    else:
        raise ValueError("unsupported transition")
    return state, None


@contextmanager
def lock():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "lock").open("w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def dt_integer(name):
    raw = (Path("/proc/device-tree/chosen/bootloader") / name).read_bytes()
    if len(raw) != 4:
        raise ValueError("bootloader property is not a 32-bit integer")
    return int.from_bytes(raw, "big")


def commit_selector(slot):
    command(["mount", "-o", "remount,rw", "/boot/redux-control"])
    try:
        # The selector is authoritative; record commit only after file+directory sync.
        temporary = CONTROL.with_suffix(".new")
        with temporary.open("w") as stream:
            stream.write(autoboot(slot))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONTROL)
        descriptor = os.open(CONTROL.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        command(["mount", "-o", "remount,ro", "/boot/redux-control"])


def backend(arguments):
    if not arguments:
        raise ValueError("backend action required")
    action = arguments[0]
    current = slot_from_cmdline(Path("/proc/cmdline").read_text())
    with lock():
        stable = stable_from_autoboot(CONTROL.read_text())
        state = load_state(STATE, stable)
        if action == "get-primary" and len(arguments) == 1:
            print(state["pending"] or stable)
            return
        if action == "get-state" and len(arguments) == 2 and arguments[1] in SLOTS:
            print(state["states"][arguments[1]])
            return
        if action == "get-current" and len(arguments) == 1:
            print(current)
            return
        if (action == "set-primary" and len(arguments) != 2) or (action == "set-state" and len(arguments) != 3):
            raise ValueError("invalid backend arguments")
        # mark-good requires the health service to call us with observed trial context.
        trial = dt_integer("tryboot") == 1 and dt_integer("partition") == SLOTS[current]["boot"]
        state, committed = transition(state, stable, current, action,
                                      arguments[1] if len(arguments) > 1 else None,
                                      arguments[2] if len(arguments) > 2 else None, trial)
        if committed:
            if os.environ.get("REDUX_HEALTH_COMMIT") != "1":
                raise ValueError("pending commit requires the measured health gate")
            commit_selector(committed)
        STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_checkpoint(STATE, state)
        logging.info(state["reason"])


def check_health(context):
    current = context["current"]
    if dt_integer("partition") != SLOTS[current]["boot"]:
        raise ValueError("firmware boot partition disagrees with kernel slot")
    if command(["systemctl", "is-active", "redux.service"]) != "active":
        raise ValueError("redux is not active")
    if command(["findmnt", "-n", "-o", "FSTYPE", "/"]) != "overlay":
        raise ValueError("root is not protected by overlay")
    if "ro" not in command(["findmnt", "-n", "-o", "OPTIONS", "/media/root-ro"]).split(","):
        raise ValueError("lower root is writable")
    for point in ("/boot/firmware", "/boot/redux-control"):
        if "ro" not in command(["findmnt", "-n", "-o", "OPTIONS", point]).split(","):
            raise ValueError(f"{point} is writable")
    if "rw" not in command(["findmnt", "-n", "-o", "OPTIONS", "/captures"]).split(","):
        raise ValueError("captures is not writable")
    if command(["systemctl", "show", "-p", "RuntimeWatchdogUSec", "--value"]) in {"0", "0s", "infinity", ""}:
        raise ValueError("PID 1 hardware watchdog is disabled")
    if Path("/sys/class/watchdog/watchdog0/state").read_text().strip() != "active":
        raise ValueError("hardware watchdog is not active")
    capture = command(["findmnt", "-n", "-o", "SOURCE", "/captures"])
    if Path(capture).resolve() != Path(context["devices"]["6"]).resolve():
        raise ValueError("captures mount belongs to another disk")


def health():
    context = json.loads((RUNTIME / "context.json").read_text())
    current = context["current"]
    try:
        # Sixty real seconds of stable service PID, not a simulated success timer.
        pid = command(["systemctl", "show", "-p", "MainPID", "--value", "redux.service"])
        if not pid.isdigit() or int(pid) <= 0:
            raise ValueError("redux has no running process")
        for _ in range(12):
            check_health(context)
            if command(["systemctl", "show", "-p", "MainPID", "--value", "redux.service"]) != pid:
                raise ValueError("redux restarted during candidate validation")
            time.sleep(5)
        os.environ["REDUX_HEALTH_COMMIT"] = "1"
        backend(["set-state", current, "good"])
    except (OSError, ValueError, subprocess.SubprocessError):
        logging.exception("OTA health failed; committed selector retained")
        if dt_integer("tryboot") == 1:
            command(["systemctl", "reboot", "--no-block"])
        raise


def install(bundle):
    if "://" in bundle and not bundle.startswith("https://"):
        raise ValueError("remote updates require HTTPS and RAUC signature verification")
    preflight()
    context = json.loads((RUNTIME / "context.json").read_text())
    subprocess.run(["rauc", "install", bundle], check=True)
    state = load_state(STATE, stable_from_autoboot(CONTROL.read_text()))
    if state["pending"] is None or state["pending"] == context["current"]:
        raise ValueError("RAUC did not stage an inactive candidate; no reboot")
    os.sync()
    subprocess.run(["reboot", "0 tryboot"], check=True)


def preflight():
    firmware = command(["vcgencmd", "bootloader_config"])
    timeout = re.search(r"^BOOT_WATCHDOG_TIMEOUT=(\d+)$", firmware, re.M)
    if not timeout or not 15 <= int(timeout[1]) <= 300:
        raise ValueError("provision/test BOOT_WATCHDOG_TIMEOUT=15..300 before unattended OTA")
    context = json.loads((RUNTIME / "context.json").read_text())
    check_health(context)
    stable = stable_from_autoboot(CONTROL.read_text())
    if context["current"] != stable or load_state(STATE, stable)["pending"] is not None:
        raise ValueError("finish/resolve the existing trial before installing again")


def validate_manifest(text):
    import configparser
    manifest = configparser.ConfigParser()
    manifest.read_string(text)
    if manifest.get("update", "compatible") != COMPATIBLE:
        raise ValueError("bundle targets another image profile")
    if {s for s in manifest.sections() if s.startswith("image.")} != {"image.boot", "image.rootfs"}:
        raise ValueError("signed update must contain both boot and rootfs, and no data slot")
    if "handler" in manifest or manifest.get("bundle", "format") != "verity":
        raise ValueError("only the verified standard verity install path is allowed")
    if "post-install" not in manifest.get("image.boot", "hooks", fallback="").split(";"):
        raise ValueError("boot image must have the slot cmdline post-install hook")


def preinstall():
    preflight()
    validate_manifest((Path(os.environ["RAUC_BUNDLE_MOUNT_POINT"]) / "manifest.raucm").read_text())


def deadline():
    current = slot_from_cmdline(Path("/proc/cmdline").read_text())
    stable = stable_from_autoboot(CONTROL.read_text())
    if dt_integer("tryboot") == 1 and current != stable:
        logging.error("candidate missed 120-second health deadline; rebooting committed %s", stable)
        command(["systemctl", "reboot", "--no-block"])


def hook():
    if sys.argv[2:] != ["slot-post-install"]:
        raise ValueError("only boot slot post-install is supported")
    if os.environ.get("RAUC_SLOT_CLASS") != "boot":
        raise ValueError("hook must apply to a boot slot")
    slot = os.environ["RAUC_SLOT_BOOTNAME"]
    context = json.loads((RUNTIME / "context.json").read_text())
    if slot not in SLOTS or slot == context["current"]:
        raise ValueError("refusing to patch the running slot")
    root_uuid = Path(context["devices"][str(SLOTS[slot]["root"])]).name
    path = Path(os.environ["RAUC_SLOT_MOUNT_POINT"]) / "cmdline.txt"
    path.write_text(patch_cmdline(path.read_text(), slot, root_uuid))
    os.sync()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mode, *arguments = sys.argv[1:]
    if mode == "generator":
        generate(arguments[0], discover(Path("/proc/cmdline").read_text()))
    elif mode == "backend":
        backend(arguments)
    elif mode == "health":
        health()
    elif mode == "install" and len(arguments) == 1:
        install(arguments[0])
    elif mode == "hook":
        hook()
    elif mode == "preinstall":
        preinstall()
    elif mode == "deadline":
        deadline()
    else:
        raise ValueError("usage: redux-ota install <signed bundle path or HTTPS URL>")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        logging.error("OTA refused: %s", error)
        sys.exit(1)
