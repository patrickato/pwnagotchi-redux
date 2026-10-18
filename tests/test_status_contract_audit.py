import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).resolve().parent.parent / 'scripts' / 'status_contract_audit.py'
spec = importlib.util.spec_from_file_location('status_contract_audit', P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def sample():
    return {'runtime': {'updated_utc': 1000, 'health': 'attention', 'health_unknown_areas': 1,
                        'visual_sampled_utc': 997, 'capture_file': '/captures/active/planned.pcap'},
            'doctor': {'overall': 'attention', 'findings': [
                {'area': 'thermal/power', 'status': 'unknown'},
                {'area': 'location', 'status': 'attention'}],
                'coverage': {'assessed': ['location'], 'not_assessed': ['thermal/power']}},
            'capture_processing': {'available': True, 'last_scan': {'completed_utc': 990}}}


class AuditTests(unittest.TestCase):
    def codes(self, data, now=1002):
        return {x['code'] for x in mod.audit(data, now)}

    def test_valid_status_with_planned_nonexistent_capture(self):
        self.assertEqual(self.codes(sample()), set())

    def test_stale_runtime(self):
        self.assertIn('runtime_stale', self.codes(sample(), now=1100))

    def test_doctor_and_runtime_health_mismatch(self):
        data = sample()
        data['runtime']['health'] = 'ok'
        self.assertIn('runtime_health_mismatch', self.codes(data))

    def test_unknown_area_mismatch(self):
        data = sample()
        data['runtime']['health_unknown_areas'] = 0
        self.assertIn('runtime_unknown_mismatch', self.codes(data))

    def test_overall_not_equal_worst_assessed(self):
        data = sample()
        data['doctor']['overall'] = 'ok'
        self.assertIn('doctor_overall_mismatch', self.codes(data))

    def test_coverage_drift(self):
        data = sample()
        data['doctor']['coverage']['not_assessed'] = []
        self.assertIn('coverage_unknown_mismatch', self.codes(data))

    def test_stale_processing(self):
        self.assertIn('processing_stale', self.codes(sample(), now=1200))

    def test_invalid_numeric_timestamp(self):
        data = sample()
        data['runtime']['updated_utc'] = float('nan')
        self.assertIn('runtime_timestamp', self.codes(data))

    def test_empty_or_wrong_input(self):
        self.assertIn('runtime_missing', self.codes({}))
        self.assertIn('root_type', self.codes([]))


if __name__ == '__main__':
    unittest.main()
