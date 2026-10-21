"""Offline-only file/runner hygiene fixtures; no subprocess touches the Pi."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

TOOL = Path(__file__).resolve().parents[1] / 'scripts/redux-field-feedback.py'
spec=importlib.util.spec_from_file_location('field_feedback_cli',TOOL)
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def private(tmp_path, name='session-'+'a'*32+'.json'):
    state=tmp_path/'private'
    state.mkdir(mode=0o700)
    file=state/name
    file.write_text('{}')
    file.chmod(0o600)
    return state,file


def projected():
    return {'schema':1,'result':'UNKNOWN','physical_acceptance':'UNKNOWN',
      'evidence_origin':'saved_unsigned_session','checks':[],
      'observations':{'radio_inventory_details':[{'radio':1,'transport':'usb',
          'kernel_mode':'monitor','arphrd':803,'private_mac':'HIGHLY_PRIVATE'}]}}


def test_accepts_latest_safe_private_report(tmp_path):
    state,file=private(tmp_path)
    assert app.newest_private(state)==file


def test_missing_or_unsafe_directory_rejected(tmp_path):
    state,file=private(tmp_path)
    state.chmod(0o755)
    with pytest.raises(ValueError): app.newest_private(state)
    state.chmod(0o700)
    file.chmod(0o644)
    with pytest.raises(ValueError): app.newest_private(state)


def test_symlink_and_invalid_filename_never_selected(tmp_path):
    state,file=private(tmp_path)
    file.unlink()
    outside=tmp_path/'outside';outside.write_text('SECRET');outside.chmod(0o600)
    file.symlink_to(outside)
    (state/'other.json').write_text('{}')
    with pytest.raises(ValueError): app.newest_private(state)
    assert outside.read_text()=='SECRET'


def test_projection_only_launches_existing_offline_inspector(tmp_path):
    _,file=private(tmp_path)
    calls=[]
    def fake(argv,**kwargs):
        calls.append(argv)
        assert argv[-3:]==['session-inspect','--session-file',str(file)]
        assert 'session-collect' not in argv
        assert kwargs['timeout']==20
        return SimpleNamespace(returncode=3,stdout=json.dumps(projected()))
    data=json.loads(app.collect_feedback(file,runner=fake))
    assert len(calls)==1
    assert data['radios'][0]['link_type']==803
    assert 'HIGHLY_PRIVATE' not in json.dumps(data)
    assert data['physical_acceptance']=='UNKNOWN'


def test_refuses_suspicious_inspection_exit(tmp_path):
    _,file=private(tmp_path)
    for exit_code in [0,1,124]:
        with pytest.raises(ValueError): app.collect_feedback(file,runner=lambda *a,**kw:
                          SimpleNamespace(returncode=exit_code,stdout=json.dumps(projected())))


def test_no_report_is_fixed_redacted_unknown(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(app,'STATE',tmp_path/'missing')
    assert app.main(['--latest-private'])==3
    value=json.loads(capsys.readouterr().out)
    assert value=={'schema':1,'format':'redux-field-feedback-v1',
                   'status':'unavailable','physical_acceptance':'UNKNOWN'}



def test_offline_publisher_rejects_invalid_filtered_schema(tmp_path, monkeypatch):
    """No extra fields may escape from even a compromised converter."""
    from types import SimpleNamespace
    from redux import field_feedback
    _, source = private(tmp_path)
    safe = field_feedback.facts(projected())
    safe["private_network_identifier"] = "NEVER_EXPOSE_THIS"
    monkeypatch.setattr(field_feedback, "facts", lambda *args: safe)
    with pytest.raises(ValueError, match="invalid_feedback_shape"):
        app.collect_feedback(source, runner=lambda *args, **kwargs:
            SimpleNamespace(returncode=3, stdout=json.dumps(projected())))



def test_feedback_refuses_stale_or_future_dated_private_reports(tmp_path):
    import os
    state, file = private(tmp_path)
    os.utime(file, (1000.0, 1000.0))
    with pytest.raises(ValueError, match="no_private_reports"):
        app.newest_private(state, now=1000.0 + 2 * 3600 + 1)
    with pytest.raises(ValueError, match="no_private_reports"):
        app.newest_private(state, now=1000.0 - 5 * 60 - 1)
    assert app.newest_private(state, now=1000.0 + 2 * 3600) == file


def test_feedback_prefers_newest_nonstale_private_report(tmp_path):
    import os
    state, old_file = private(tmp_path)
    new_file = state / ("session-" + "b" * 32 + ".json")
    new_file.write_text("{}")
    new_file.chmod(0o600)
    os.utime(old_file, (1000.0, 1000.0))
    os.utime(new_file, (8000.0, 8000.0))
    assert app.newest_private(state, now=8500.0) == new_file
