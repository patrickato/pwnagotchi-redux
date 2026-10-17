"""Acceptance presentation is sanitized; never executes hardware operations."""
import ast
import json
from pathlib import Path

import pytest

from redux import acceptance as a
from redux.radio.local_evidence import Unavailable


def supplier(peer, **kw):
    kw['ownership_checks']['secret_iface']=True
    return dict(ethernet_verified=True,boot_id='PRIVATE_BOOT',radios=[dict(
        iface='secret_iface',phy='PRIVATE_PHY',transport='usb',monitor=True,
        arphrd=803,mode='monitor',decoded_frames=None,owner_verified=False)])


def test_consolidated_output_is_allowlisted_and_cannot_claim_reception():
    def commands(args):
        if args[0]=='systemctl':return 'inactive'
        if args[:3]==['iw','reg','get']:return 'global\ncountry US: DFS-FCC\n'
        raise Unavailable('PRIVATE BACKEND ERROR')
    report=a.report('192.0.2.1',run=commands,collector=supplier)
    text=json.dumps(report)
    assert 'PRIVATE' not in text and 'secret_iface' not in text and '192.0.2' not in text
    assert report['gates']['usb_wifi_netdev_present']=='PASS'
    assert report['gates']['lease_eligible']=='FAIL'
    assert report['gates']['redux-live.service']=='FAIL'
    assert report['radios'][0]['exclusion_checks']=='PASS'
    assert report['radios'][0]['authenticated_owner']=='UNKNOWN'
    assert report['radios'][0]['decoded_reception']=='UNKNOWN'
    assert set(report['pan'].values())=={'UNKNOWN'}


@pytest.mark.parametrize('error',[PermissionError('SECRET'),Unavailable('observation_changed'),
    ValueError('PRIVATE'),KeyError('PRIVATE'),TypeError('PRIVATE')])
def test_failed_inventory_never_uses_previous_results(error):
    def fail(*args,**kw):raise error
    r=a.report('192.0.2.1',run=fail,collector=fail)
    assert r['radios']==[]
    assert r['gates']['lease_eligible']=='UNKNOWN'
    assert 'PRIVATE' not in json.dumps(r) and 'SECRET' not in json.dumps(r)


def test_pan_failure_is_unknown_without_mutation_or_cleanup():
    calls=[]
    def denied(args):calls.append(args);raise Unavailable('private')
    r=a.report('192.0.2.1',run=denied,collector=supplier,
               pan=('/org/bluez/hci0/dev_02_00_00_00_00_01','192.0.2.3','/org/bluez/hci0'))
    assert set(r['pan'].values())=={'UNKNOWN'}
    assert not any('Connect' in c or 'Disconnect' in c or 'Pair' in c for c in calls)


def test_entrypoint_has_no_receipt_or_hardware_execution_flags(capsys):
    with pytest.raises(SystemExit):a.main(['--execute-approved'])
    assert '--execute-approved' not in (a.__doc__ or '')


def test_static_no_packet_or_mutation_backend_in_readonly_paths():
    for name in ('redux/acceptance.py','redux/radio/local_evidence.py','redux/connectivity/pan_preflight.py'):
        tree=ast.parse(Path(name).read_text())
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(n and ('bluez_executor' in n or 'live_runtime' in n) for n in imports)
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        assert not set(calls)&{'socket','set_type','restart','Pair','Connect','Disconnect','RegisterAgent'}
    # Receipt publishing is an explicit API, never reachable from acceptance.
    assert 'publish(' not in Path('redux/acceptance.py').read_text()


def launcher():
    import importlib.util
    spec=importlib.util.spec_from_file_location('pi_acceptance',Path('scripts/pi-acceptance.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_strict_ssh_memory_bundle_and_no_installation():
    module=launcher();args=module.command('192.0.2.1',Path('/private/key'))
    assert 'StrictHostKeyChecking=yes' in args and 'HostKeyAlgorithms=ssh-ed25519' in args
    assert 'sudo -n python3 -B - "$SSH_CONNECTION"'==args[-1]
    source=module.bundle()
    compile(source,'<synthetic-bundle>','exec')
    assert 'MemorySource' in source and 'open(' not in source
    assert 'publish(' not in source  # supplier code is encoded, never executed as provisioning


def test_bundle_contains_every_offline_import_and_no_installed_dependency(tmp_path):
    import subprocess
    import sys
    module=launcher();source=module.bundle()
    # Replace only final entry call with an assertion: execute all actual imports
    # in an empty cwd and isolated Python, exactly as the stdin bundle does.
    source=source.rsplit('main()',1)[0]+"assert main.__module__ == 'redux.acceptance'\n"
    result=subprocess.run([sys.executable,'-I','-B','-','192.0.2.1 1000 192.0.2.2 22'],
        input=source,text=True,capture_output=True,cwd=tmp_path,timeout=5)
    assert result.returncode==0,result.stderr
    assert not list(tmp_path.iterdir())


def test_launcher_does_not_publish_failed_or_private_backend_output():
    module=launcher()
    class Reader:
        def __init__(self,**kw):pass
        def __call__(self,*a,**kw):return 'PRIVATE invalid output'
    with pytest.raises(Unavailable,match='invalid_acceptance_response'):
        module.fetch(['ssh'],module.bundle(),reader_factory=Reader)


def test_usb_device_presence_counts_devices_without_exposing_identifiers(tmp_path):
    hub=tmp_path/'usb1';hub.mkdir();(hub/'bDeviceClass').write_text('09')
    device=tmp_path/'1-1';device.mkdir();(device/'bDeviceClass').write_text('00')
    interface=tmp_path/'1-1:1.0';interface.mkdir()
    (device/'serial').write_text('PRIVATE SERIAL NOT READ')
    assert a.usb_presence(tmp_path)=={'device_count_including_hubs':2,'inventory_stable':True}


def test_unknown_usb_descriptor_never_becomes_adapter_capability(tmp_path):
    device=tmp_path/'1-1';device.mkdir();(device/'bDeviceClass').write_text('INVALID')
    with pytest.raises(Unavailable,match='descriptor_invalid'):a.usb_presence(tmp_path)
