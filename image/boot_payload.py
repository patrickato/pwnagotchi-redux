"""Read-only host-side checks before pi-gen closes boot partitions.

This validates staged boot-file *presence and slot configuration*, not actual
firmware execution, kernel correctness or filesystem integrity. Run while the
exported FAT and root filesystems are mounted during image construction.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

_KERNEL_AND_FIRMWARE = (
    "kernel8.img", "kernel_2712.img", "start4.elf", "fixup4.dat",
    "bcm2711-rpi-4-b.dtb", "bcm2712-rpi-5-b.dtb",
)
_UUID = re.compile(r"[A-Fa-f0-9-]{36}\Z")
_ARM64 = re.compile(r"(?m)^\s*arm_64bit\s*=\s*1\s*(?:#.*)?$")


def required_file(folder: Path, name: str) -> Path:
    path = folder / name
    # A FAT export cannot preserve a Unix symlink. Refuse fixtures or
    # accidental staging layouts that would point outside the mounted tree.
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or linked boot payload: {name}")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        raise ValueError(f"empty/non-file boot payload: {name}")
    return path


def text_file(folder: Path, name: str, limit=8192) -> str:
    path = required_file(folder, name)
    if path.stat().st_size > limit:
        raise ValueError(f"unexpectedly large text boot payload: {name}")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise ValueError(f"invalid UTF-8 boot payload: {name}") from error


def boot_files(root: Path, *, slot=None, partuuid=None):
    boot = root / "boot/firmware"
    if not boot.is_dir() or boot.is_symlink():
        raise ValueError("boot/firmware must be an accessible mounted directory")
    evidence = {}
    for filename in _KERNEL_AND_FIRMWARE:
        path = required_file(boot, filename)
        evidence[filename] = {"bytes": path.stat().st_size}
    config = text_file(boot, "config.txt")
    if not _ARM64.search(config):
        raise ValueError("boot config does not explicitly enable ARM64")
    cmdline = text_file(boot, "cmdline.txt")
    if "\n" in cmdline.strip("\r\n") or "\x00" in cmdline:
        raise ValueError("cmdline.txt must contain one line without NULs")
    words = cmdline.split()
    # Stock Pi OS firstboot/init_resize scripts can alter partition boundaries.
    # A Redux root partition is deliberately NOT last: REDUXCAP follows it.
    if any(word.startswith('init=') or 'firstboot' in word.lower()
           or 'resize' in word.lower() for word in words):
        raise ValueError('stock firstboot/resize token remains in boot cmdline')
    mask = root / 'etc/systemd/system/resize2fs_once.service'
    if not mask.is_symlink() or mask.readlink() != Path('/dev/null'):
        raise ValueError('resize2fs_once must be masked for fixed partition layout')
    legacy = root / 'etc/init.d/resize2fs_once'
    if legacy.exists() or legacy.is_symlink():
        raise ValueError('legacy resize2fs_once init script remains in image')
    roots = [word.removeprefix("root=") for word in words if word.startswith("root=")]
    slots = [word.removeprefix("rauc.slot=") for word in words
             if word.startswith("rauc.slot=")]
    if len(roots) != 1 or not roots[0]:
        raise ValueError("boot cmdline requires exactly one root device")
    if slot:
        if slot not in ("A", "B") or len(slots) != 1 or slots[0] != slot:
            raise ValueError("A/B cmdline requires its matching rauc.slot")
        if not partuuid or not _UUID.fullmatch(partuuid):
            raise ValueError("A/B export must supply a real root PARTUUID")
        if roots[0].lower() != ("PARTUUID=" + partuuid).lower():
            raise ValueError("A/B boot cmdline points at the wrong root partition")
        if any(word.startswith("init=") for word in words):
            raise ValueError("A/B cmdline must not run the stock partition resizer")
        if "panic=10" not in words:
            raise ValueError("A/B cmdline missing rollback reboot timeout")
    elif slots:
        raise ValueError("Lite export unexpectedly contains an A/B slot selector")
    if not any(roots[0].startswith(prefix) for prefix in ("PARTUUID=", "UUID=", "/dev/")):
        raise ValueError("unknown Lite boot root device")
    return {"files": evidence, "config": config, "words": words,
            "cmdline": cmdline, "root": roots[0], "slot": slot}


def selector(root: Path):
    path = root / "boot/redux-control"
    if not path.is_dir() or path.is_symlink():
        raise ValueError("A/B selector mountpoint is missing")
    content = text_file(path, "autoboot.txt")
    expected = ("[all]\ntryboot_a_b=1\nboot_partition=2\n"
                "[tryboot]\nboot_partition=3\n")
    if content != expected:
        raise ValueError("unexpected A/B fallback selector; refusing export")
    if not _ARM64.search(text_file(path, "config.txt")):
        raise ValueError("A/B selector config does not enable ARM64")
    required_file(path, "start.elf")
    return {"stable_boot_partition": 2, "trial_boot_partition": 3}


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def verify(root, *, profile="lite", other_root=None,
           a_root_partuuid=None, b_root_partuuid=None):
    root = Path(root)
    if profile not in ("lite", "ab"):
        raise ValueError("unsupported image profile")
    if profile == "lite":
        if other_root is not None or a_root_partuuid or b_root_partuuid:
            raise ValueError("Lite profile cannot accept A/B slot arguments")
        a = boot_files(root)
        return {"profile": "lite", "boot_files": sorted(a["files"]),
                "boot_files_observed": True, "hardware_boot_verified": False}
    if other_root is None:
        raise ValueError("A/B validation needs both mounted boot/root slots")
    if a_root_partuuid == b_root_partuuid:
        raise ValueError("A/B root PARTUUIDs must be distinct")
    a = boot_files(root, slot="A", partuuid=a_root_partuuid)
    b = boot_files(Path(other_root), slot="B", partuuid=b_root_partuuid)
    control = selector(root)
    if a["config"] != b["config"]:
        raise ValueError("boot A/B firmware configuration differs")
    for filename in _KERNEL_AND_FIRMWARE:
        if digest(root / "boot/firmware" / filename) != digest(
                Path(other_root) / "boot/firmware" / filename):
            raise ValueError(f"boot A/B payload differs: {filename}")
    def normalized(words):
        return [w for w in words if not w.startswith(("root=", "rauc.slot="))]
    if normalized(a["words"]) != normalized(b["words"]):
        raise ValueError("boot A/B cmdlines have unexpected non-slot differences")
    return {"profile": "ab", "boot_files": sorted(a["files"]),
            "boot_files_observed": True, "slots": ["A", "B"],
            "selector": control, "hardware_boot_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("lite", "ab"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--other-root", type=Path)
    parser.add_argument("--a-root-partuuid")
    parser.add_argument("--b-root-partuuid")
    args = parser.parse_args(argv)
    try:
        report = verify(args.root, profile=args.profile,
                        other_root=args.other_root,
                        a_root_partuuid=args.a_root_partuuid,
                        b_root_partuuid=args.b_root_partuuid)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
