"""Contract tests for the one-command, read-only field status qualifier."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "field_status", Path(__file__).resolve().parents[1] / "scripts" / "field-status.py")
field = importlib.util.module_from_spec(spec)
spec.loader.exec_module(field)


class StatusQualificationTests(unittest.TestCase):
    def test_missing_telemetry_not_claimed_successful(self):
        checks = dict((name, state) for name, state, _ in field.assess(
            {"doctor_overall": "attention", "areas": {"thermal/power": "unknown"},
             "telemetry": None}))
        self.assertEqual(checks["Doctor report"], "PASS")
        self.assertEqual(checks["Doctor thermal assessment"], "UNKNOWN")
        self.assertEqual(checks["Telemetry integration"], "UNKNOWN")

    def test_fresh_real_values(self):
        data = {"doctor_overall": "ok", "areas": {"thermal/power": "ok"},
                "acceptance": {"checkpoint_freshness": "fresh"},
                "telemetry": {"fresh": True, "values": {
                    "cpu_temp_c": 46.2, "cpu_load_pct": 12.1, "ram_pct": 25.0,
                    "cpu_frequency_mhz": 1800}}}
        self.assertTrue(all(status == "PASS" for _, status, _ in field.assess(data)))

    def test_stale_fails(self):
        data = {"doctor_overall": "attention", "areas": {"thermal/power": "unknown"},
                "telemetry": {"fresh": False, "values": {}}}
        checks = dict((name, state) for name, state, _ in field.assess(data))
        self.assertEqual(checks["Telemetry freshness"], "FAIL")
        self.assertEqual(checks["cpu_temp_c"], "UNKNOWN")

    def test_empty_and_error_do_not_pass(self):
        self.assertEqual(field.assess({})[0][1], "FAIL")
        self.assertEqual(field.assess({"error": "TimeoutError"})[0][1], "FAIL")

    def test_ssh_flags_protect_host_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "id"
            key.write_text("placeholder")
            calls = []
            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0,
                    '{"doctor_overall":"unknown","areas":{},"telemetry":null}', "")
            field.fetch("192.168.12.115", key, runner=runner)
            argv, options = calls[0]
            self.assertIn("StrictHostKeyChecking=yes", argv)
            self.assertIn("BatchMode=yes", argv)
            self.assertNotIn("sudo", argv)
            self.assertIn("capture_output", options)
            self.assertTrue(options["capture_output"])

    def test_failed_ssh_does_not_disclose_stderr(self):
        with self.assertRaisesRegex(RuntimeError, "remote read-only probe failed"):
            field.fetch("192.168.12.115", Path("/not-used"),
                        runner=lambda argv, **kw: subprocess.CompletedProcess(
                            argv, 255, "", "SECRET CREDENTIAL"))


if __name__ == "__main__":
    unittest.main()


def test_sanitizer_never_exports_identifiers_reasons_or_paths():
    import json
    raw = {'doctor': {'overall':'degraded','findings':[{'area':'thermal/power','status':'degraded',
            'reason':'SECRET SSID 00:11:22:33:44:55'}]},
           'runtime':{'updated_utc':100,'state':'running','capture_file':'/secret/pcap','handed_off':1},
           'access_points':[{'ssid':'SECRET'}],
           'telemetry':{'fresh':True,'measured_at':99,'age_seconds':1,
                        'values':{'cpu_temp_c':45,'cpu_load_pct':float('nan'),'ram_pct':True,
                                  'on_battery':None,'throttled_mask':'SECRET'},
                        'provenance':{'cpu_temp_c':'SECRET'}}}
    clean = field.sanitize(raw, 100)
    encoded = json.dumps(clean, allow_nan=False)
    assert 'SECRET' not in encoded and '00:11' not in encoded and 'capture_file' not in encoded
    assert clean['telemetry']['fresh']
    assert clean['telemetry']['values']['ram_pct'] is None
    assert clean['telemetry']['values']['cpu_load_pct'] is None
    checks = {name:state for name,state,_ in field.assess(clean)}
    assert checks['Doctor health'] == checks['Doctor thermal assessment'] == 'FAIL'


def test_cached_fresh_bit_cannot_override_old_or_missing_timestamp():
    for stamp in [1,101,None,'invalid',float('nan')]:
        clean = field.sanitize({'telemetry':{'fresh':True,'measured_at':stamp,'age_seconds':0,
                               'values':{'cpu_temp_c':45}}},100)
        assert not clean['telemetry']['fresh']
        checks = {name:state for name,state,_ in field.assess(clean)}
        assert checks['Telemetry freshness'] == 'FAIL'
        assert checks['cpu_temp_c'] == 'UNKNOWN'


def test_remote_program_executes_the_tested_sanitizer_without_network(monkeypatch, capsys):
    import urllib.request
    import json
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            assert size == 1024 * 1024 + 1
            return b'{"doctor":{"overall":"unknown"},"SECRET":"never exported"}'
    class Opener:
        def open(self, url, timeout):
            assert url == 'http://127.0.0.1:8080/api/status' and timeout == 3
            return Response()
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *args: Opener())
    exec(field.REMOTE, {})
    out = capsys.readouterr().out
    assert 'SECRET' not in out
    assert json.loads(out)['doctor_overall'] == 'unknown'


def test_fetch_rejects_wrong_root_and_oversized_output():
    for text in ['[]', 'x' * 65537]:
        with __import__('pytest').raises(RuntimeError, match='invalid remote JSON'):
            field.fetch('192.168.1.2', Path('/not-used'), runner=lambda args, **kw:
                        subprocess.CompletedProcess(args, 0, text, ''))
