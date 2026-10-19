"""Beta gates consume this execution's evidence, never infer physical safety."""
from xml.etree.ElementTree import Element, SubElement, ElementTree

import pytest
from redux.core.beta_readiness import assess, test_evidence as evidence, INTEGRATION, UI
from test_qualification import module


def report(path, required=INTEGRATION | UI, *, fault=None):
    root = Element('testsuites')
    suite = SubElement(root, 'testsuite')
    for name in sorted(required):
        test = SubElement(suite, 'testcase', classname='tests.' + name, name='scenario')
        if fault:
            SubElement(test, fault)
    ElementTree(root).write(path)
    return path


def test_passed_software_never_means_physical_or_release_qualification(tmp_path):
    path = report(tmp_path / 'test.xml')
    value = assess([(name, 'PASS', 'ran') for name in ('Python suite', 'Image contracts', 'Python compile', 'Shell syntax', 'Diff whitespace')], path)
    assert value['offline_qualified'] is True
    assert value['beta_ready'] is False
    assert all(row['status'] == 'UNKNOWN' for row in value['matrix'][4:])


@pytest.mark.parametrize('fault,status', [('failure', 'FAIL'), ('error', 'FAIL'), ('skipped', 'UNKNOWN')])
def test_failed_and_skipped_executions_cannot_qualify(tmp_path, fault, status):
    assert evidence(report(tmp_path / 'x.xml', fault=fault), INTEGRATION)[0] == status


def test_absent_malformed_incomplete_or_linked_evidence_is_unknown(tmp_path):
    path = tmp_path / 'x.xml'
    assert evidence(path, UI)[0] == 'UNKNOWN'
    path.write_text('<invalid')
    assert evidence(path, UI)[0] == 'UNKNOWN'
    report(path, {'test_ui_state'})
    assert evidence(path, UI)[0] == 'UNKNOWN'
    link = tmp_path / 'link'
    link.symlink_to(path)
    assert evidence(link, UI)[0] == 'UNKNOWN'


def test_beta_runner_reuses_full_run_and_leaves_unknowns_visible(monkeypatch, tmp_path):
    from types import SimpleNamespace
    q = module('redux-qualify')
    monkeypatch.setattr(q, 'load', lambda _: SimpleNamespace(checks=lambda: []))
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        junit = next((arg.split('=', 1)[1] for arg in command if arg.startswith('--junitxml=')), None)
        if junit:
            report(junit)
        kwargs['stdout'].write(b'50 passed\n')
        return SimpleNamespace(returncode=0)
    output = tmp_path / 'beta.json'
    assert q.main(['--beta', '--beta-report', str(output)], runner=runner) == 3
    assert len(calls) == 5
    assert sum('pytest' in args for args in calls) == 1
    import json
    assert json.loads(output.read_text())['offline_qualified'] is True
    assert q.main(['--beta', '--beta-report', str(output)], runner=runner) == 1  # refuse overwrite


def test_compile_failure_prevents_offline_beta_qualification(tmp_path):
    checks = [(name, 'PASS', 'ran') for name in ('Python suite', 'Image contracts', 'Shell syntax', 'Diff whitespace')]
    checks.append(('Python compile', 'FAIL', 'syntax error'))
    value = assess(checks, report(tmp_path / 'x.xml'))
    assert value['offline_qualified'] is False
    assert value['matrix'][0]['status'] == 'FAIL'
