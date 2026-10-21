"""Storage tiering: SD primary stays strict; USB adds bulk, NAS adds archive."""
from redux.storage_tier import (StorageVolume, Tier, plan_storage_tiers,
                                 parse_findmnt)

SD = StorageVolume("/captures", "ext4", rw=True)
USB = StorageVolume("/mnt/usbbulk", "exfat", rw=True)
NAS = StorageVolume("/mnt/nas", "nfs4", rw=True, network=True)


def test_sd_only_works_and_nudges_for_more():
    p = plan_storage_tiers([SD])
    assert p.target(Tier.PRIMARY) == "/captures"
    assert p.target(Tier.BULK) is None and p.target(Tier.ARCHIVE) is None
    assert any("add a USB disk" in w for w in p.warnings)


def test_usb_disk_adds_a_bulk_tier_even_on_exfat():
    p = plan_storage_tiers([SD, USB])
    assert p.target(Tier.PRIMARY) == "/captures"
    assert p.target(Tier.BULK) == "/mnt/usbbulk"      # exfat accepted for bulk
    assert p.warnings == []


def test_nas_adds_an_archive_tier():
    p = plan_storage_tiers([SD, NAS])
    assert p.target(Tier.ARCHIVE) == "/mnt/nas"
    assert "NAS" in p.reasons[Tier.ARCHIVE]


def test_full_three_tier_rig():
    p = plan_storage_tiers([SD, USB, NAS])
    assert p.target(Tier.PRIMARY) == "/captures"
    assert p.target(Tier.BULK) == "/mnt/usbbulk"
    assert p.target(Tier.ARCHIVE) == "/mnt/nas"
    assert p.warnings == []


def test_primary_still_requires_lock_capable_ext4():
    # an exfat stick mounted at /captures is NOT accepted as primary (locking)
    p = plan_storage_tiers([StorageVolume("/captures", "exfat", rw=True)])
    assert p.target(Tier.PRIMARY) is None
    assert any("lock-capable ext4" in w for w in p.warnings)


def test_readonly_volumes_are_not_used():
    p = plan_storage_tiers([SD, StorageVolume("/mnt/usbbulk", "vfat", rw=False)])
    assert p.target(Tier.BULK) is None


def test_parse_findmnt_classifies_network_and_local():
    text = ('{"filesystems":['
            '{"target":"/captures","fstype":"ext4","options":"rw,noatime","source":"/dev/mmcblk0p3"},'
            '{"target":"/mnt/usbbulk","fstype":"exfat","options":"rw","source":"/dev/sda1"},'
            '{"target":"/mnt/nas","fstype":"nfs4","options":"rw","source":"192.168.1.9:/pwn"}]}')
    vols = parse_findmnt(text)
    bymp = {v.mountpoint: v for v in vols}
    assert bymp["/mnt/nas"].network is True
    assert bymp["/mnt/usbbulk"].network is False and bymp["/mnt/usbbulk"].rw is True
    # end to end through the planner
    p = plan_storage_tiers(vols)
    assert p.target(Tier.PRIMARY) == "/captures"
    assert p.target(Tier.BULK) == "/mnt/usbbulk"
    assert p.target(Tier.ARCHIVE) == "/mnt/nas"
