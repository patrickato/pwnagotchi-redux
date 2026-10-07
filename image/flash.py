#!/usr/bin/env python3
"""Write a checksum-verified release to an unmounted removable Linux disk."""
import argparse
import fcntl
import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import subprocess


def source(path):
    return lzma.open(path, 'rb') if path.suffix == '.xz' else path.open('rb')


def inspect_image(path, expected):
    if not path.is_file() or len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected.lower()):
        raise ValueError('Provide a regular image and its trusted release SHA256.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        raise ValueError('Release checksum mismatch; refusing to write.')
    raw = hashlib.sha256()
    size = 0
    header = b''
    with source(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            if not header:
                header = chunk[:512]
            size += len(chunk)
            raw.update(chunk)
    if size < 512 or size % 512 or header[510:512] != b'\x55\xaa':
        raise ValueError('Image has no sector-aligned partition table.')
    return size, raw.hexdigest()


def descendants(node):
    yield node
    for child in node.get('children', []):
        yield from descendants(child)


def select_disk(inventory, device, size):
    matches = [node for disk in inventory['blockdevices'] for node in descendants(disk) if node['path'] == device]
    if len(matches) != 1:
        raise ValueError('Target disk is missing or ambiguous.')
    disk = matches[0]
    if disk['type'] != 'disk' or not disk['rm'] or disk['ro'] or int(disk['size']) < size:
        raise ValueError('Target must be a writable, removable whole disk with enough capacity.')
    if any(any(node.get('mountpoints') or []) for node in descendants(disk)):
        raise ValueError('Unmount every target partition before flashing.')
    return disk


def copy_stream(reader, writer, size, expected):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = reader.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError('Image was truncated during writing.')
        digest.update(chunk)
        remaining -= len(chunk)
        view = memoryview(chunk)
        while view:
            written = writer.write(view)
            if not written or written > len(view):
                raise OSError('Disk write made no progress.')
            view = view[written:]
    if reader.read(1) or digest.hexdigest() != expected:
        raise ValueError('Image changed during writing; disk is not verified.')


def verify_stream(reader, size, expected):
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = reader.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError('Disk read-back was truncated.')
        digest.update(chunk)
        remaining -= len(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('Disk read-back checksum mismatch.')


def disk_snapshot(device, size):
    info = os.stat(device)
    if not stat.S_ISBLK(info.st_mode):
        raise ValueError('Target must be a Linux block device.')
    inventory = json.loads(subprocess.check_output([
        'lsblk', '--json', '--bytes', '--output', 'PATH,TYPE,SIZE,RM,RO,MOUNTPOINTS,MODEL'
    ], text=True))
    return info.st_rdev, select_disk(inventory, device, size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--sha256', required=True, help='SHA256 from a trusted release channel')
    parser.add_argument('--device', required=True, help='whole removable disk, e.g. /dev/sdb')
    parser.add_argument('--confirm-device', help='repeat the exact device path to authorize erasure')
    parser.add_argument('--dry-run', action='store_true', help='validate and print the actual plan without writing')
    args = parser.parse_args()
    try:
        size, digest = inspect_image(args.image, args.sha256)
        identity, disk = disk_snapshot(args.device, size)
        print(json.dumps({'image': str(args.image), 'raw_bytes': size, 'raw_sha256': digest,
                          'target': disk, 'reason': 'Verified release; removable disk has sufficient capacity and no mounted partitions.'}))
        if args.dry_run:
            return
        if os.geteuid() != 0 or args.confirm_device != args.device:
            raise ValueError('Writing requires root and --confirm-device matching --device exactly; this erases the disk.')
        if disk_snapshot(args.device, size) != (identity, disk):
            raise ValueError('Target identity or mount state changed; refusing to write.')
        with source(args.image) as reader, open(args.device, 'wb', buffering=0) as writer:
            if os.fstat(writer.fileno()).st_rdev != identity:
                raise ValueError('Opened disk identity changed; refusing to write.')
            copy_stream(reader, writer, size, digest)
            os.fsync(writer.fileno())
            fcntl.ioctl(writer.fileno(), 0x1261)  # BLKFLSBUF: force subsequent verification off the block cache.
        with open(args.device, 'rb', buffering=0) as reader:
            verify_stream(reader, size, digest)
        print(json.dumps({'verified_bytes': size, 'sha256': digest, 'device': args.device,
                          'reason': 'Flushed disk and verified every image byte by read-back.'}))
    except (ValueError, OSError, lzma.LZMAError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Flash failed: {exc}\n')


if __name__ == '__main__':
    main()
