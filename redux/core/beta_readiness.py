"""Offline beta acceptance from executed gates, never hardware inference."""
from pathlib import Path
import xml.etree.ElementTree as ET

INTEGRATION = frozenset({'test_platform_lifecycle', 'test_live_runtime', 'test_resource_telemetry',
    'test_live_health', 'test_live_usb_lease', 'test_dual_radio_readiness',
    'test_live_pipeline_e2e', 'test_capture_pipeline_wiring', 'test_capture_recovery'})
UI = frozenset({'test_ui_state', 'test_web_refresh', 'test_fbui_gesture_integration', 'test_touch_reconnect',
    'test_fbui', 'test_gesture_nav', 'test_web_navigation', 'test_capture_tft_panel',
    'test_radio_tft_panel', 'test_display_preview', 'test_touch_nav',
    'test_doctor_tft_scroll', 'test_telemetry_history'})


def test_evidence(path, required):
    """Require execution of every named contract; skipped tests are UNKNOWN."""
    if path is None:
        return 'UNKNOWN', 'JUnit execution evidence unavailable'
    try:
        path = Path(path)
        if path.is_symlink() or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
            raise ValueError('invalid test evidence')
        root = ET.fromstring(path.read_bytes())
    except (OSError, ValueError, ET.ParseError):
        return 'UNKNOWN', 'JUnit execution evidence unavailable'
    seen, count, skipped, failed = set(), 0, 0, 0
    for case in root.iter('testcase'):
        parts = set(case.get('classname', '').split('.'))
        matches = required & parts
        if not matches:
            continue
        seen.update(matches)
        count += 1
        skipped += case.find('skipped') is not None
        failed += case.find('failure') is not None or case.find('error') is not None
    if failed:
        return 'FAIL', f'{failed} failing contract(s) in {count} executed cases'
    if seen != required or skipped:
        return 'UNKNOWN', f'missing contracts: {", ".join(sorted(required - seen)) or "none"}; {skipped} skipped'
    return 'PASS', f'{count} executed cases; no failures or skips'


def assess(checks, junit):
    gates = {name: (state, detail) for name, state, detail in checks}
    def gate(name):
        return gates.get(name, ('UNKNOWN', 'check not executed'))
    required = ('Python suite', 'Python compile', 'Shell syntax', 'Diff whitespace')
    states = [gate(name)[0] for name in required]
    offline = 'FAIL' if 'FAIL' in states else 'UNKNOWN' if 'UNKNOWN' in states else 'PASS'
    detail = '; '.join(name + ': ' + gate(name)[0] for name in required)
    entries = [
        ('offline_software', offline, detail),
        ('integration_tests', *test_evidence(junit, INTEGRATION)),
        ('ui_behavior', *test_evidence(junit, UI)),
        ('image_contracts', *gate('Image contracts')),
        ('release_candidate', 'UNKNOWN', 'Run redux-release.py candidate with actual signed release artifacts'),
        ('physical_rf_display', 'UNKNOWN', 'Operator Pi 4/5 RF and display acceptance is still required'),
        ('actual_update_rollback', 'UNKNOWN', 'Physical install, failed-boot rollback and recovery evidence required'),
    ]
    software = all(state == 'PASS' for _, state, _ in entries[:4])
    return {'schema': 1, 'offline_qualified': software, 'beta_ready': False,
            'assessment': 'offline-qualified / physical gates open' if software else 'software qualification incomplete',
            'matrix': [{'gate': name, 'status': state, 'detail': detail} for name, state, detail in entries]}


def acceptance_matrix(source, candidate, hardware):
    """Combine freshly executed source/candidate gates with separate attestations.

    Callers must execute the validators; imported PASS JSON is not execution.
    Unknown physical rows are never filled from source or image test results.
    """
    matrix = assess([], None)['matrix']
    for row in matrix:
        if row['gate']=='offline_software' and source.get('status')=='FAIL':
            row.update(status='FAIL',detail='executed source qualification failed')
        if source.get('status') == 'PASS':
            match = next((r for r in source.get('matrix', []) if r['gate']==row['gate']), None)
            if match:row.update(status=match['status'], detail='executed exact-revision source qualification')
        if row['gate']=='release_candidate':
            row.update(status=candidate['status'], detail='actual candidate verification' if candidate.get('reason')=='actual_artifact_gates_executed' else 'actual candidate not verified')
    matrix.append({'gate':'hardware_attestations','status':hardware['result'],
                   'detail':'separate externally scoped attestations; physical certification requires operator review'})
    return matrix
