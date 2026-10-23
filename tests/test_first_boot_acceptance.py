"""First-boot image contracts and real producer recovery on temporary storage."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_root_payload import slot, gate
from test_hardware_acceptance import cli, args
from redux.acceptance_scenarios import scenario, NOW, TARGET
from redux.hardware_acceptance import evaluate


@pytest.mark.parametrize('unit,change',[
    # wrong_section misplaces RequiresMountsFor into [Service]; exercise it on
    # redux.service, which keeps that hard mount requirement. redux-live now uses
    # a soft captures.mount dependency so its diagnostics can report storage loss.
    ('redux.service','duplicate'),('redux.service','wrong_section'),
    ('redux.service','cycle'),('redux.service','display'),('redux-live.service','radio'),
    ('redux-capture-ingest.service','writable'),('redux-capture-ingest.service','capabilities'),
    ('redux-capture-ingest.timer','recovery_timer'),('redux.service','ownership'),
    ('redux-capture-ingest.service','missing'),('redux.service','dropin'),
])
def test_first_boot_dependencies_permissions_and_recovery_contracts(tmp_path,unit,change):
    root=slot(tmp_path);file=root/'etc/systemd/system'/unit;raw=file.read_text()
    if change=='duplicate':raw+='\n[Service]\nProtectSystem=false\n'
    elif change=='wrong_section':raw=raw.replace('RequiresMountsFor=/captures','').replace('[Service]','[Service]\nRequiresMountsFor=/captures')
    elif change=='cycle':raw=raw.replace('After=local-fs.target','After=local-fs.target\nBefore=local-fs.target')
    elif change=='display':raw=raw.replace('[Unit]','[Unit]\nRequires=redux-fbui.service')
    elif change=='radio':raw=raw.replace('[Unit]','[Unit]\nBindsTo=sys-subsystem-net-devices-wlan0.device')
    elif change=='writable':raw=raw.replace('ReadWritePaths=/captures','ReadWritePaths=/')
    elif change=='capabilities':raw=raw.replace('CapabilityBoundingSet=','CapabilityBoundingSet=CAP_NET_ADMIN')
    elif change=='recovery_timer':raw=raw.replace('OnUnitInactiveSec=20s','OnUnitInactiveSec=0')
    elif change=='ownership':raw=raw.replace('-o redux -g redux -m 0700','-o root -g root -m 0777')
    elif change=='missing':file.unlink()
    elif change=='dropin':
        directory=file.parent/(unit+'.d');directory.mkdir();(directory/'override.conf').write_text('[Service]\nUser=root\n')
    if change!='missing':file.write_text(raw)
    with pytest.raises(ValueError):gate.verify(root)


def test_actual_shipped_worker_matches_effective_image_policy(tmp_path):
    root=slot(tmp_path);repo=Path(__file__).resolve().parents[1]
    raw=(repo/'systemd/redux-capture-ingest.service').read_text()
    raw=raw.replace('[Unit]','[Unit]\nRequiresMountsFor=/captures').replace('[Service]','[Service]\nEnvironment=PYTHONPATH=/opt/redux')
    (root/'etc/systemd/system/redux-capture-ingest.service').write_text(raw)
    assert gate.verify(root)['root_payload_verified'] is True


def test_AB_and_runtime_services_share_one_ordering_graph(tmp_path):
    a=slot(tmp_path,'A','ab');b=slot(tmp_path,'B','ab')
    for name,dependency in (('redux.service','redux-ota-deadline.service'),('redux-ota-deadline.service','redux.service')):
        p=a/'etc/systemd/system'/name
        p.write_text('[Unit]\nBefore='+dependency+'\n'+p.read_text())
    with pytest.raises(ValueError,match='cycle'):gate.verify(a,profile='ab',other_root=b)


def test_offline_preflight_passes_software_but_never_physical_boot(cli,tmp_path,capsys):
    root=slot(tmp_path)
    assert cli.main(['--json','preflight','--root',str(root),'--profile','lite'])==3
    result=json.loads(capsys.readouterr().out)
    assert result['checks'][0]['status']=='PASS' and result['physical_boot_verified'] is False
    (root/'etc/fstab').write_text('ROOTDEV / ext4 defaults 0 1\n')
    assert cli.main(['preflight','--root',str(root),'--profile','lite'])==1


def test_capture_worker_failure_reopen_retry_and_evidence_remain_separate(tmp_path):
    from redux.crack.ingest import CaptureIngestor, Settings, read_summary
    from redux.crack.recovery import recovery_report
    from test_capture_recovery import fake_success, fake_failure
    incoming=tmp_path/'incoming';incoming.mkdir();source=incoming/'synthetic.pcap'
    source.write_bytes(b'SYNTHETIC TEST INPUT NOT RADIO CAPTURE');os.utime(source,(NOW-120,NOW-120))
    settings=Settings((incoming,),tmp_path/'ready',tmp_path/'state/jobs.db',settle_seconds=0)
    p=scenario();observation=next(r for r in p['payload']['observations'] if r['gate']=='processing')
    assert read_summary(settings.database)['available'] is False
    with CaptureIngestor(settings,runner=fake_failure,clock=lambda:NOW) as worker:
        failed=worker.scan()
    observation['data']['worker_scan']=read_summary(settings.database)['last_scan']
    assert evaluate(p,expected=TARGET,now=NOW,gates=['processing'])['result']=='FAIL'
    recovered=recovery_report(settings,apply=True,runner=fake_success,clock=lambda:NOW)
    assert recovered['outcomes']['ready']==1
    with CaptureIngestor(settings,runner=fake_success,clock=lambda:NOW) as worker:
        resumed=worker.scan();summary=worker.summary()
    assert source.read_bytes()==b'SYNTHETIC TEST INPUT NOT RADIO CAPTURE'
    observation['data'].update(handed_off=1,closed_artifacts=1,converted_artifacts=summary['by_status']['ready'],
        hash_records=summary['hash_records'],worker_scan=read_summary(settings.database)['last_scan'])
    result=evaluate(p,expected=TARGET,now=NOW,gates=['processing'])
    assert result['matrix'][0]['observation_status']=='PASS'
    assert result['result']=='UNKNOWN'  # converter and input are synthetic
    # Reopening the database must not replay an unverified physical event.
    assert read_summary(settings.database)['hash_records']==1


def test_sensor_disappearance_and_recovery_feed_actual_evaluator(tmp_path):
    from redux.core.telemetry import ResourceCollector
    from test_resource_telemetry import put
    clock=[NOW-60]
    collector=ResourceCollector(tmp_path,clock=lambda:clock[0],monotonic=lambda:clock[0],throttle_reader=lambda:0)
    assert collector.sample().reading.cpu_temp_c is None
    put(tmp_path,'proc/device-tree/model','Raspberry Pi 4 Model B\0')
    put(tmp_path,'sys/class/thermal/thermal_zone0/type','cpu-thermal')
    put(tmp_path,'sys/class/thermal/thermal_zone0/temp','55000')
    put(tmp_path,'sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq','1500000')
    put(tmp_path,'proc/meminfo','MemTotal: 1000 kB\nMemAvailable: 600 kB\n')
    put(tmp_path,'proc/stat','cpu 10 0 10 80 0 0 0 0\n')
    clock[0]+=2;assert collector.sample().reading.cpu_load_pct is None
    samples=[]
    for advance,ticks in ((28,'cpu 40 0 40 120 0 0 0 0\n'),(30,'cpu 70 0 70 160 0 0 0 0\n')):
        clock[0]+=advance;put(tmp_path,'proc/stat',ticks);collector.sample();status=collector.status()
        samples.append(dict(measured_at=status['measured_at'],**{k:status['values'][k] for k in
            ('cpu_temp_c','cpu_load_pct','ram_pct','cpu_frequency_mhz','throttled_mask')}))
    p=scenario();resource=next(r for r in p['payload']['observations'] if r['gate']=='resources');resource['data']['samples']=samples
    r=evaluate(p,expected=TARGET,now=NOW,gates=['resources'])
    assert r['matrix'][0]['observation_status']=='PASS' and r['result']=='UNKNOWN'
    (tmp_path/'sys/class/thermal/thermal_zone0/temp').unlink();clock[0]+=2;collector.sample()
    samples[-1]['cpu_temp_c']=collector.status()['values']['cpu_temp_c']
    assert evaluate(p,expected=TARGET,now=NOW,gates=['resources'])['matrix'][0]['observation_status']=='UNKNOWN'
    put(tmp_path,'sys/class/thermal/thermal_zone0/temp','54000');clock[0]+=2
    assert collector.sample().reading.cpu_temp_c==54
