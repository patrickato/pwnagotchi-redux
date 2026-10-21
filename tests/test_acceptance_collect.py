"""Injected local acquisition fixtures; never invoke collection on a Pi."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from redux import acceptance_collect as acquisition
from redux.hardware_acceptance import evaluate, validate
from redux.acceptance_scenarios import scenario, NOW, TARGET
from test_hardware_acceptance import cli


@pytest.fixture
def local(tmp_path):
    clock=[NOW-32]
    def put(name,value):
        p=tmp_path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
    put('proc/device-tree/model','Raspberry Pi 4 Model B\0')
    put('usr/share/redux/redux-revision',TARGET['revision'])
    put('proc/sys/kernel/random/boot_id','11111111-2222-3333-4444-555555555555')
    seed=scenario();data={r['gate']:r['data'] for r in seed['payload']['observations']}
    calls=[]
    def cached():
        calls.append(clock[0])
        return {'runtime':{'state':'degraded','updated_utc':clock[0]},
            'capture_processing':{'last_scan':{'scanned':1,'outcomes':{'ready':1},'completed_utc':clock[0],'secret':'SSID-do-not-copy'}},
            'telemetry':{'measured_at':clock[0],'values':dict(cpu_temp_c=55,cpu_load_pct=60,ram_pct=40,cpu_frequency_mhz=1500,throttled_mask=0)}}
    def service(unit,expectation):return {'evidence':deepcopy(data['services']['units'][unit])}
    options=dict(root=tmp_path,clock=lambda:clock[0],monotonic=lambda:clock[0],
        sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds),status_supplier=cached,
        service=service,storage=lambda root:deepcopy(data['storage']),architecture=lambda:'aarch64')
    return tmp_path,clock,calls,options


def test_acquisition_reuses_cached_producers_is_bounded_unsigned_and_read_only(local,monkeypatch):
    root,clock,calls,options=local
    from redux.core.telemetry import ResourceCollector
    monkeypatch.setattr(ResourceCollector,'sample',lambda *a:pytest.fail('duplicate sensor collector'))
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    package=acquisition.collect(TARGET,'7'*64,seconds=32,**options)
    validate(package)
    assert len(calls)==17 and len(package['payload']['observations'])==4
    assert not package.get('signature')
    assert 'secret' not in json.dumps(package) and 'SSID' not in json.dumps(package)
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    report=evaluate(package,expected=TARGET,now=clock[0])
    assert not report['physical_attestation_complete']
    assert all(r['status']=='UNKNOWN' for r in report['matrix'])
    assert next(r for r in report['matrix'] if r['gate']=='resources')['observation_status']=='PASS'


@pytest.mark.parametrize('failure',['dashboard','storage','services','partial','stale','malicious'])
def test_independent_producer_failures_do_not_abandon_acquisition(local,failure):
    root,clock,calls,options=local
    original=options['status_supplier']
    def unavailable(*args):raise OSError('private text must not appear')
    if failure=='dashboard':options['status_supplier']=unavailable
    elif failure=='storage':options['storage']=unavailable
    elif failure=='services':options['service']=unavailable
    else:
        def supplier():
            value=original()
            if failure=='partial':value['telemetry']['values']=None
            elif failure=='stale':value['telemetry']['measured_at']-=1000
            else:
                value['telemetry']['values']['cpu_temp_c']='SSID-secret'
                value['runtime']['state']='SSID-secret'
                value['capture_processing']['last_scan']['outcomes']={'SSID-secret':1}
            return value
        options['status_supplier']=supplier
    p=acquisition.collect(TARGET,'7'*64,seconds=2,**options)
    report=evaluate(p,expected=TARGET,now=clock[0])
    assert not report['physical_attestation_complete']
    assert 'SSID' not in json.dumps(p) and 'private text' not in json.dumps(p)
    assert len(p['payload']['observations'])>=2


@pytest.mark.parametrize('case',['non_pi','wrong_revision','boot_changed','revision_changed','budget','session'])
def test_wrong_host_source_or_boot_and_unbounded_acquisition_rejected(local,case):
    root,clock,calls,options=local;seconds=2;session='7'*64
    if case=='non_pi':(root/'proc/device-tree/model').write_text('Development host')
    elif case=='wrong_revision':(root/'usr/share/redux/redux-revision').write_text('0'*40)
    elif case in ('boot_changed','revision_changed'):
        def sleep(seconds):
            clock[0]+=seconds
            path='proc/sys/kernel/random/boot_id' if case=='boot_changed' else 'usr/share/redux/redux-revision'
            (root/path).write_text('0'*40)
        options['sleep']=sleep
    elif case=='budget':seconds=1000
    else:session='invalid'
    with pytest.raises(ValueError):acquisition.collect(TARGET,session,seconds=seconds,**options)


def test_explicit_collect_cli_only_writes_stdout_and_remains_unknown(cli,monkeypatch,capsys):
    calls=[]
    def collect(*a,**k):calls.append((a,k));return scenario()
    monkeypatch.setattr(acquisition,'collect',collect)
    params=['collect-local','--revision',TARGET['revision'],'--release-sha256',TARGET['release_sha256'],
        '--device-id',TARGET['device_id'],'--model','pi4','--profile','ab','--session-id','7'*64]
    assert cli.main(params)==3
    output=capsys.readouterr();assert json.loads(output.out)['payload']['origin']=='synthetic'
    assert len(calls)==1 and 'UNKNOWN' in output.err
    with pytest.raises(SystemExit):cli.main(['--report','forbidden.json']+params)
    assert len(calls)==1


def test_loopback_status_reader_has_fixed_address_no_proxy_redirect_and_bounded_bytes(monkeypatch):
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):seen.append(limit);return b'{}'
    class Opener:
        def open(self,url,timeout):seen.extend((url,timeout));return Response()
    def build(*handlers):
        assert handlers[0].proxies=={}
        assert handlers[1].redirect_request(None,None,None,None,None,None) is None
        return Opener()
    monkeypatch.setattr(acquisition.urllib.request,'build_opener',build)
    assert acquisition.cached_status()=={}
    assert seen==['http://127.0.0.1:8080/api/status',2,65537]
