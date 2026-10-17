#!/usr/bin/env python3
"""Strict-SSH, memory-only acceptance; no Pi installation or saved receipt.

Use from WSL: python3 scripts/pi-acceptance.py --host <verified Pi IP>
The SSH client's measured return address is passed privately to the collector.
No host-key replacement, RF, agent, receipt writing or persistent Pi artifacts.
"""
import argparse
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import shlex
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from redux.radio.local_evidence import BoundedReader, Unavailable  # noqa: E402

MODULES = ('redux.acceptance', 'redux.radio.local_evidence', 'redux.radio.live_lease',
           'redux.radio.probe', 'redux.radio.orchestrator', 'redux.radio.dual_radio',
           'redux.radio.split_integration', 'redux.connectivity.bluez_pan',
           'redux.connectivity.pan_preflight', 'redux.radio.readiness', 'redux.core.runtime_identity')


def bundle(root=ROOT):
    sources = {n: (root/(n.replace('.', '/')+'.py')).read_text() for n in MODULES}
    for name in ('redux', 'redux.radio', 'redux.connectivity', 'redux.core'):
        # Namespace-only parents intentionally avoid importing optional live code.
        sources[name] = ''
    encoded = base64.b64encode(json.dumps(sources, sort_keys=True).encode()).decode()
    return '''import base64, importlib.abc, importlib.util, json, sys
sources=json.loads(base64.b64decode("ENCODED"))
class MemorySource(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name in sources:
            return importlib.util.spec_from_loader(name, self, is_package=name in ('redux','redux.radio','redux.connectivity','redux.core'))
    def create_module(self, spec): return None
    def exec_module(self, module): exec(compile(sources[module.__name__], '<readonly-acceptance>', 'exec'), module.__dict__)
sys.meta_path.insert(0, MemorySource())
# SSH_CONNECTION is supplied by the verified server, never guessed from DHCP.
connection=sys.argv[1].split()
if len(connection)!=4: raise SystemExit(1)
import ipaddress
peer=str(ipaddress.ip_address(connection[0]))
sys.argv=['redux-acceptance','--management-peer',peer]+sys.argv[2:]
from redux.acceptance import main
main()
'''.replace('ENCODED', encoded)


def command(host, identity, pan_args=()):
    ipaddress.ip_address(host)
    remote = 'sudo -n python3 -B - "$SSH_CONNECTION"'
    if pan_args:
        remote += ' ' + shlex.join(pan_args)
    return ['ssh', '-i', str(identity), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'ConnectTimeout=8',
            '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', f'pi@{host}', remote]


def fetch(args, source, *, reader_factory=None):
    class SshReader(BoundedReader):
        def allowed(self, candidate):
            return list(candidate) == args
    reader = (reader_factory or SshReader)(seconds=45, command_seconds=45)
    # Temporary file exists on the laptop only, and is unlinked at close.
    with tempfile.TemporaryFile(mode='w+b') as input_file:
        input_file.write(source.encode());input_file.seek(0)
        output = reader(args, stdin=input_file)
    try:
        value = json.loads(output)
        if (type(value) is not dict or value.get('format') != 'redux-readonly-acceptance-v1'
                or value.get('hardware_changes') is not False):
            raise ValueError
    except (ValueError, TypeError):
        raise Unavailable('invalid_acceptance_response') from None
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', required=True)
    p.add_argument('--dual-radio', action='store_true')
    p.add_argument('--identity', type=Path, default=Path.home()/'.ssh/redux_field_ed25519')
    p.add_argument('--pan-peer')
    p.add_argument('--pan-route-peer')
    p.add_argument('--pan-adapter', default='/org/bluez/hci0')
    a = p.parse_args(argv)
    try:
        ipaddress.ip_address(a.host)
        if a.pan_route_peer:ipaddress.ip_address(a.pan_route_peer)
    except ValueError:
        p.error('valid IP address required')
    if bool(a.pan_peer) != bool(a.pan_route_peer):p.error('both PAN peer and route peer required')
    pan = ['--pan-peer', a.pan_peer, '--pan-route-peer', a.pan_route_peer,
           '--pan-adapter', a.pan_adapter] if a.pan_peer else []
    if a.dual_radio:pan += ['--dual-radio']
    source = bundle()
    try:
        value = fetch(command(a.host, a.identity, pan), source)
    except (Unavailable, OSError):
        print('Read-only acceptance unavailable; verify Ethernet, trusted SSH host key and sudo -n.', file=sys.stderr)
        return 3
    value['collector_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    print(json.dumps(value, sort_keys=True))
    return 0 if all(v == 'PASS' for v in value['gates'].values()) else 2


if __name__ == '__main__':
    raise SystemExit(main())
