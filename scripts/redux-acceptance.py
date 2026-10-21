#!/usr/bin/env python3
"""Offline acceptance by default; explicit session-collect uses read-only SSH.

Never sudo, RF operations, installation, service changes or deployment.
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
import json
import re
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'image'))
from redux.hardware_acceptance import GATES, evaluate, validate, canonical
from redux.core.beta_readiness import acceptance_matrix
from update_safety import read, json_record, recovery_plan
import root_payload
import candidate_factory


def load(path, limit=128*1024):
    path = Path(path).absolute()
    return json_record(read(path.parent, path.name, limit))


def save(path, value):
    """New, private local report only; never overwrite or follow directory links."""
    from update_safety import safe_path
    path = Path(path).absolute(); safe_path(path.parent, path.name)
    import os
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(canonical(value) + b'\n')


def source_qualification(expected, runner=subprocess.run):
    # Reuse the existing runner. Never trust an imported self-authored PASS JSON.
    candidate_factory.source(ROOT, expected['revision'])
    with tempfile.TemporaryDirectory(prefix='redux-acceptance-source-') as folder:
        evidence = Path(folder) / 'beta.json'
        with (Path(folder)/'qualification.log').open('wb') as log:
            result = runner([sys.executable, str(ROOT/'scripts/redux-qualify.py'), '--beta', '--beta-report', str(evidence)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=1800, check=False)
        with (Path(folder)/'qualification.log').open('rb') as log:
            log.seek(0,2);length=log.tell();log.seek(max(0,length-16384))
            tail=log.read(16384).decode('utf-8',errors='replace')
        totals={}
        for label,name in (('Python suite','python'),('Image contracts','image')):
            match=re.search(r'PASS\s+'+label+r': exit 0; ([0-9]+) passed([^\n]*)',tail)
            if match:
                skips=re.search(r'([0-9]+) skipped',match[2])
                totals[name]={'passed':int(match[1]),'skipped':int(skips[1]) if skips else 0,'exit_code':0}
        candidate_factory.source(ROOT, expected['revision'])
        report = load(evidence)
        if (result.returncode not in (0, 3) or report.get('offline_qualified') is not True
                or report.get('source_revision')!=expected['revision'] or report.get('worktree_dirty') is not False):
            states={}
            for label in ('Python suite','Image contracts','Python compile','Shell syntax','Diff whitespace'):
                match=re.search(r'^(PASS|FAIL|UNKNOWN)\s+'+label+r':',tail,re.M)
                if match:states[label]=match[1]
            return {'status':'FAIL', 'reason':'executed_source_qualification_failed',
                    'qualification_exit':result.returncode,'gate_statuses':states,'test_totals':totals,
                    'reported_source_matches':report.get('source_revision')==expected['revision'],
                    'reported_worktree_clean':report.get('worktree_dirty') is False,
                    'diagnostic_hint':'use_short_pytest_basetemp_for_unix_sockets' if 'AF_UNIX path too long' in tail else 'run_redux_qualify_locally_for_failure_details'}
        return {'status':'PASS', 'reason':'existing_qualification_executed_at_exact_clean_revision',
                'revision':expected['revision'], 'matrix':report['matrix'][:4], 'test_totals':totals}


def candidate_qualification(directory, expected, keyring=None, trusted_sha256=None, scratch=None):
    inventory = candidate_factory.inspect(directory)
    if (inventory['recorded_source']['revision'] != expected['revision']
            or inventory['inputs']['profile'] != expected['profile']
            or inventory['files'][inventory['image']]['sha256'] != expected['release_sha256']):
        raise ValueError('candidate target mismatch')
    result = candidate_factory.verify(directory, keyring=keyring, trusted_sha256=trusted_sha256, temporary_root=scratch)
    bundle = None
    if any(row['name']=='Authenticated update bundle' and row['status']=='PASS' for row in result['checks']):
        bundle = inventory['files']['update.raucb']['sha256']
    return {'status':'FAIL' if result['result']=='FAIL' else 'PASS' if result.get('offline_qualified') is True else 'UNKNOWN',
            'reason':'actual_artifact_gates_executed', 'checks':[{'name':r['name'],'status':r['status']} for r in result['checks']],
            'image_sha256':expected['release_sha256'], 'bundle_authenticated':bundle is not None}, bundle


def emit(result, args):
    if args.report:save(args.report, result)
    if args.json:
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    else:
        for row in result.get('matrix', []):
            print(f"{row['status']:15} {row['gate']}: {row['reason']}")
        for row in result.get('checks', []):
            print(f"{row['status']:15} {row['gate']}: {row['reason']}")
        for name in ('source_qualification','candidate_qualification'):
            if name in result:
                print(f"{result[name]['status']:15} {name}: {result[name]['reason']}")
                if result[name].get('diagnostic_hint'):print('Action: '+result[name]['diagnostic_hint'])
        if result.get('observations') is not None:
            print('Saved observations (unsigned; not physical qualification):')
            print(json.dumps(result['observations'],indent=2,sort_keys=True,allow_nan=False))
        if result.get('recovery_plan'):
            print('Recovery plan only: '+result['recovery_plan']['phase']+'; '+result['recovery_plan']['status'])
        note=(' Read-only SSH session; no device configuration changes performed.' if result.get('network_actions_performed') is True else ' Offline only; no device or network actions performed.')
        print('Result: '+result['result']+'.'+note)
    return 1 if result['result']=='FAIL' else 3 if result['result']=='UNKNOWN' else 0


def session_support():
    """Reuse the existing bounded SSH transport and memory source loader."""
    import importlib.util
    import inspect
    def module(name):
        spec=importlib.util.spec_from_file_location(name.replace('-','_'), ROOT/'scripts'/(name+'.py'))
        value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
        return value
    pi=module('pi-acceptance');field=module('field-status')
    from redux.acceptance_collect import cached_status
    helper='import json, math, urllib.request\n'+inspect.getsource(field.sanitize)+'\n'+inspect.getsource(cached_status)
    modules=tuple(dict.fromkeys(pi.MODULES+('redux.acceptance_session','redux.boot_readiness',
        'redux.display.acceptance','redux.display.kiosk','redux.display.local_http',
         'redux.core.processing_evidence')))
    entry='''import json, os, sys
try:
    from redux.acceptance_session import remote_snapshot
    from redux.session_helpers import cached_status, sanitize
    connection=os.environ.get('SSH_CONNECTION','').split()
    if len(connection)!=4:raise ValueError('no verified SSH connection')
    import ipaddress
    peer=str(ipaddress.ip_address(connection[0]))
    result=remote_snapshot(peer,sys.argv[1],sys.argv[2],status_supplier=cached_status,sanitizer=sanitize)
    print(json.dumps(result,sort_keys=True,allow_nan=False))
except Exception as exc:
    # The host needs one bounded *class* of failure, not a traceback, paths,
    # exception messages, SSIDs, identifiers or arbitrary stderr from the Pi.
    category=type(exc).__name__
    if category not in ('ImportError','ModuleNotFoundError','AttributeError',
                        'TypeError','ValueError','OSError','KeyError',
                        'IndexError','TimeoutError','NameError','RuntimeError'):
        category='InternalError'
    print(json.dumps({'format':'redux-bringup-session-v1',
                      'hardware_changes':False,'collector_error':category,
                      'session_id':sys.argv[1] if len(sys.argv)>1 else None,
                      'host_revision':sys.argv[2] if len(sys.argv)>2 else None},
                     sort_keys=True))
'''
    source=pi.bundle(modules=modules,extra_sources={'redux.session_helpers':helper},entrypoint=entry)
    return pi,field,source


def session_plan(revision):
    import hashlib
    _,_,source=session_support()
    return {'format':'redux-bringup-plan-v1','host_revision':revision,
        'collector_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'ssh':{'user':'pi','public_key_only':True,'strict_host_key_checking':True,'sudo':False,'deadline_seconds':45},
        'scope':['installed source, model, kernel and firmware version hashes',
                 'existing radio/ownership/regulatory inventory and service states',
                 'two passive sysfs RX/TX counter snapshots; no packet socket',
                 'kernel mount identity checks and capacity',
                 'one cached dashboard status: Doctor, telemetry, worker aggregates and plugin availability',
                 'existing framebuffer/SPI/touch metadata inventory; no input events or pixels'],
        'excluded':['capture contents, logs, credentials, SSIDs, MACs, serials',
                    'sudo, installs, writes, restarts, reboots, scans, interface or RF changes'],
        'physical_acceptance':'UNKNOWN','card_identity':'UNKNOWN','requires_operator_authorization':True}


def session_collect(args):
    import hashlib
    import shlex
    import uuid
    from redux.acceptance_session import FORMAT
    from update_safety import safe_path
    output=args.output.absolute()
    safe_path(output.parent,output.name)
    if output.exists() or output.is_symlink():raise ValueError('use a new session output')
    pi,field,source=session_support()
    host,key=field.validate_target(args.host,args.key)
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True))
    session=uuid.uuid4().hex+uuid.uuid4().hex
    argv=['ssh','-F','/dev/null','-T','-i',str(key)]
    for option in ('BatchMode=yes','StrictHostKeyChecking=yes','ConnectTimeout=8','IdentitiesOnly=yes',
        'PreferredAuthentications=publickey','PasswordAuthentication=no','KbdInteractiveAuthentication=no',
        'ClearAllForwardings=yes','ForwardAgent=no','ForwardX11=no','PermitLocalCommand=no',
        'ControlMaster=no','ControlPath=none','UpdateHostKeys=no','ProxyCommand=none','ProxyJump=none','ServerAliveInterval=5','ServerAliveCountMax=2'):
        argv+=['-o',option]
    argv += [f'pi@{host}','python3 -B - '+shlex.join([session,revision])]
    from redux.radio.local_evidence import Unavailable
    try:
        report=pi.fetch(argv,source,format_name=FORMAT)
    except Unavailable as exc:
        # Only locally generated, allowlisted Unavailable reasons may leave the
        # host. Never print SSH stderr, exception arguments or remote output.
        reason = {
            'command_timeout': 'ssh_or_collector_timeout',
            'deadline': 'ssh_or_collector_timeout',
            'command_overflow': 'ssh_output_overflow',
            'command_failed': 'ssh_or_remote_process_failed',
            'invalid_acceptance_response': 'ssh_invalid_collector_response',
            'command_unavailable': 'ssh_process_unavailable',
        }.get(str(exc), 'ssh_unavailable_check_verified_ethernet_host_key_identity_no_retry')
        return {'schema':1,'result':'UNKNOWN','session_report_saved':False,'network_actions_performed':True,
                'checks':[{'gate':'read_only_transport','status':'UNKNOWN','reason':reason}]}
    if report.get('collector_error') is not None:
        if report.get('session_id')!=session or report.get('host_revision')!=revision:
            raise ValueError('session response identity mismatch')
        category = report['collector_error']
        supported = {'ImportError','ModuleNotFoundError','AttributeError',
                     'TypeError','ValueError','OSError','KeyError','IndexError',
                     'TimeoutError','NameError','RuntimeError','InternalError'}
        if category not in supported:
            raise ValueError('invalid collector error code')
        return {'schema':1,'result':'UNKNOWN','session_report_saved':False,'network_actions_performed':True,
                'checks':[{'gate':'remote_collector','status':'UNKNOWN',
                           'reason':'remote_'+category}]}
    if report.get('session_id')!=session or report.get('host_revision')!=revision:
        raise ValueError('session response identity mismatch')
    if report.get('physical_acceptance')!='UNKNOWN' or report.get('unsigned') is not True:
        raise ValueError('unqualified acceptance claim')
    if subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()!=revision:
        raise ValueError('host source changed during acquisition')
    report['operator_card_role']=getattr(args,'card_role','unverified')
    report['host_worktree_dirty']=dirty
    report['collector_sha256']=hashlib.sha256(source.encode()).hexdigest()
    save(args.output,report)
    return {**session_inspect(report), 'session_report_saved':True,'network_actions_performed':True,
            'evidence_origin':'strict_ssh_read_only_unsigned','identity_stable':report.get('identity_stable') is True}



def session_inspect(record, now=None):
    """Readable projection of a saved session, never attest imported assertions."""
    from redux.acceptance_session import FORMAT, token
    if (not isinstance(record,dict) or record.get('format')!=FORMAT
            or record.get('hardware_changes') is not False or record.get('unsigned') is not True
            or any(record.get(key)!='UNKNOWN' for key in ('physical_acceptance','release_identity','card_identity','update_rollback'))
            or not token(record.get('host_revision'),r'[a-f0-9]{40}')
            or not token(record.get('session_id'),r'[a-f0-9]{64}')
            or (record.get('embedded_revision') is not None and not token(record['embedded_revision'],r'[a-f0-9]{40}'))):
        raise ValueError('invalid or forged session')
    stamp=record.get('completed_at')
    from redux.hardware_acceptance import number
    now=time.time() if now is None else now
    fresh=number(stamp) and 0<=now-stamp<=120
    checks=[{'gate':'saved_observation_freshness','status':'PASS' if fresh else 'UNKNOWN',
             'reason':'timestamp_within_120_seconds' if fresh else 'historical_or_invalid_timestamp'},
            {'gate':'session_identity_continuity','status':'PASS' if record.get('identity_stable') is True else 'UNKNOWN',
             'reason':'saved_identity_observation_is_not_independent_attestation'}]
    field=__import__('importlib.util',fromlist=['util'])
    spec=field.spec_from_file_location('session_field_status',ROOT/'scripts/field-status.py')
    module=field.module_from_spec(spec);spec.loader.exec_module(module)
    status=record.get('status')
    if isinstance(status,dict):
        for name,state,detail in module.assess(status):
            checks.append({'gate':name,'status':state if fresh and record.get('identity_stable') is True else 'UNKNOWN',
                           'reason':'saved_read_only_observation_not_physical_qualification'})
    checks.extend({'gate':gate,'status':'UNKNOWN','reason':'requires_separate_approved_physical_evidence'}
                  for gate in ('decoded_reception','capture_conversion','physical_touch_display','reboot_preservation','update_rollback','disposable_card_identity'))
    observations={'host_revision':record['host_revision'],'embedded_revision':record.get('embedded_revision'),
        'model':record.get('model') if record.get('model') in ('pi4','pi5') else None,
        'kernel':token(record.get('kernel')), 'architecture':token(record.get('architecture')),
        'firmware':{},'radios':[], 'display_metadata_state':None}
    firmware=record.get('firmware')
    if isinstance(firmware,dict):
        observations['firmware']={key:token(firmware.get(key),r'[a-f0-9]{64}') for key in ('firmware_version_sha256','eeprom_version_sha256')}
    rows=record.get('rx_observations')
    if isinstance(rows,list):
        for row in rows[:8]:
            if not isinstance(row,dict):continue
            observations['radios'].append({key:row.get(key) if type(row.get(key)) is int and 0<=row[key]<=2**63-1 else None
                                           for key in ('radio','arphrd','rx_delta','tx_delta')})
    # Project already-saved evidence, never read hardware or emit unbounded
    # free-text names, paths, network identifiers, capture fields or secrets.
    def one_of(value, choices):
        return value if type(value) is str and value in choices else None
    def count(value, maximum=2**63-1):
        return value if type(value) is int and 0 <= value <= maximum else None
    def boolean(value):
        return value if type(value) is bool else None

    display=record.get('display')
    if isinstance(display,dict) and display.get('state') in ('READY_FOR_OPERATOR_SELECTION','BLOCKED','UNAVAILABLE','INVALID'):
        observations['display_metadata_state']=display['state']
        choices={'INVENTORY TRUNCATED','FRAMEBUFFER MISSING','FRAMEBUFFER AMBIGUOUS',
                 'ILI9486 MISSING','ILI9486 AMBIGUOUS','ILI9486 DRIVER UNBOUND',
                 'TOUCH DEVICE MISSING','TOUCH DEVICE AMBIGUOUS',
                 'TOUCH CAPABILITIES MISSING','INVENTORY INVALID'}
        blockers=display.get('blockers')
        observations['display_blockers']=([b for b in blockers[:16] if type(b) is str and b in choices]
                                          if isinstance(blockers,list) else [])
        observations['display_candidates']={
            key:count(display.get(key),64) for key in
            ('framebuffer_candidates','ili9486_candidates','touch_candidates')}
        framebuffers=display.get('framebuffers')
        observations['framebuffer_metadata']=[]
        if isinstance(framebuffers,list):
            for row in framebuffers[:16]:
                if not isinstance(row,dict):continue
                observations['framebuffer_metadata'].append({
                    'ordinal':count(row.get('ordinal'),64),
                    'dimensions':token(row.get('dimensions'),r'[0-9]{1,5},[0-9]{1,5}'),
                    'bits_per_pixel':token(row.get('bits_per_pixel'),r'[0-9]{1,3}'),
                    'stride':token(row.get('stride'),r'[0-9]{1,8}')})

    storage=record.get('storage')
    if isinstance(storage,dict):
        observations['storage']={}
        for name in ('root','root_lower','boot','captures'):
            row=storage.get(name)
            observations['storage'][name]=(
                {'status':one_of(row.get('status'),('ok','action','unknown')),
                 'filesystem':one_of(row.get('filesystem'),('overlay','ext4','vfat'))}
                if isinstance(row,dict) else {'status':None,'filesystem':None})
        bytes_free=count(storage.get('available_bytes'))
        observations['captures_available_mib']=(
            bytes_free//(1024*1024) if bytes_free is not None else None)

    radios=record.get('radio')
    if isinstance(radios,dict):
        inventory_status=one_of(radios.get('inventory_status'),(
            'fresh','unavailable','capabilities_unknown','observation_changed',
            'observation_stale','radio_inventory_changed','boot_changed',
            'ethernet_changed','iw_identity_changed','process_ownership_changed',
            'nm_changed','inventory_disagreement','radio_identity_disagreement',
            'ethernet_unverified','process_inventory_overflow','inventory_overflow',
            'radio_overflow','iw_identity_invalid','interface_invalid',
            'file_overflow','command_denied','command_timeout','command_overflow',
            'command_failed','command_unavailable','deadline','permission_denied',
            'device_or_process_disappeared','io_unavailable','invalid_measurement'))
        observations['radio_inventory_status']=inventory_status
        radio_details=[]
        for row in radios.get('radios',[])[:8] if isinstance(radios.get('radios'),list) else []:
            if not isinstance(row,dict):continue
            radio_details.append({
                'radio':count(row.get('radio'),64),
                'transport':one_of(row.get('transport'),('onboard','usb','unknown')),
                'kernel_mode':one_of(row.get('kernel_mode'),('managed','monitor','unknown')),
                'arphrd':count(row.get('arphrd'),65535),
                'nm_managed':boolean(row.get('nm_managed')),
                'nm_state':count(row.get('nm_state'),120),
                'usb_ancestry':boolean(row.get('usb_ancestry')),
                'identity_stable':boolean(row.get('identity_stable')),
                'conflicting_processes':count(row.get('conflicting_processes'),8192)})
        observations['radio_inventory_details']=radio_details

    processing=record.get('processing')
    if isinstance(processing,dict):
        artifact_total=count(processing.get('artifact_count'))
        allowed_statuses={'ready','error','invalid','duplicate','skipped','deferred',
                          'deferred_low_storage','paused_low_storage','storage_error'}
        grouped=processing.get('by_status')
        verified_groups=None
        if (isinstance(grouped,dict) and len(grouped)<=len(allowed_statuses)
                and all(type(key) is str and key in allowed_statuses
                        and count(value) is not None for key,value in grouped.items())):
            if artifact_total is not None and sum(grouped.values())==artifact_total:
                verified_groups={key:grouped[key] for key in sorted(grouped)}
        # A categorized ledger row is not independently verified capture
        # material. Only the separate inode/digest audit can verify prepared files.
        scan=processing.get('scan')
        recent_scan=None
        if isinstance(scan,dict):
            scanned=count(scan.get('scanned'))
            outcomes=scan.get('outcomes')
            if (scanned is not None and isinstance(outcomes,dict)
                    and len(outcomes)<=len(allowed_statuses)
                    and all(type(k) is str and k in allowed_statuses
                            and count(v) is not None for k,v in outcomes.items())
                    and (sum(outcomes.values())==scanned
                         or scanned==0 and outcomes=={'paused_low_storage':1})):
                recent_scan={'scanned':scanned,
                             'outcomes':{key:outcomes[key] for key in sorted(outcomes)}}
        observations['capture_processing']={
            'available':boolean(processing.get('available')),
            'fresh_at_collection':boolean(processing.get('fresh')),
            'artifact_count':artifact_total,
            'by_status':verified_groups,
            'last_scan':recent_scan}
    plugins=record.get('plugins')
    if isinstance(plugins,dict):
        observations['plugin_inventory_available']=boolean(plugins.get('available'))
    if isinstance(status,dict):
        acceptance=status.get('acceptance')
        if isinstance(acceptance,dict):
            observations['live_runtime']={
                'checkpoint_freshness':one_of(acceptance.get('checkpoint_freshness'),('fresh','stale','future','invalid')),
                'live_state':one_of(acceptance.get('live_state'),(
                    'running','starting','starting_engine','rotating','degraded',
                    'telemetry_degraded','storage_paused','stopped','lease_blocked',
                    'termination_pending')),
                'events_seen':count(int(acceptance['events_seen']))
                    if type(acceptance.get('events_seen')) is str
                    and acceptance['events_seen'].isdigit()
                    and len(acceptance['events_seen'])<=19 else None}
    return {'schema':1,'result':'UNKNOWN','host_revision':record['host_revision'], 'observations':observations,
            'embedded_revision':record.get('embedded_revision'),'checks':checks,
            'physical_acceptance':'UNKNOWN','evidence_origin':'saved_unsigned_session'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--report', type=Path, help='new private redacted report; no overwrite')
    sub=parser.add_subparsers(dest='mode', required=True)
    plan=sub.add_parser('session-plan',help='offline exact read-only SSH scope and collector identity; no connection')
    saved=sub.add_parser('session-inspect',help='inspect a saved bounded session; never promote hardware gates')
    saved.add_argument('--session-file',type=Path,required=True)
    session=sub.add_parser('session-collect',help='explicit separately authorized read-only SSH, no sudo or device writes')
    session.add_argument('--host',required=True)
    session.add_argument('--key',type=Path,default=Path.home()/'.ssh/redux_field_ed25519')
    session.add_argument('--card-role',choices=('unverified','disposable-redux','jayofelony','other'),default='unverified',help='operator declaration only; never hardware identity or flash authorization')
    session.add_argument('--output',type=Path,required=True,help='new private host-only report')
    session.add_argument('--authorize-read-only',action='store_true',required=True,help='operator approved this exact read-only session')
    pre=sub.add_parser('preflight', help='existing assembled-root contract, no boot or mounts')
    pre.add_argument('--root', type=Path, required=True)
    pre.add_argument('--other-root', type=Path)
    pre.add_argument('--profile', choices=('lite','ab'), required=True)
    pre.add_argument('--country')
    inspect=sub.add_parser('inspect', help='bounded schema/inventory inspection; never hardware PASS')
    inspect.add_argument('--package', type=Path, required=True)
    replay=sub.add_parser('replay', help='deterministic synthetic scenarios; never physical evidence')
    replay.add_argument('--scenario', choices=('healthy','no_radio','missing_captures','stale_sensors','display_mapping',
        'broken_processing','ethernet_framing','interrupted_update','ambiguous_rollback'), required=True)
    local=sub.add_parser('collect-local',help='explicit read-only acquisition on an approved local Pi; never SSH or sudo')
    local.add_argument('--session-id',required=True)
    local.add_argument('--sample-seconds',type=int,default=2)
    local.add_argument('--revision',required=True);local.add_argument('--release-sha256',required=True)
    local.add_argument('--device-id',required=True);local.add_argument('--model',choices=('pi4','pi5'),required=True)
    local.add_argument('--profile',choices=('lite','ab'),required=True)
    for mode in ('evaluate','compare'):
        p=sub.add_parser(mode)
        if mode=='evaluate':p.add_argument('--package',type=Path,required=True)
        else:
            p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True)
        p.add_argument('--revision',required=True);p.add_argument('--release-sha256',required=True)
        p.add_argument('--device-id',required=True,help='pseudonymous device SHA256, never a MAC/serial')
        p.add_argument('--model',choices=('pi4','pi5'),required=True);p.add_argument('--profile',choices=('lite','ab'),required=True)
        p.add_argument('--trust-policy',type=Path,help='separately reviewed external session authorization/public key')
        p.add_argument('--gate',choices=GATES,action='append')
        p.add_argument('--now',type=float,help='explicit replay clock; disables physical provenance')
        if mode=='evaluate':
            p.add_argument('--candidate-dir',type=Path)
            p.add_argument('--keyring',type=Path);p.add_argument('--trusted-image-sha256')
            p.add_argument('--scratch-dir',type=Path)
            p.add_argument('--qualify-source',action='store_true',help='run existing complete qualification, no duplicate runner')
            p.add_argument('--recovery-root',type=Path,help='existing read-only saved A/B state for plan only')
    args=parser.parse_args(argv)
    if args.mode=='collect-local' and args.report:
        parser.error('collect-local writes stdout only; save evidence on the operator host')
    try:
        if args.mode=='session-plan':
            revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
            result=session_plan(revision)
            if args.report:save(args.report,result)
            print(json.dumps(result,sort_keys=True,allow_nan=False) if args.json else json.dumps(result,indent=2))
            return 3
        elif args.mode=='session-inspect':
            return emit(session_inspect(load(args.session_file)),args)
        elif args.mode=='session-collect':
            return emit(session_collect(args),args)
        elif args.mode=='preflight':
            root_payload.verify(args.root,profile=args.profile,other_root=args.other_root,expected_country=args.country)
            result={'schema':1,'result':'UNKNOWN','checks':[{'gate':'assembled_root','status':'PASS','reason':'existing_runtime_and_first_boot_contract_passed'},
                {'gate':'physical_boot','status':'UNKNOWN','reason':'offline_root_is_not_boot_evidence'}], 'physical_boot_verified':False}
        elif args.mode=='inspect':
            package=load(args.package);p=validate(package)
            result={'schema':1,'result':'UNKNOWN','target':p['target'],'origin':p['origin'],
                'matrix':[{'gate':row['gate'],'status':'UNKNOWN','reason':'inspection_does_not_verify_provenance_or_measurements'} for row in p['observations']]}
        elif args.mode=='replay':
            from redux.acceptance_scenarios import scenario, NOW
            package=scenario(args.scenario)
            result=evaluate(package,expected=package['payload']['target'],now=NOW)
        elif args.mode=='collect-local':
            from redux.acceptance_collect import collect
            expected={k:getattr(args,k) for k in ('revision','release_sha256','device_id','model','profile')}
            package=collect(expected,args.session_id,seconds=args.sample_seconds)
            print(json.dumps(package,sort_keys=True,allow_nan=False))
            print('UNKNOWN: unsigned read-only acquisition; no physical qualification inferred.',file=sys.stderr)
            return 3
        else:
            expected={k:getattr(args,k) for k in ('revision','release_sha256','device_id','model','profile')}
            policy=load(args.trust_policy, 16384) if args.trust_policy else None
            # Supplying a replay clock can never freshen a physical attestation.
            now=time.time() if args.now is None else args.now
            if args.now is not None:policy=None
            def evaluation(path, bundle=None):
                return evaluate(load(path),expected=expected,now=now,policy=policy,gates=args.gate,authenticated_bundle_sha256=bundle)
            if args.mode=='compare':
                before,after=evaluation(args.before),evaluation(args.after)
                result={'schema':1,'result':'FAIL' if 'FAIL' in (before['result'],after['result']) else 'UNKNOWN',
                    'target':expected,'matrix':[{'gate':a['gate'],'status':a['status'], 'reason':'previous_'+b['status']+'_current_'+a['status']} for b,a in zip(before['matrix'],after['matrix'])],
                    'note':'Independent evaluations; observations are never merged across boots/devices.'}
            else:
                candidate,bundle=None,None
                if args.candidate_dir:
                    candidate,bundle=candidate_qualification(args.candidate_dir,expected,args.keyring,args.trusted_image_sha256,args.scratch_dir)
                result=evaluation(args.package,bundle)
                software=source_qualification(expected) if args.qualify_source else {'status':'UNKNOWN','reason':'source_qualification_not_executed'}
                result['source_qualification']=software
                result['candidate_qualification']=candidate or {'status':'UNKNOWN','reason':'actual_candidate_not_verified'}
                # Reuse the beta matrix. Never fill its physical rows from source tests.
                result['release_acceptance_matrix']=acceptance_matrix(software,result['candidate_qualification'],result)
                if args.recovery_root:
                    plan=recovery_plan(args.recovery_root)
                    # Never export raw paths, exception text or operator notes from imported state.
                    result['recovery_plan']={'phase':plan['phase'],'status':plan['result'],'execution_permitted':False}
                if software['status']=='FAIL' or candidate and candidate['status']=='FAIL':result['result']='FAIL'
                elif result['result']=='PASS' and (software['status']!='PASS' or not candidate or candidate['status']!='PASS'):
                    result['result']='UNKNOWN'
        return emit(result,args)
    except (OSError,ValueError,TypeError,KeyError,RecursionError,ImportError,subprocess.SubprocessError):
        # Imported evidence and paths must never leak into error output.
        result={'schema':1,'result':'FAIL','checks':[{'gate':'input','status':'FAIL','reason':'invalid_or_unavailable_evidence_review_schema_and_target'}]}
        print(json.dumps(result,sort_keys=True) if args.json else 'FAIL: invalid or unavailable evidence; review schema, target and prerequisites.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
