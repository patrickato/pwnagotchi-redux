#!/usr/bin/env python3
"""Read-only, privacy-preserving audit of a Redux /api/status JSON snapshot.

Intended for offline use against a saved status response. Never accesses radio,
network, service state, or files under /captures itself.
"""
import argparse
import json
import math
import sys
import time

SEVERITIES = {'ok', 'attention', 'degraded', 'action', 'unknown'}
RANK = {'ok': 0, 'attention': 1, 'degraded': 2, 'action': 3}


def audit(snapshot, now=None):
    now = time.time() if now is None else now
    issues = []
    def flag(code, message):
        issues.append({'code': code, 'message': message})
    if not isinstance(snapshot, dict):
        return [{'code': 'root_type', 'message': 'Status response is not an object'}]
    runtime = snapshot.get('runtime')
    if not isinstance(runtime, dict):
        flag('runtime_missing', 'Runtime object missing; supervisor state unverified')
        runtime = {}
    doctor = snapshot.get('doctor')
    if not isinstance(doctor, dict):
        flag('doctor_missing', 'Doctor report missing; health unverified')
        doctor = {}
    stamp = runtime.get('updated_utc')
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
        flag('runtime_timestamp', 'Runtime timestamp absent or invalid')
    elif stamp > now + 30:
        flag('runtime_future', 'Runtime timestamp ahead of the reference clock')
    elif now - stamp > 15:
        flag('runtime_stale', 'Runtime checkpoint older than 15 seconds')
    findings = doctor.get('findings')
    if not isinstance(findings, list):
        flag('findings_missing', 'Doctor findings missing')
        findings = []
    assessed = []
    unknown = []
    known = []
    for finding in findings:
        if not isinstance(finding, dict):
            flag('finding_type', 'Doctor finding is not an object')
            continue
        area, status = finding.get('area'), finding.get('status')
        if not isinstance(area, str) or not area:
            flag('finding_area', 'Doctor finding missing an area name')
        if status not in SEVERITIES:
            flag('finding_status', 'Doctor finding has an invalid status')
            continue
        if status == 'unknown':
            unknown.append(area)
        else:
            assessed.append(area)
            known.append(status)
    coverage = doctor.get('coverage')
    if not isinstance(coverage, dict):
        flag('coverage_missing', 'Doctor coverage missing')
        coverage = {}
    if 'not_assessed' in coverage and coverage['not_assessed'] != unknown:
        flag('coverage_unknown_mismatch', 'Doctor coverage and unknown findings disagree')
    if 'assessed' in coverage and coverage['assessed'] != assessed:
        flag('coverage_assessed_mismatch', 'Doctor coverage and assessed findings disagree')
    expected = max(known, key=RANK.get) if known else 'unknown'
    if findings and doctor.get('overall') != expected:
        flag('doctor_overall_mismatch', 'Doctor overall severity differs from findings')
    if isinstance(runtime.get('health_unknown_areas'), int) and runtime['health_unknown_areas'] != len(unknown):
        flag('runtime_unknown_mismatch', 'Runtime unknown-area count differs from Doctor report')
    if runtime.get('health') is not None and doctor.get('overall') is not None and runtime['health'] != doctor['overall']:
        flag('runtime_health_mismatch', 'Runtime health differs from Doctor severity')
    processing = snapshot.get('capture_processing')
    if not isinstance(processing, dict):
        flag('processing_missing', 'Capture processing report missing')
    elif processing.get('available') is True:
        scan = processing.get('last_scan')
        if not isinstance(scan, dict):
            flag('processing_scan_unknown', 'Processing available but no last-scan evidence')
        else:
            completed = scan.get('completed_utc')
            if isinstance(completed, (float, int)) and not isinstance(completed, bool) and math.isfinite(completed):
                if completed > now + 30:
                    flag('processing_future', 'Processing scan timestamp in future')
                elif now - completed > 180:
                    flag('processing_stale', 'Processing scan is more than 180 seconds old')
            else:
                flag('processing_timestamp', 'Processing last-scan timestamp unavailable')
    visual = runtime.get('visual_sampled_utc')
    if visual is not None:
        if not isinstance(visual, (float, int)) or isinstance(visual, bool) or not math.isfinite(visual):
            flag('visual_timestamp', 'Visual sample timestamp invalid')
        elif isinstance(stamp, (float, int)) and not isinstance(stamp, bool) and math.isfinite(stamp) and visual > stamp + 30:
            flag('visual_future', 'Visual sample newer than the runtime checkpoint')
        elif now - visual > 20:
            flag('visual_stale', 'Visual sample is more than 20 seconds old')
    # Do not infer capture existence from capture_file: planned path != written capture.
    return issues


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', default='-', help='Saved status JSON file (default: stdin)')
    parser.add_argument('--now', type=float, default=None, help='Reference UTC epoch for replay/testing')
    args = parser.parse_args(argv)
    try:
        if args.file == '-':
            data = json.load(sys.stdin)
        else:
            with open(args.file, encoding='utf-8') as inp:
                data = json.load(inp)
    except (OSError, ValueError) as exc:
        print('Invalid status input: ' + type(exc).__name__, file=sys.stderr)
        return 2
    issues = audit(data, args.now)
    print(json.dumps({'result': 'pass' if not issues else 'attention', 'issue_count': len(issues), 'issues': issues}, indent=2))
    return 0 if not issues else 1


if __name__ == '__main__':
    raise SystemExit(main())
