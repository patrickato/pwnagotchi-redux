#!/usr/bin/env python3
"""Host release factory and read-only candidate/RAUC/recovery gates. Never SSH/install/reboot/sudo."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'image'))
from qualify_candidate import qualify
import candidate_factory as factory
from update_safety import inspect_configuration,recovery_plan,read,json_record


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json',action='store_true',help='machine-readable verdict with the same exit policy')
    sub=parser.add_subparsers(dest='mode',required=True)
    config=sub.add_parser('rauc',help='inspect a saved/extracted root; no device discovery')
    config.add_argument('--root',type=Path,required=True)
    config.add_argument('--revision',required=True)
    config.add_argument('--context',type=Path,help='saved canonical context JSON, not a live device')
    recovery=sub.add_parser('recovery',help='plan only; never execute a recovery action')
    recovery.add_argument('--root',type=Path,required=True)
    recovery.add_argument('--trial',action=argparse.BooleanOptionalAction,default=None,
                          help='operator-recorded trial observation; never inferred from a pending label')
    candidate=sub.add_parser('candidate',help='inspect actual compressed image and optional signed update')
    candidate.add_argument('--image',type=Path,required=True)
    candidate.add_argument('--revision',required=True)
    candidate.add_argument('--profile',choices=('lite','ab'),required=True)
    candidate.add_argument('--country',required=True)
    candidate.add_argument('--trusted-sha256')
    candidate.add_argument('--bundle',type=Path)
    candidate.add_argument('--keyring',type=Path)
    candidate.add_argument('--version')
    candidate.add_argument('--scratch-dir',type=Path,help='existing private scratch parent for actual image inspection')
    factory_parser=sub.add_parser('factory',help='host-only candidate preflight/build/verify/inspect; never sudo or deploy')
    operations=factory_parser.add_subparsers(dest='factory_operation',required=True)
    for operation in ('preflight','build'):
        option=operations.add_parser(operation)
        option.add_argument('--revision',required=True)
        option.add_argument('--profile',choices=('lite','ab'),required=True)
        option.add_argument('--country',required=True)
        option.add_argument('--workspace',type=Path,required=True)
        option.add_argument('--out',type=Path,required=True)
        option.add_argument('--captures-mib',type=int,default=512)
        for name in ('cert','key','keyring'):
            option.add_argument('--'+name,type=Path)
        option.add_argument('--version')
    for operation in ('verify','inspect'):
        option=operations.add_parser(operation)
        option.add_argument('--candidate',type=Path,required=True)
        if operation=='verify':
            option.add_argument('--trusted-sha256')
            option.add_argument('--keyring',type=Path)
            option.add_argument('--scratch-dir',type=Path)
    args=parser.parse_args(argv)
    if args.mode=='factory':
        try:
            if args.factory_operation in ('build','preflight'):
                call=factory.build if args.factory_operation=='build' else factory.preflight
                result=call(ROOT,revision=args.revision,profile=args.profile,country=args.country,
                    workspace=args.workspace,output=args.out,cert=args.cert,key=args.key,keyring=args.keyring,
                    version=args.version,captures_mib=args.captures_mib)
            elif args.factory_operation=='verify':
                result=factory.verify(args.candidate,trusted_sha256=args.trusted_sha256,keyring=args.keyring,temporary_root=args.scratch_dir)
            else:
                result=factory.inspect(args.candidate)
        except (OSError,ValueError,TypeError,KeyError,ImportError) as error:
            result={'schema':1,'result':'FAIL','checks':[{'status':'FAIL','name':'Factory operation','detail':str(error)}]}
        if args.json:
            print(json.dumps(result,sort_keys=True,allow_nan=False))
        else:
            for row in result.get('checks',[]):
                print(f"{row['status']:7} {row['name']}: {row['detail']}")
            print('Factory: '+result.get('state',result.get('operation','failed'))+'; result '+result['result'])
            if result.get('failure_detail'):
                print('Failed stage: '+result.get('stage','unknown')+'; '+result['failure_detail'])
            print(result.get('note',result.get('recovery','No image, physical boot or rollback readiness inferred.')))
        return {'PASS':0,'FAIL':1,'UNKNOWN':3}[result['result']]
    if args.mode=='candidate':
        result=qualify(args.image,revision=args.revision,profile=args.profile,country=args.country,
                       trusted_sha256=args.trusted_sha256,bundle=args.bundle,keyring=args.keyring,version=args.version,temporary_root=args.scratch_dir)
    elif args.mode=='rauc':
        try:
            context=json_record(read(args.context.absolute().parent,args.context.name,8192)) if args.context else None
        except (OSError,ValueError) as exc:
            parser.error('invalid saved context: '+str(exc))
        result=inspect_configuration(args.root,revision=args.revision,context=context)
    else:
        result=recovery_plan(args.root,trial=args.trial)
    if args.json:
        print(json.dumps(result,sort_keys=True,allow_nan=False))
    else:
        for row in result['checks']:
            print(f"{row['status']:7} {row['name']}: {row['detail']}")
        if 'condition' in result: print('Configuration condition: '+result['condition'])
        if 'phase' in result: print('Recovery phase: '+result['phase'])
        for action in result.get('actions',[]): print('PLAN ONLY: '+action)
        print('Result: '+result['result']+'; no installation or recovery authorized/executed')
        if 'offline_qualified' in result: print('Offline candidate qualified: '+str(result['offline_qualified']))
    return {'PASS':0,'FAIL':1,'UNKNOWN':3}[result['result']]


if __name__=='__main__':
    raise SystemExit(main())
