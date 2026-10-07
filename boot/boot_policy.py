"""Disk-boot policy for Pi 4/5; selections carry their concrete reason."""
from pathlib import Path

MASK_UNITS = {
    "NetworkManager-wait-online.service": "redux boot needs local storage, not an uplink",
    "systemd-networkd-wait-online.service": "no network dependency on the service boot path",
    "apt-daily.service": "field root is immutable; update through reviewed images",
    "apt-daily.timer": "no automatic APT writes to the field overlay",
    "apt-daily-upgrade.service": "kernel and nexmon are upgraded as a pair",
    "apt-daily-upgrade.timer": "no unattended image changes",
    "rpi-eeprom-update.service": "boot firmware updates require an explicit maintenance window",
    "ModemManager.service": "default image has no cellular modem role",
    "avahi-daemon.service": "no automatic local service advertisement is required",
    "avahi-daemon.socket": "socket activation must not restart the removed boot work",
    "man-db.timer": "manual index rebuilding is irrelevant to field boot",
}
INITRD_CANDIDATES = (
    "ext4", "overlay", "mmc_block", "sdhci", "sdhci_pltfm", "sdhci_brcmstb",
    "sdhci_iproc", "bcm2835_sdhost", "usb_storage", "uas", "xhci_hcd",
    "xhci_pci", "xhci_plat_hcd", "pcie_brcmstb", "nvme", "nvme_core", "bcm2835_wdt",
)


def select_modules(paths):
    actual = {Path(p).name.split(".ko", 1)[0].replace("-", "_") for p in paths}
    if "ext4" not in actual or "overlay" not in actual or "mmc_block" not in actual:
        raise ValueError("target kernel lacks required ext4/overlay/MMC support; cannot trim safely")
    return [name for name in INITRD_CANDIDATES if name in actual]


def write_policy(root):
    root = Path(root)
    selected = set()
    reasons = []
    for kernel in sorted((root / "lib/modules").iterdir()):
        if not kernel.is_dir():
            continue
        paths = list(kernel.rglob("*.ko*"))
        paths += (kernel / "modules.builtin").read_text().splitlines()
        modules = select_modules(paths)
        selected.update(modules)
        reasons.append(f"{kernel.name}: disk boot requires SD/USB/NVMe, ext4 and overlay; available modules: {', '.join(modules)}")
    if not selected:
        raise ValueError("no target kernels found")
    conf = root / "etc/initramfs-tools/conf.d/redux-modules"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("MODULES=list\n")
    (root / "etc/initramfs-tools/modules").write_text("\n".join(sorted(selected)) + "\n")
    manifest = root / "usr/share/redux/boot-policy.txt"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("\n".join(reasons + [f"{unit}: {reason}" for unit, reason in MASK_UNITS.items()]) + "\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        write_policy(args.root)
    except (OSError, ValueError) as error:
        parser.error(str(error))
