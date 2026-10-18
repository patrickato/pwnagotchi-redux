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
