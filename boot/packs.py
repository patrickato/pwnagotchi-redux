"""Opt-in passive Kali tools in a separate, signed-APT arm64 filesystem."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from redux.core.boot import atomic_checkpoint

PACKS = {"kali-tools": {"suite": "kali-rolling", "packages": ("tcpdump", "tshark"),
                       "reason": "opt-in packet inspection tools; no attack launcher or radio operation"}}
BASE = Path("/captures/packs")


def plan(name, mirror, keyring):
    if name not in PACKS:
        raise ValueError("unknown pack")
    from urllib.parse import urlsplit
    url = urlsplit(mirror)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("APT mirror requires a plain HTTPS URL without credentials")
    keyring = Path(keyring)
    if not keyring.is_file() or keyring.is_symlink():
        raise ValueError("supply a verified public Kali archive keyring file")
    return {"name": name, "architecture": "arm64", "mirror": mirror,
            "suite": PACKS[name]["suite"], "packages": list(PACKS[name]["packages"]),
            "reason": PACKS[name]["reason"]}


def pack_path(name, base=BASE):
    if name not in PACKS:
        raise ValueError("unknown pack")
    base = Path(base)
    if base.is_symlink() or (base / name).is_symlink():
        raise ValueError("pack directories must not be symlinks")
    root = base / name
    if root.resolve().parent != base.resolve():
        raise ValueError("pack path escaped managed storage")
    return root


def checked(arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


def install(name, mirror, keyring, base=BASE, run=checked):
    specification = plan(name, mirror, keyring)
    if os.geteuid() != 0:
        raise ValueError("pack installation requires root")
    # Do not silently place persistent tools in the ephemeral overlay.
    mount = subprocess.check_output(["findmnt","-n","-o","FSTYPE,OPTIONS","/captures"],text=True).split()
    if len(mount) != 2 or mount[0] != "ext4" or "rw" not in mount[1].split(","):
        raise ValueError("captures must be a writable ext4 partition")
    root = pack_path(name, base)
    if root.exists():
        raise ValueError("pack already exists; inspect/remove it before replacing")
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700)
    record = root / "pack.json"
    atomic_checkpoint(record,specification | {"status":"installing"})
    filesystem = root / "rootfs"
    try:
        # sid's debootstrap script supports derivative archive suites. The supplied
        # archive keyring is mandatory; never disable signature verification.
        run(["debootstrap","--arch=arm64","--variant=minbase",f"--keyring={Path(keyring).resolve()}",
             specification["suite"],str(filesystem),mirror,"/usr/share/debootstrap/scripts/sid"])
        policy = filesystem / "usr/sbin/policy-rc.d"
        policy.write_text("#!/bin/sh\n# Packs never start services during install.\nexit 101\n")
        policy.chmod(0o755)
        public = filesystem / "usr/share/keyrings/redux-kali.gpg"
        public.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(keyring,public)
        public.chmod(0o644)
        (filesystem / "etc/apt/sources.list").write_text(
            f"deb [arch=arm64 signed-by=/usr/share/keyrings/redux-kali.gpg] {mirror} kali-rolling main\n")
        environment = dict(os.environ,DEBIAN_FRONTEND="noninteractive")
        run(["chroot",str(filesystem),"debconf-set-selections"],
            input="wireshark-common wireshark-common/install-setuid boolean false\n",text=True,env=environment)
        run(["chroot",str(filesystem),"apt-get","update"],env=environment)
        run(["chroot",str(filesystem),"apt-get","install","--no-install-recommends","-y",*specification["packages"]],env=environment)
        run(["chroot",str(filesystem),"apt-get","clean"])
        packages = subprocess.check_output(["chroot",str(filesystem),"dpkg-query","-W","-f=${binary:Package}\t${Version}\n"],text=True)
        (root / "packages.tsv").write_text(packages)
        atomic_checkpoint(record,specification | {"status":"installed","reason":specification["reason"] + "; signatures checked by debootstrap/APT; no tool executed"})
    except (OSError,subprocess.SubprocessError) as error:
        atomic_checkpoint(record,specification | {"status":"failed","reason":f"installation stopped ({error}); partial files retained for inspection/remove"})
        raise


def remove(name, base=BASE, mountinfo=Path("/proc/self/mountinfo")):
    root = pack_path(name,base)
    if not root.exists():
        return "pack is absent; nothing removed"
    if os.geteuid() != 0:
        raise ValueError("pack removal requires root")
    resolved = root.resolve()
    for line in mountinfo.read_text().splitlines():
        fields = line.split()
        mounted = Path(fields[4].replace("\\040"," ").replace("\\134","\\"))
        if mounted == resolved or resolved in mounted.parents:
            raise ValueError("pack has mounted filesystems; unmount them before removal")
    shutil.rmtree(root)
    return f"removed {name}: only this managed pack filesystem was deleted; base OS unchanged"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("list","install","remove"))
    parser.add_argument("name",nargs="?",choices=PACKS)
    parser.add_argument("--mirror",default="https://http.kali.org/kali")
    parser.add_argument("--keyring",type=Path)
    parser.add_argument("--dry-run",action="store_true")
    args = parser.parse_args()
    if args.action == "list":
        for name, info in PACKS.items():
            record = pack_path(name) / "pack.json"
            status = json.loads(record.read_text()) if record.exists() else {"status":"not installed","reason":info["reason"]}
            print(json.dumps({"name":name,**status},sort_keys=True))
    elif not args.name:
        parser.error("select a pack")
    elif args.action == "install":
        if args.keyring is None:
            parser.error("--keyring is required; verify the archive key independently")
        specification = plan(args.name,args.mirror,args.keyring)
        if args.dry_run:
            print(json.dumps(specification,sort_keys=True))
        else:
            install(args.name,args.mirror,args.keyring)
            print(f"Installed {args.name}; inspect with packs list. No tool was executed.")
    elif args.dry_run:
        print(f"Would remove only {pack_path(args.name)}; no files changed")
    else:
        print(remove(args.name))


if __name__ == "__main__":
    try:
        main()
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        raise SystemExit(f"Packs stopped: {error}")
