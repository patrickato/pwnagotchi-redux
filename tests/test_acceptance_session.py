"""Offline bring-up sessions. Fake files/components; never contact hardware."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
import pytest
from redux.acceptance_session import remote_snapshot, capture_progress

ROOT=Path(__file__).resolve().parents[1]


def launcher():
    spec=importlib.util.spec_from_file_location('session_cli',ROOT/'scripts/redux-acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fake_root(root):
    values={'usr/share/redux/redux-revision':'a'*40,
            'proc/sys/kernel/random/boot_id':'12345678-1234-1234-1234-123456789abc',
            'proc/device-tree/model':'Raspberry Pi 4 Model B Rev 1.5',
            'proc/self/mountinfo':'1 0 0:1 / / rw - overlay overlay rw\n',
            'sys/class/net/wlan0/ifindex':'3','sys/class/net/wlan0/type':'1',
            'sys/class/net/wlan0/statistics/rx_packets':'20',
            'sys/class/net/wlan0/statistics/tx_packets':'2'}
    for name,value in values.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
    (root/'captures').mkdir()


def snapshot(root, **options):
    return remote_snapshot('192.0.2.1','1'*64,'b'*40,root=root,runner=lambda args:'firmware version',
        display_supplier=lambda:{'framebuffers':[],'input_devices':[],'spi_devices':[]},
        radio_supplier=lambda *args,**kw:{'gates':{},'radios':[]},clock=lambda:100,**options)


def test_host_plan_is_offline_and_never_qualifies(capsys):
    module=launcher()
    assert module.main(['--json','session-plan'])==3
    plan=json.loads(capsys.readouterr().out)
    assert plan['physical_acceptance']=='UNKNOWN'
    assert plan['requires_operator_authorization'] is True
    assert plan['ssh']['sudo'] is False
    assert len(plan['collector_sha256'])==64


def test_extended_memory_bundle_imports_without_installed_source(tmp_path):
    _,_,source=launcher().session_support()
    prefix=source.split('import json, os, sys\n',1)[0]
    # -I plus a different cwd excludes this checkout and any installed Redux.
    # Import modules used only when remote_snapshot executes. Merely importing
    # the snapshot module missed transitive kiosk/HTTP dependencies and allowed
    # a valid SSH login to fail before producing a session JSON response.
    program=(prefix+'\nimport redux.acceptance_session, redux.session_helpers, redux.acceptance\n'
             'import redux.boot_readiness, redux.display.acceptance, redux.display.local_http\n'
             'print("MEMORY_OK")\n')
    result=subprocess.run([sys.executable,'-I','-B','-'],input=program,text=True,capture_output=True,cwd=tmp_path,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='MEMORY_OK'


def test_current_host_cannot_replace_installed_revision(tmp_path):
    fake_root(tmp_path)
    report=snapshot(tmp_path)
    assert report['embedded_revision']=='a'*40
    assert report['host_revision']=='b'*40
    assert report['identity_stable'] is True
    assert report['physical_acceptance']==report['release_identity']==report['card_identity']=='UNKNOWN'
    assert report['display']['touch_verified'] is False
    assert report['firmware']['compatibility']=='UNKNOWN'
    assert not report['processing']['available']


@pytest.mark.parametrize('filename,value',[('usr/share/redux/redux-revision','invalid'),('proc/sys/kernel/random/boot_id','invalid'),('proc/device-tree/model','not a Pi')])
def test_missing_or_malformed_identity_is_not_fabricated(tmp_path,filename,value):
    fake_root(tmp_path);(tmp_path/filename).write_text(value)
    report=snapshot(tmp_path)
    if filename.endswith('redux-revision'):assert report['embedded_revision'] is None
    if filename.endswith('boot_id'):assert report['identity_stable'] is False
    if filename.endswith('model'):assert report['model'] is None
    assert report['physical_acceptance']=='UNKNOWN'


def test_independent_status_failure_preserves_other_probes(tmp_path):
    fake_root(tmp_path)
    def unavailable():raise OSError('PRIVATE error')
    report=snapshot(tmp_path,status_supplier=unavailable)
    assert report['status']=={}
    assert report['model']=='pi4'
    assert report['firmware']['firmware_version_sha256']
    assert 'PRIVATE' not in json.dumps(report)


@pytest.mark.parametrize('worker',[
    {'last_scan':{'scanned':1,'completed_utc':99,'outcomes':{'PRIVATE_SSID':1}}},
    {'by_status':{'PRIVATE_SSID':1},'artifacts':1},
    {'last_scan':{'scanned':0,'completed_utc':1,'outcomes':{}}},
    {'last_scan':{'scanned':0,'completed_utc':101,'outcomes':{}}}])
def test_worker_redaction_and_freshness(worker):
    value=capture_progress({'capture_processing':worker},1000)
    assert not value['fresh']
    assert 'PRIVATE' not in json.dumps(value)


def test_worker_scan_is_not_an_artifact(tmp_path):
    value=capture_progress({'capture_processing':{'available':True,'artifacts':0,'by_status':{},
        'last_scan':{'scanned':0,'completed_utc':99,'outcomes':{}}}},100)
    assert value['fresh'] and value['artifact_count']==0


def test_session_authorization_flag_required(capsys):
    with pytest.raises(SystemExit):launcher().main(['session-collect','--host','192.168.1.2','--output','unused.json'])
    assert 'authorize-read-only' in capsys.readouterr().err


def test_existing_output_refused_before_any_connection(tmp_path, monkeypatch):
    module=launcher();output=tmp_path/'existing.json';output.write_text('preserve')
    monkeypatch.setattr(module,'session_support',lambda:pytest.fail('must not prepare or connect'))
    assert module.main(['session-collect','--host','192.168.1.2','--output',str(output),'--authorize-read-only'])==1
    assert output.read_text()=='preserve'


def test_passive_rx_counts_never_prove_decoded_reception(tmp_path, monkeypatch):
    fake_root(tmp_path)
    monkeypatch.setattr('redux.radio.local_evidence.collect',lambda *args,**kw:{'radios':[{'iface':'wlan0'}]})
    def radio(peer,**kwargs):
        kwargs['collector'](peer)
        return {'gates':{},'radios':[]}
    def sleep(seconds):
        assert seconds==1
        (tmp_path/'sys/class/net/wlan0/statistics/rx_packets').write_text('25')
    report=remote_snapshot('192.0.2.1','1'*64,'b'*40,root=tmp_path,runner=lambda args:'',
        radio_supplier=radio,display_supplier=lambda:{},sleep=sleep)
    rx=report['rx_observations'][0]
    assert rx['arphrd']==1 and rx['rx_delta']==5 and rx['tx_delta']==0
    assert rx['decoded_reception']=='UNKNOWN'
    assert 'wlan0' not in json.dumps(report)


def test_save_is_private_and_refuses_overwrite(tmp_path):
    module=launcher();path=tmp_path/'report.json';module.save(path,{'result':'UNKNOWN'})
    assert path.stat().st_mode&0o777==0o600
    with pytest.raises(FileExistsError):module.save(path,{})


def test_saved_session_cannot_become_physical_pass(tmp_path):
    fake_root(tmp_path);report=snapshot(tmp_path);module=launcher()
    value=module.session_inspect(report,now=100)
    assert value['result']==value['physical_acceptance']=='UNKNOWN'
    assert value['embedded_revision']!=value['host_revision']
    for key in ('physical_acceptance','release_identity','card_identity','update_rollback'):
        forged={**report,key:'PASS'}
        with pytest.raises(ValueError):module.session_inspect(forged,now=100)


def test_imported_stale_or_future_sessions_do_not_refresh_checks(tmp_path):
    fake_root(tmp_path);report=snapshot(tmp_path);module=launcher()
    for now in (99,1000):
        value=module.session_inspect(report,now=now)
        assert value['checks'][0]['status']=='UNKNOWN'
        assert value['result']=='UNKNOWN'


def test_storage_capacity_does_not_fall_back_to_root(tmp_path):
    fake_root(tmp_path)
    report=snapshot(tmp_path)
    assert report['storage']['captures']['status']=='action'
    assert report['storage']['root']['status']=='ok'
    assert report['storage']['available_bytes'] is None


def test_reboot_during_acquisition_invalidates_identity(tmp_path):
    fake_root(tmp_path)
    def cached():
        (tmp_path/'proc/sys/kernel/random/boot_id').write_text('22345678-1234-1234-1234-123456789abc')
        return {}
    report=snapshot(tmp_path,status_supplier=cached)
    assert report['identity_stable'] is False
    value=launcher().session_inspect(report,now=100)
    assert all(row['status']=='UNKNOWN' for row in value['checks'][1:])


def test_session_transport_is_strict_bounded_and_never_sudo(tmp_path, monkeypatch):
    from types import SimpleNamespace
    module=launcher();fake_root(tmp_path/'pi');record=snapshot(tmp_path/'pi')
    identity=tmp_path/'identity';identity.write_text('test placeholder')
    _,field,source=module.session_support()
    seen=[]
    def fetch(argv,program,format_name):
        seen.append(argv)
        session,revision=argv[-1].split()[-2:]
        return {**record,'session_id':session,'host_revision':revision}
    monkeypatch.setattr(module,'session_support',lambda:(SimpleNamespace(fetch=fetch),field,source))
    output=tmp_path/'session.json'
    result=module.session_collect(SimpleNamespace(host='192.168.1.2',key=identity,output=output))
    assert result['result']=='UNKNOWN' and result['network_actions_performed'] is True
    argv=seen[0]
    assert argv[0:3]==['ssh','-F','/dev/null']
    for option in ('StrictHostKeyChecking=yes','BatchMode=yes','PasswordAuthentication=no','ForwardAgent=no','UpdateHostKeys=no','ProxyCommand=none'):
        assert option in argv
    assert 'sudo' not in ' '.join(argv)
    assert 'python3 -B - ' in argv[-1]
    saved=json.loads(output.read_text())
    assert saved['embedded_revision']=='a'*40
    assert saved['collector_sha256']
    assert '192.168' not in output.read_text()


def test_emit_does_not_call_actual_ssh_session_offline(capsys):
    from types import SimpleNamespace
    module=launcher()
    assert module.emit({'result':'UNKNOWN','network_actions_performed':True},SimpleNamespace(report=None,json=False))==3
    text=capsys.readouterr().out
    assert 'Read-only SSH session' in text and 'Offline only' not in text


def test_display_inventory_retains_dimensions_without_device_paths(tmp_path):
    fake_root(tmp_path)
    report=remote_snapshot('192.0.2.1','1'*64,'b'*40,root=tmp_path,runner=lambda args:'',
        radio_supplier=lambda *a,**kw:{},display_supplier=lambda:{
            'framebuffers':[{'device':'/dev/fb1','virtual_size':'320,480','bits_per_pixel':'16','stride':'640'}],
            'input_devices':[],'spi_devices':[]})
    assert report['display']['framebuffers'][0]['dimensions']=='320,480'
    assert '/dev/fb1' not in json.dumps(report)
    assert report['display']['physical_display_verified'] is False


def test_readable_session_projection_excludes_unrecognized_text(tmp_path):
    fake_root(tmp_path);record=snapshot(tmp_path)
    record['firmware']['PRIVATE']='PRIVATE'
    record['rx_observations']=[{'radio':1,'arphrd':1,'rx_delta':4,'tx_delta':0,'secret':'PRIVATE'}]
    record['kernel']='bad kernel PRIVATE'
    result=launcher().session_inspect(record,now=100)
    assert 'PRIVATE' not in json.dumps(result)
    assert result['observations']['radios'][0]['arphrd']==1
    assert result['observations']['kernel'] is None


def test_transport_failure_is_bounded_unknown_without_traceback_or_raw_stderr(tmp_path,monkeypatch,capsys):
    from types import SimpleNamespace
    from redux.radio.local_evidence import Unavailable
    module=launcher();identity=tmp_path/'key';identity.write_text('placeholder')
    _,field,source=module.session_support()
    def failure(*args,**kw):raise Unavailable('PRIVATE stderr')
    monkeypatch.setattr(module,'session_support',lambda:(SimpleNamespace(fetch=failure),field,source))
    output=tmp_path/'new.json'
    assert module.main(['session-collect','--host','192.168.1.2','--key',str(identity),
                        '--output',str(output),'--authorize-read-only'])==3
    text=capsys.readouterr().out
    assert 'UNKNOWN' in text and 'host_key_identity_no_retry' in text
    assert 'PRIVATE' not in text and not output.exists()


def test_unavailable_mount_inventory_is_unknown_not_proven_missing_mount(tmp_path):
    fake_root(tmp_path);(tmp_path/'proc/self/mountinfo').unlink()
    report=snapshot(tmp_path)
    assert report['storage']=={'availability':'unknown'}


def test_failed_radio_inventory_discards_partial_counter_results(tmp_path,monkeypatch):
    fake_root(tmp_path)
    monkeypatch.setattr('redux.radio.local_evidence.collect',lambda *args,**kw:{'radios':[{'iface':'wlan0'}]})
    def partial(peer,**kwargs):
        kwargs['collector'](peer)
        return {'inventory_status':'permission_denied','radios':[]}
    report=remote_snapshot('192.0.2.1','1'*64,'b'*40,root=tmp_path,runner=lambda args:'',
        radio_supplier=partial,display_supplier=lambda:{},sleep=lambda seconds:None)
    assert report['rx_observations']==[]


def test_full_memory_collector_executes_without_installed_redux(tmp_path):
    """Run the complete entrypoint, not merely imports, in isolated Linux Python.

    An unavailable local Pi/sysfs/status producer is UNKNOWN, not a traceback.
    This test never reaches an SSH host or samples remote hardware.
    """
    import os
    _, _, source = launcher().session_support()
    environment = {
        "SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22",
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LC_ALL": "C",
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-", "1" * 64, "b" * 40],
        input=source, text=True, capture_output=True, cwd=tmp_path,
        env=environment, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report.get("collector_error") is None, report.get("collector_error")
    assert report["format"] == "redux-bringup-session-v1"
    assert report["session_id"] == "1" * 64
    assert report["host_revision"] == "b" * 40
    assert report["hardware_changes"] is False
    assert report["physical_acceptance"] == "UNKNOWN"
    assert report["card_identity"] == "UNKNOWN"


def test_remote_collector_failure_is_classified_without_raw_error(tmp_path, monkeypatch):
    from types import SimpleNamespace
    module = launcher()
    _, field, source = module.session_support()
    identity = tmp_path / "key"
    identity.write_text("placeholder")

    def remote_error(argv, program, format_name):
        session, revision = argv[-1].split()[-2:]
        return {"format": format_name, "hardware_changes": False,
                "session_id": session, "host_revision": revision,
                "collector_error": "ModuleNotFoundError"}

    monkeypatch.setattr(module, "session_support",
                        lambda: (SimpleNamespace(fetch=remote_error), field, source))
    output = tmp_path / "private-report.json"
    result = module.session_collect(
        SimpleNamespace(host="192.168.1.2", key=identity, output=output))
    assert result["result"] == "UNKNOWN"
    assert result["session_report_saved"] is False
    assert result["checks"] == [{
        "gate": "remote_collector", "status": "UNKNOWN",
        "reason": "remote_ModuleNotFoundError",
    }]
    assert not output.exists()


def test_failed_ssh_process_is_distinct_from_remote_python_fault(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from redux.radio.local_evidence import Unavailable
    module = launcher()
    _, field, source = module.session_support()
    identity = tmp_path / "key"
    identity.write_text("placeholder")

    def failed_process(*args, **kwargs):
        raise Unavailable("command_failed")

    monkeypatch.setattr(module, "session_support",
                        lambda: (SimpleNamespace(fetch=failed_process), field, source))
    result = module.session_collect(SimpleNamespace(
        host="192.168.1.2", key=identity, output=tmp_path / "result.json"))
    assert result["result"] == "UNKNOWN"
    assert result["checks"] == [{
        "gate": "read_only_transport", "status": "UNKNOWN",
        "reason": "ssh_or_remote_process_failed",
    }]
    assert not (tmp_path / "result.json").exists()


def test_memory_dashboard_helper_decodes_successful_cached_status_offline(tmp_path):
    """Exercise the successful Pi dashboard branch in an isolated interpreter.

    A host without the loopback dashboard only tests the error path, so missing
    imports such as json in the in-memory helper would previously go unnoticed.
    This uses a fake opener and does not connect to anything.
    """
    _, _, source = launcher().session_support()
    prefix = source.split('import json, os, sys\n', 1)[0]
    program = prefix + '''
import io, urllib.request
import redux.session_helpers as helper

class StubOpener:
    def open(self, url, *, timeout):
        assert url == 'http://127.0.0.1:8080/api/status'
        assert timeout == 2
        return io.BytesIO(b'{"runtime":{"state":"running"}}')

urllib.request.build_opener = lambda *args: StubOpener()
status = helper.cached_status()
assert status['runtime']['state'] == 'running'
print('DASHBOARD_OK')
'''
    run = subprocess.run(
        [sys.executable, "-I", "-B", "-"], input=program, text=True,
        capture_output=True, cwd=tmp_path, timeout=10, check=False)
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "DASHBOARD_OK"


def test_inspect_existing_session_surfaces_safe_hardware_blockers(tmp_path):
    """Saved data can explain TFT/radio/storage blockers without another Pi read."""
    fake_root(tmp_path)
    record=snapshot(tmp_path)
    record['display'].update(
        state='BLOCKED',
        blockers=['FRAMEBUFFER MISSING','TOUCH DEVICE MISSING'],
        framebuffer_candidates=0, ili9486_candidates=1, touch_candidates=0,
        framebuffers=[{'ordinal':1,'dimensions':'1920,1080',
                       'bits_per_pixel':'32','stride':'7680',
                       'device':'/dev/fbPRIVATE'}])
    record['radio']={'inventory_status':'fresh','radios':[
        {'radio':1,'transport':'onboard','kernel_mode':'monitor','arphrd':1,
         'nm_managed':True,'nm_state':20,'identity_stable':True,
         'iface':'PRIVATE_WLAN','driver':'PRIVATE'},
        {'radio':2,'transport':'usb','kernel_mode':'monitor','arphrd':803,
         'usb_ancestry':True,'identity_stable':True,'iface':'PRIVATE_USB'}]}
    record['storage']['captures']={
        'status':'ok','filesystem':'ext4','device_major_minor':'PRIVATE_DEVICE'}
    record['storage']['available_bytes']=128*1024*1024
    record['processing']={'available':True,'fresh':False,'artifact_count':4,
                          'capture_paths':['PRIVATE_CAPTURE']}
    record['plugins']={'available':False,'secret':'PRIVATE_PLUGIN'}
    record['status']={'acceptance':{
        'checkpoint_freshness':'fresh','live_state':'running','events_seen':'42',
        'network_identifiers':'PRIVATE_AP'}}
    summary=launcher().session_inspect(record,now=100)
    ob=summary['observations']
    assert summary['result']=='UNKNOWN'
    assert ob['display_blockers']==['FRAMEBUFFER MISSING','TOUCH DEVICE MISSING']
    assert ob['framebuffer_metadata'][0]['dimensions']=='1920,1080'
    assert ob['storage']['captures']=={'status':'ok','filesystem':'ext4'}
    assert ob['captures_available_mib']==128
    assert ob['radio_inventory_details'][0]['transport']=='onboard'
    assert ob['radio_inventory_details'][1]['transport']=='usb'
    assert ob['radio_inventory_details'][1]['arphrd']==803
    assert ob['capture_processing']['artifact_count']==4
    assert ob['plugin_inventory_available'] is False
    assert ob['live_runtime']['events_seen']==42
    assert 'PRIVATE' not in json.dumps(summary)


def test_saved_session_detail_rejects_untrusted_free_text(tmp_path):
    fake_root(tmp_path)
    record=snapshot(tmp_path)
    record['display']={'state':'BLOCKED','blockers':['PRIVATE_REASON'],
                       'framebuffer_candidates':100000,'framebuffers':[
                           {'dimensions':'PRIVATE_RESOLUTION'}]}
    record['storage']={'captures':{'status':'PRIVATE','filesystem':'PRIVATE'},
                       'available_bytes':-1}
    record['radio']={'inventory_status':'PRIVATE','radios':[
        {'transport':'PRIVATE','kernel_mode':'PRIVATE','arphrd':-1,
         'iface':'PRIVATE_IDENTIFIER'}]}
    record['status']={'acceptance':{
        'live_state':'PRIVATE','checkpoint_freshness':'PRIVATE',
        'events_seen':'9'*1000}}
    observed=launcher().session_inspect(record,now=100)['observations']
    assert observed['display_blockers']==[]
    assert observed['display_candidates']['framebuffer_candidates'] is None
    assert observed['framebuffer_metadata'][0]['dimensions'] is None
    assert observed['radio_inventory_status'] is None
    assert observed['radio_inventory_details'][0]['transport'] is None
    assert observed['captures_available_mib'] is None
    assert observed['live_runtime']['events_seen'] is None
    assert 'PRIVATE' not in json.dumps(observed)


def test_saved_session_exposes_ledger_status_groups_not_capture_proof(tmp_path):
    """Stored aggregate ledger facts are available without opening a capture."""
    fake_root(tmp_path)
    report=snapshot(tmp_path)
    report['processing']={
        'available':True,'fresh':True,'artifact_count':5,
        'by_status':{'ready':2,'invalid':1,'duplicate':1,'error':1},
        'scan':{'scanned':2,'completed_utc':100,
                'outcomes':{'ready':1,'invalid':1}},
    }
    projection=launcher().session_inspect(report,now=100)
    capture=projection['observations']['capture_processing']
    assert projection['physical_acceptance']=='UNKNOWN'
    assert capture['fresh_at_collection'] is True
    assert capture['artifact_count']==5
    assert capture['by_status']=={
        'duplicate':1,'error':1,'invalid':1,'ready':2}
    assert capture['last_scan']=={
        'scanned':2,'outcomes':{'invalid':1,'ready':1}}


@pytest.mark.parametrize('groups',[
    {'ready':5,'PRIVATE_NETWORK':1},
    {'ready':True,'invalid':4},
    {'ready':6},
    {'ready':-1,'error':6},
])
def test_saved_session_rejects_untrusted_ledger_breakdown(tmp_path, groups):
    fake_root(tmp_path)
    report=snapshot(tmp_path)
    report['processing']={
        'available':True,'fresh':True,'artifact_count':5,'by_status':groups,
        'scan':{'scanned':1,'outcomes':{'PRIVATE_NETWORK':1}},
    }
    projected=launcher().session_inspect(report,now=100)
    facts=projected['observations']['capture_processing']
    assert facts['artifact_count']==5
    assert facts['by_status'] is None
    assert facts['last_scan'] is None
    assert 'PRIVATE_NETWORK' not in json.dumps(projected)
