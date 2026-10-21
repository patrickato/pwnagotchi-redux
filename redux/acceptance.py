"""Single local read-only Pi acceptance entry point; no receipts or state writes.

Run explicitly: python3 -B -m redux.acceptance --management-peer <Ethernet peer>
Private peer addresses/paths and command output are never included in reports.
"""
import argparse
import json
import platform
import os
from pathlib import Path
import re

from .radio.local_evidence import BoundedReader, Unavailable, collect, read_small
from .connectivity.pan_preflight import collect_pan, gates as pan_gates


def usb_presence(root=Path('/sys/bus/usb/devices')):
    """Count USB devices (including hubs), never infer a working Wi-Fi adapter."""
    def snapshot():
        rows = {}
        for number, item in enumerate(Path(root).iterdir()):
            if number >= 256:
                raise Unavailable('usb_inventory_overflow')
            descriptor = item/'bDeviceClass'
            if not descriptor.exists():
                continue  # USB interface, not a device descriptor
            kind = read_small(descriptor, 8)
            if not re.fullmatch(r'[0-9a-fA-F]{2}', kind):
                raise Unavailable('usb_descriptor_invalid')
            target = item.resolve(strict=True)
            st = target.stat()
            rows[item.name] = (str(target), st.st_dev, st.st_ino, kind)
        return rows
    before = snapshot()
    if before != snapshot():
        raise Unavailable('usb_inventory_changed')
    return {'device_count_including_hubs': len(before), 'inventory_stable': True}


def report(peer, *, run=None, collector=collect, pan=None, dual_radio=False):
    if dual_radio:
        from .radio.readiness import Reader, collect_readiness
        run = run or Reader(seconds=35)
    else:
        run = run or BoundedReader(seconds=35)
    result = {'format': 'redux-readonly-acceptance-v1', 'hardware_changes': False,
              'gates': {}, 'radios': [], 'pan': pan_gates({})}
    def check(name, action):
        try:
            value = action()
            result['gates'][name] = 'PASS' if value is True else 'FAIL' if value is False else 'UNKNOWN'
        except (Unavailable, OSError, ValueError, KeyError, TypeError, IndexError):
            result['gates'][name] = 'UNKNOWN'

    check('pi4_or_pi5', lambda: bool(re.search(r'Raspberry Pi [45] Model',
          read_small('/proc/device-tree/model').rstrip('\x00'))))
    check('arm64', lambda: platform.machine() == 'aarch64')
    check('driver_kernel_vermagic', lambda: run(['modinfo', '-F', 'vermagic', 'brcmfmac']).split()[0]
          == run(['uname', '-r']).strip())
    check('loaded_driver_srcversion', lambda: bool(read_small('/sys/module/brcmfmac/srcversion'))
          and read_small('/sys/module/brcmfmac/srcversion') == run(['modinfo', '-F', 'srcversion', 'brcmfmac']).strip())
    check('regulatory_US', lambda: bool(re.search(r'(?m)^country US:',
          run(['iw', 'reg', 'get']).split('phy#', 1)[0])))
    def storage():
        rows = json.loads(run(['findmnt', '-J', '-T', '/captures', '-o', 'TARGET,FSTYPE,OPTIONS']))['filesystems']
        return (len(rows) == 1 and rows[0]['target'] == '/captures' and rows[0]['fstype'] == 'ext4'
                and 'rw' in rows[0]['options'].split(','))
    check('capture_storage', storage)
    try:
        result['usb_bus'] = usb_presence()
    except (Unavailable, OSError, ValueError):
        result['usb_bus'] = {'device_count_including_hubs': None, 'inventory_stable': None}
    def capacity():
        v = os.statvfs('/captures')
        return v.f_bavail * v.f_frsize >= 256 * 1024 * 1024
    check('capture_free_space_256MiB', capacity)
    for unit in ('redux.service', 'redux-live.service', 'redux-capture-ingest.timer'):
        check(unit, lambda u=unit: run(['systemctl', 'show', '--property=ActiveState', '--value', u]).strip() == 'active')
    try:
        checks, details = {}, {}
        receipt = collector(peer, run=run, ownership_checks=checks, diagnostics=details)
        result['inventory_status'] = 'fresh'
        result['gates']['ethernet_return_path'] = 'PASS' if receipt['ethernet_verified'] is True else 'FAIL'
        for number, row in enumerate(receipt['radios'], 1):
            # Deliberate field allowlist: no iface, PHY, address, serial, ancestry,
            # boot ID, process IDs or raw exception/backend text in public output.
            result['radios'].append(dict(radio=number, transport=row['transport'],
                monitor_advertised=row['monitor'], kernel_mode=row['mode'] if row['mode'] in
                ('managed', 'monitor') else 'unknown', arphrd=row['arphrd'],
                exclusion_checks='PASS' if checks.get(row['iface']) is True else 'UNKNOWN',
                authenticated_owner='UNKNOWN', decoded_reception='UNKNOWN', lease_eligible=False,
                **{key: details.get(row['iface'], {}).get(key) for key in
                   ('nm_managed', 'nm_state', 'driver', 'identity_stable', 'usb_ancestry', 'conflicting_processes')}))
        result['gates']['usb_wifi_netdev_present'] = 'PASS' if any(r['transport'] == 'usb' for r in result['radios']) else 'FAIL'
        result['gates']['lease_eligible'] = 'FAIL'  # no independent decoded reception/authenticated owner
    except (Unavailable, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        reasons = {'capabilities_unknown', 'observation_changed', 'observation_stale',
                   'radio_inventory_changed', 'boot_changed', 'ethernet_changed',
                   'iw_identity_changed', 'process_ownership_changed', 'nm_changed',
                   'inventory_disagreement', 'radio_identity_disagreement', 'ethernet_unverified',
                   'process_inventory_overflow', 'inventory_overflow', 'radio_overflow',
                   'iw_identity_invalid', 'interface_invalid', 'file_overflow', 'command_denied',
                   'command_timeout', 'command_overflow', 'command_failed', 'command_unavailable', 'deadline'}
        result['inventory_status'] = (str(exc) if isinstance(exc, Unavailable) and str(exc) in reasons else
            'permission_denied' if isinstance(exc, PermissionError) else
            'device_or_process_disappeared' if isinstance(exc, FileNotFoundError) else
            'io_unavailable' if isinstance(exc, OSError) else 'invalid_measurement')
        result['gates'].update(ethernet_return_path='UNKNOWN', usb_wifi_netdev_present='UNKNOWN', lease_eligible='UNKNOWN')
    if pan:
        try:
            result['pan'] = pan_gates(collect_pan(run, pan[0], pan[1], adapter=pan[2]))
        except (Unavailable, OSError, ValueError, KeyError, TypeError, IndexError):
            pass
    if dual_radio:
        result['dual_radio_readiness']=collect_readiness(peer,run=run)
        result['gates']['dual_radio_readiness'] = (
            'PASS' if result['dual_radio_readiness'].get('readiness',{}).get('state')
            == 'READY_FOR_SEPARATE_QUALIFICATION' else 'FAIL')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--management-peer', required=True)
    parser.add_argument('--dual-radio', action='store_true', help='read-only loaded policy, NM and framing evidence')
    parser.add_argument('--pan-peer', help='private pinned BlueZ Device1 path; no discovery')
    parser.add_argument('--pan-route-peer', help='private already-provisioned phone IP')
    parser.add_argument('--pan-adapter', default='/org/bluez/hci0')
    args = parser.parse_args(argv)
    import ipaddress
    try:
        ipaddress.ip_address(args.management_peer)
        if args.pan_route_peer:
            ipaddress.ip_address(args.pan_route_peer)
    except ValueError:
        parser.error('valid IP addresses required')
    if bool(args.pan_peer) != bool(args.pan_route_peer):
        parser.error('PAN requires both pinned peer and route peer')
    value = report(args.management_peer, dual_radio=args.dual_radio, pan=(args.pan_peer, args.pan_route_peer, args.pan_adapter)
                   if args.pan_peer else None)
    print(json.dumps(value, sort_keys=True))
    return 0 if all(v == 'PASS' for v in value['gates'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
