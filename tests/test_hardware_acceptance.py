"""Synthetic offline evidence only. No device, network, RF or physical claims."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from redux import hardware_acceptance as gate
from redux.acceptance_scenarios import scenario, NOW, TARGET

ROOT=Path(__file__).resolve().parents[1]


def row(package, name):return next(r for r in package['payload']['observations'] if r['gate']==name)


@pytest.fixture
def signer():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key=Ed25519PrivateKey.generate()  # isolated, memory-only TEST key
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw).hex()
    policy=dict(schema=1,target=deepcopy(TARGET),session_id='7'*64,collector_sha256='8'*64,public_key=public,
        authorized_from=NOW-3600,authorized_until=NOW+300,max_age_seconds=300,gate_scope=list(gate.GATES),
        firmware_sha256=['f'*64],eeprom_sha256=['c'*64])
    def sign(package):
        package['signature']=key.sign(gate.canonical(package['payload'])).hex()
        return package
    return sign,policy


def qualified(signer, package=None, **kwargs):
    sign,policy=signer
    package=package or scenario()
    package['payload']['origin']='physical'  # hypothetical attestation, NOT a measurement
    sign(package)
    return gate.evaluate(package,expected=TARGET,now=NOW,policy=policy,**kwargs)


def statuses(report):return {r['gate']:r['status'] for r in report['matrix']}


def test_synthetic_healthy_is_never_physical_pass_even_with_trusted_test_signature(signer):
    sign,policy=signer;p=sign(scenario())
    r=gate.evaluate(p,expected=TARGET,now=NOW,policy=policy,authenticated_bundle_sha256='9'*64)
    assert set(statuses(r).values())=={'UNKNOWN'}
    assert not r['physical_attestation_complete'] and not r['production_ready']
    assert all(x['observation_status']=='PASS' for x in r['matrix'])


def test_hypothetical_scoped_attestation_and_independent_bundle_gates(signer):
    r=qualified(signer)
    assert all(statuses(r)[g]=='PASS' for g in gate.GATES if g not in gate.UPDATE)
    assert all(statuses(r)[g]=='UNKNOWN' for g in gate.UPDATE)
    assert not r['physical_attestation_complete']
    r=qualified(signer,authenticated_bundle_sha256='9'*64)
    assert set(statuses(r).values())=={'PASS'}
    assert r['physical_attestation_complete'] and not r['production_ready']


@pytest.mark.parametrize('name,affected,observation',[
    ('healthy','radio','PASS'),('no_radio','radio','UNKNOWN'),
    ('missing_captures','storage','FAIL'),('stale_sensors','resources','UNKNOWN'),
    ('display_mapping','display','FAIL'),('broken_processing','processing','FAIL'),
    ('ethernet_framing','radio','FAIL'),('interrupted_update','trial_boot','UNKNOWN'),
    ('ambiguous_rollback','rollback','UNKNOWN')])
def test_actual_evaluator_replays_multisystem_scenarios(name,affected,observation):
    p=scenario(name);r=gate.evaluate(p,expected=TARGET,now=NOW)
    found=next(x for x in r['matrix'] if x['gate']==affected)
    assert found['observation_status']==observation
    assert 'PASS' not in statuses(r).values()
    if name=='no_radio':
        assert next(x for x in r['matrix'] if x['gate']=='services')['observation_status']=='PASS'


@pytest.mark.parametrize('case',['no_policy','unsigned','wrong_key','tampered','expired','future','wrong_session','wrong_collector','scope','historical'])
def test_forged_or_unauthorized_success_stays_unknown(signer,case):
    sign,policy=signer;p=scenario();p['payload']['origin']='physical';sign(p)
    gates=['radio']
    if case=='no_policy':policy=None
    elif case=='unsigned':p.pop('signature')
    elif case=='wrong_key':policy['public_key']='0'*64
    elif case=='tampered':row(p,'radio')['data']['rx_after']+=1
    elif case=='expired':policy['authorized_until']=NOW-1
    elif case=='future':policy['authorized_from']=NOW+1
    elif case=='wrong_session':policy['session_id']='0'*64
    elif case=='wrong_collector':policy['collector_sha256']='0'*64
    elif case=='scope':policy['gate_scope']=['storage']
    elif case=='historical':p['payload']['origin']='historical';sign(p)
    r=gate.evaluate(p,expected=TARGET,now=NOW,policy=policy,gates=gates)
    assert statuses(r)=={'radio':'UNKNOWN'}


@pytest.mark.parametrize('key,value',[('revision','0'*40),('device_id','0'*64),('release_sha256','0'*64),('model','pi5'),('profile','lite')])
def test_wrong_revision_release_model_profile_or_device_rejected(signer,key,value):
    p=scenario();expected=dict(TARGET);expected[key]=value
    r=gate.evaluate(p,expected=expected,now=NOW,gates=['radio'])
    assert r['result']=='FAIL' and r['matrix'][0]['reason']=='wrong_target'


@pytest.mark.parametrize('case',['cross_device','cross_revision','cross_boot','duplicate','oversized','deep','unknown_field','fake_success'])
def test_malformed_contradictory_or_oversized_envelopes(case):
    p=scenario()
    if case=='cross_device':p['payload']['observations'][0]['target']['device_id']='0'*64
    elif case=='cross_revision':p['payload']['observations'][0]['target']['revision']='0'*40
    elif case=='cross_boot':row(p,'radio')['boot_id']='0'*64
    elif case=='duplicate':p['payload']['observations'].append(deepcopy(p['payload']['observations'][0]))
    elif case=='oversized':row(p,'storage')['data']['mountinfo']='x'*150000
    elif case=='deep':
        value={};row(p,'radio')['data']['bad']=value
        for _ in range(20):value['nested']={};value=value['nested']
    elif case=='unknown_field':p['trust_policy']={'trusted':True}
    else:p['payload']['observations'][0]['PASS']=True
    with pytest.raises(ValueError):gate.evaluate(p,expected=TARGET,now=NOW)


@pytest.mark.parametrize('gate_name,key,value',[
    ('storage','label_major_minor','179:2'),('storage','boot_uid',0),('storage','boot_mode',0o777),
    ('radio','rx_after',5),('radio','decoded_rx_frames',500),('radio','tx_after',1),
    ('radio','arphrd',True),('radio','header_classification','bad_fcs'),
    ('processing','converted_artifacts',10),('processing','handed_off',-1),
    ('display','points',[]),('display','navigation',['next']),
    ('resources','samples',[]),('persistence','after_inventory','0'*64),
    ('persistence','after_boot','1'*64),('persistence','before_count',0),
])
def test_partial_malformed_or_contradictory_measurements_never_pass(signer,gate_name,key,value):
    p=scenario();row(p,gate_name)['data'][key]=value
    r=qualified(signer,p,gates=[gate_name])
    assert r['matrix'][0]['status'] in ('FAIL','UNKNOWN')


@pytest.mark.parametrize('case',['stale','future','missing','throttled','frequency','clock_reset','duration','idle','future_sample'])
def test_resource_coverage_and_real_freshness(signer,case):
    p=scenario();r=row(p,'resources');samples=r['data']['samples']
    if case=='stale':r['observed_at']-=1000
    elif case=='future':p['payload']['observed_at']+=100;r['observed_at']+=100
    elif case=='missing':samples[1]['cpu_temp_c']=None
    elif case=='throttled':samples[1]['throttled_mask']=0x10000
    elif case=='frequency':samples[1]['cpu_frequency_mhz']=None
    elif case=='clock_reset':samples[1]['measured_at']=samples[0]['measured_at']-1
    elif case=='duration':samples[0]['measured_at']=NOW-1
    elif case=='idle':
        for s in samples:s['cpu_load_pct']=1
    else:samples[1]['measured_at']=NOW+1
    assert qualified(signer,p,gates=['resources'])['result']!='PASS'


def test_lite_update_not_applicable_requires_absence_of_AB_claims():
    p=scenario();expected=dict(TARGET,profile='lite');p['payload']['target']=expected
    p['payload']['observations']=[]
    r=gate.evaluate(p,expected=expected,now=NOW,gates=['rollback'])
    assert statuses(r)=={'rollback':'NOT APPLICABLE'}
    assert not r['physical_attestation_complete']
    p=scenario();p['payload']['target']=expected
    for observation in p['payload']['observations']:observation['target']=expected
    assert gate.evaluate(p,expected=expected,now=NOW,gates=['rollback'])['result']=='FAIL'


def test_separate_boot_claims_and_ambiguous_update_never_promoted(signer):
    p=scenario('ambiguous_rollback')
    assert qualified(signer,p,gates=['rollback'],authenticated_bundle_sha256='9'*64)['result']=='UNKNOWN'
    p=scenario();r=row(p,'trial_boot');r['data']['events'][1]['boot_id']=r['data']['events'][0]['boot_id']
    assert qualified(signer,p,gates=['trial_boot'],authenticated_bundle_sha256='9'*64)['result']=='FAIL'


def test_gate_selection_is_bounded_and_independent(signer):
    r=qualified(signer,scenario('missing_captures'),gates=['radio'])
    assert statuses(r)=={'radio':'PASS'} and not r['physical_attestation_complete']
    with pytest.raises(ValueError):gate.evaluate(scenario(),expected=TARGET,now=NOW,gates=['radio','radio'])


@pytest.fixture
def cli():
    spec=importlib.util.spec_from_file_location('redux_acceptance_cli',ROOT/'scripts/redux-acceptance.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


def args(path):
    return ['evaluate','--package',str(path),'--revision',TARGET['revision'],'--release-sha256',TARGET['release_sha256'],
        '--device-id',TARGET['device_id'],'--model','pi4','--profile','ab','--now',str(NOW)]


def test_cli_is_offline_redacted_and_report_is_private_no_replace(cli,tmp_path,capsys,monkeypatch):
    p=scenario();source=tmp_path/'observations.json';source.write_text(json.dumps(p));report=tmp_path/'report.json'
    monkeypatch.setattr(cli.subprocess,'run',lambda *a,**k:pytest.fail('unexpected subprocess/network operation'))
    assert cli.main(['--json','--report',str(report)]+args(source))==3
    out=capsys.readouterr().out
    assert '/dev/' not in out and 'mountinfo' not in out and 'points' not in out
    assert json.loads(out)['production_ready'] is False
    assert report.stat().st_mode & 0o777==0o600
    before=report.read_bytes()
    assert cli.main(['--report',str(report)]+args(source))==1
    assert report.read_bytes()==before


@pytest.mark.parametrize('bad',[b'{"payload":{},"payload":{}}',b'{"payload":NaN}',b'x'*140000,b'{"secret":"SSID-do-not-export"}'])
def test_parser_errors_never_echo_sensitive_data(cli,tmp_path,capsys,bad):
    path=tmp_path/'SSID-private';path.write_bytes(bad)
    assert cli.main(['--json','inspect','--package',str(path)])==1
    out=capsys.readouterr()
    assert 'SSID' not in out.out+out.err and str(path) not in out.err


def test_evidence_symlink_and_hardlink_rejected(cli,tmp_path):
    source=tmp_path/'source';source.write_text(json.dumps(scenario()));link=tmp_path/'link';link.symlink_to(source)
    with pytest.raises(ValueError):cli.load(link)
    link.unlink();import os;os.link(source,link)
    with pytest.raises(ValueError):cli.load(link)


@pytest.mark.parametrize('name',['healthy','no_radio','missing_captures','stale_sensors','display_mapping','broken_processing','ethernet_framing','interrupted_update','ambiguous_rollback'])
def test_public_replay_command_never_certifies_hardware(cli,capsys,name):
    code=cli.main(['--json','replay','--scenario',name]);r=json.loads(capsys.readouterr().out)
    assert code in (1,3) and r['evidence_origin']=='synthetic'
    assert not r['physical_attestation_complete'] and not r['production_ready']


def test_compare_never_combines_evidence_into_physical_success(cli,tmp_path,capsys):
    before=tmp_path/'before';after=tmp_path/'after'
    before.write_text(json.dumps(scenario('no_radio')));after.write_text(json.dumps(scenario()))
    common=args(after)[3:]  # remove evaluate --package PATH
    assert cli.main(['--json','compare','--before',str(before),'--after',str(after)]+common)==3
    assert 'PASS' not in statuses(json.loads(capsys.readouterr().out)).values()


def test_replay_clock_cannot_freshen_physical_signature(cli,tmp_path,signer,capsys):
    sign,policy=signer;p=scenario();p['payload']['origin']='physical';sign(p)
    package=tmp_path/'p.json';package.write_text(json.dumps(p));trust=tmp_path/'policy.json';trust.write_text(json.dumps(policy))
    assert cli.main(['--json']+args(package)+['--gate','radio','--trust-policy',str(trust)])==3
    assert json.loads(capsys.readouterr().out)['matrix'][0]['provenance_verified'] is False


def test_rollback_cannot_substitute_for_independent_recovery(signer):
    p=scenario();row(p,'recovery')['data']=deepcopy(row(p,'rollback')['data'])
    r=qualified(signer,p,gates=['recovery'],authenticated_bundle_sha256='9'*64)
    assert r['result']!='PASS'
    p=scenario();row(p,'recovery')['data']['medium_sha256']='0'*64
    assert qualified(signer,p,gates=['recovery'],authenticated_bundle_sha256='9'*64)['result']=='FAIL'


@pytest.mark.parametrize('gate_name',['storage','persistence','resources','services','trial_boot'])
def test_contradictions_and_pre_authorization_measurements(signer,gate_name):
    p=scenario();data=row(p,gate_name)['data']
    if gate_name=='storage':data['partitions'][1]['disk_major_minor']='8:0'
    elif gate_name=='persistence':data['after_at']=data['before_at']-1
    elif gate_name=='resources':
        for sample in data['samples']:sample['measured_at']-=3601
    elif gate_name=='services':data['live_state']='stopped'
    else:data['events'][1]['at']=NOW+1
    assert qualified(signer,p,gates=[gate_name],authenticated_bundle_sha256='9'*64)['result']!='PASS'


def test_pi5_model_and_bound_release_are_supported(signer):
    sign,policy=signer;p=scenario()
    target=dict(TARGET,model='pi5');p['payload']['target']=target
    for r in p['payload']['observations']:r['target']=target
    row(p,'platform')['data']['model']='pi5';p['payload']['origin']='physical';sign(p)
    policy['target']=target
    result=gate.evaluate(p,expected=target,now=NOW,policy=policy,gates=['platform'])
    assert result['result']=='PASS' and not result['physical_attestation_complete']


def test_source_integration_executes_existing_runner_at_exact_revision(cli,tmp_path,monkeypatch):
    from types import SimpleNamespace
    calls=[];checks=[]
    monkeypatch.setattr(cli.candidate_factory,'source',lambda root,revision:checks.append(revision))
    def runner(command,**options):
        calls.append(command)
        assert command[1].endswith('scripts/redux-qualify.py')
        assert command[2]=='--beta'
        options['stdout'].write(b'PASS    Python suite: exit 0; 10 passed\nPASS    Image contracts: exit 0; 4 passed\n')
        Path(command[-1]).write_text(json.dumps({'offline_qualified':True,'source_revision':TARGET['revision'],'worktree_dirty':False,'matrix':[
            dict(gate=g,status='PASS',detail='synthetic execution fixture') for g in
            ('offline_software','integration_tests','ui_behavior','image_contracts')]}))
        return SimpleNamespace(returncode=3)
    result=cli.source_qualification(TARGET,runner=runner)
    assert result['status']=='PASS' and len(calls)==1
    assert checks==[TARGET['revision'],TARGET['revision']]
    assert result['test_totals']['python']=={'passed':10,'skipped':0,'exit_code':0}


def test_actual_candidate_verifier_is_reused_and_wrong_source_never_accepted(cli,monkeypatch):
    calls=[]
    inventory={'recorded_source':{'revision':TARGET['revision']},'inputs':{'profile':'ab'},'image':'image.img.xz',
        'files':{'image.img.xz':{'sha256':TARGET['release_sha256']},'update.raucb':{'sha256':'9'*64}}}
    monkeypatch.setattr(cli.candidate_factory,'inspect',lambda _:inventory)
    def verify(*a,**kw):
        calls.append((a,kw));return {'result':'UNKNOWN','offline_qualified':False,'checks':[
            {'name':'Authenticated update bundle','status':'PASS','detail':'do not publish producer text'}]}
    monkeypatch.setattr(cli.candidate_factory,'verify',verify)
    result,bundle=cli.candidate_qualification('candidate',TARGET,keyring='external-public-trust')
    assert result['status']=='UNKNOWN' and bundle=='9'*64 and len(calls)==1
    assert 'detail' not in result['checks'][0]
    with pytest.raises(ValueError):cli.candidate_qualification('candidate',dict(TARGET,revision='0'*40))
    assert len(calls)==1


def test_combined_matrix_keeps_source_candidate_and_physical_gates_separate():
    from redux.core.beta_readiness import acceptance_matrix
    source={'status':'PASS','matrix':[dict(gate=g,status='PASS') for g in
        ('offline_software','integration_tests','ui_behavior','image_contracts')]}
    hardware=gate.evaluate(scenario(),expected=TARGET,now=NOW)
    rows=acceptance_matrix(source,{'status':'PASS','reason':'actual_artifact_gates_executed'},hardware)
    states={r['gate']:r['status'] for r in rows}
    assert states['release_candidate']=='PASS' and states['offline_software']=='PASS'
    assert states['physical_rf_display']==states['actual_update_rollback']==states['hardware_attestations']=='UNKNOWN'


def test_cpu_stress_is_measured_load_not_itself_a_thermal_failure(signer):
    p=scenario()
    for sample in row(p,'resources')['data']['samples']:sample['cpu_load_pct']=100
    assert qualified(signer,p,gates=['resources'])['result']=='PASS'
    row(p,'resources')['data']['samples'][-1]['cpu_temp_c']=80
    assert qualified(signer,p,gates=['resources'])['result']=='FAIL'
    p=scenario();row(p,'resources')['data']['samples'][-1]['throttled_mask']=0x80000000
    assert qualified(signer,p,gates=['resources'])['result']=='UNKNOWN'


def test_wrong_target_rejected_even_when_selected_observation_is_absent():
    p=scenario();p['payload']['observations']=[]
    result=gate.evaluate(p,expected=dict(TARGET,profile='lite'),now=NOW,gates=['rollback'])
    assert result['result']=='FAIL' and result['matrix'][0]['reason']=='wrong_target'


def test_fittable_calibration_is_not_proof_of_correct_active_projection(signer):
    p=scenario();data=row(p,'display')['data'];data.pop('observed_points')
    assert qualified(signer,p,gates=['display'])['result']=='UNKNOWN'
    p=scenario()
    for point in row(p,'display')['data']['observed_points']:point['pixel_x']=479-point['pixel_x']
    result=qualified(signer,p,gates=['display'])
    assert result['result']=='FAIL' and result['matrix'][0]['reason']=='active_touch_projection_mismatch'


@pytest.mark.parametrize('case',['wrong_revision','dirty','offline_failed','bad_exit'])
def test_executed_source_report_must_match_clean_requested_revision(cli,monkeypatch,case):
    from types import SimpleNamespace
    monkeypatch.setattr(cli.candidate_factory,'source',lambda *args:None)
    def runner(command,**options):
        report={'source_revision':TARGET['revision'],'worktree_dirty':False,'offline_qualified':True,'matrix':[]}
        if case=='wrong_revision':report['source_revision']='0'*40
        elif case=='dirty':report['worktree_dirty']=True
        elif case=='offline_failed':report['offline_qualified']=False
        Path(command[-1]).write_text(json.dumps(report))
        return SimpleNamespace(returncode=1 if case=='bad_exit' else 3)
    assert cli.source_qualification(TARGET,runner=runner)['status']=='FAIL'
