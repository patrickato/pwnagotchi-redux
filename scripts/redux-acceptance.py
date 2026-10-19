#!/usr/bin/env python3
"""Offline first-boot and hardware acceptance. Never SSH, sudo, RF or deploy."""
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
    with path.open('xb') as stream:
        import os
        os.chmod(path, 0o600)
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
        if result.get('recovery_plan'):
            print('Recovery plan only: '+result['recovery_plan']['phase']+'; '+result['recovery_plan']['status'])
        print('Result: '+result['result']+'. Offline only; no device or network actions performed.')
    return 1 if result['result']=='FAIL' else 3 if result['result']=='UNKNOWN' else 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--report', type=Path, help='new private redacted report; no overwrite')
    sub=parser.add_subparsers(dest='mode', required=True)
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
        if args.mode=='preflight':
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
