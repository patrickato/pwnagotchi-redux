"""Pure offline audit receipt contract tests."""
import importlib.util
import json
import tempfile
from pathlib import Path
import unittest

FILE = Path(__file__).resolve().parents[1] / 'redux/crack/integrity_receipt.py'
spec = importlib.util.spec_from_file_location('integrity_receipt', FILE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def receipt(**changes):
    row = dict(schema=1, completed_utc=1000, result='pass', checked=2,
               ready_rows=2, issues={})
    row.update(changes)
    return row


class IntegrityReceiptTests(unittest.TestCase):
    def test_verified(self):
        self.assertEqual(module.classify_receipt(receipt(), now=1010)['status'], 'ok')

    def test_empty_unknown(self):
        row = receipt(result='unknown', checked=0, ready_rows=0)
        self.assertEqual(module.classify_receipt(row, now=1001)['reason'], 'no_ready_artifacts')

    def test_failed_artifact(self):
        row = receipt(result='attention', issues={'artifact_missing': 1})
        self.assertEqual(module.classify_receipt(row, now=1001)['status'], 'attention')

    def test_stale(self):
        self.assertEqual(module.classify_receipt(receipt(), now=5000)['reason'], 'stale_receipt')

    def test_future(self):
        self.assertEqual(module.classify_receipt(receipt(completed_utc=2000), now=1000)['reason'], 'invalid_timestamp')

    def test_malformed_and_unknown_issue(self):
        self.assertEqual(module.classify_receipt(receipt(issues={'secret_file': 1}), now=1001)['status'], 'unknown')
        self.assertEqual(module.classify_receipt(receipt(schema=True), now=1001)['status'], 'unknown')
        self.assertEqual(module.classify_receipt(receipt(ready_rows=-2), now=1001)['status'], 'unknown')

    def test_false_pass(self):
        for row in (receipt(checked=0, ready_rows=0), receipt(checked=1),
                    receipt(issues={'artifact_missing': 1}), receipt(result='wrong')):
            self.assertEqual(module.classify_receipt(row, now=1001)['status'], 'unknown')

    def test_contradictory_issue_totals(self):
        forged = receipt(result='attention', checked=1, ready_rows=1,
                         issues={'artifact_missing': 2})
        self.assertEqual(module.classify_receipt(forged, now=1001)['status'], 'unknown')

    def test_valid_single_issue(self):
        actual = receipt(result='attention', checked=1, ready_rows=1,
                         issues={'artifact_missing': 1})
        self.assertEqual(module.classify_receipt(actual, now=1001)['status'], 'attention')

    def test_cached_receipt_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps(receipt()))
            self.assertEqual(module.read_cached_receipt(path, now=1001)['status'], 'ok')
            path.write_text('{"schema":1,"schema":1}')
            self.assertEqual(module.read_cached_receipt(path, now=1001)['status'], 'unknown')

    def test_cached_receipt_rejects_symlinks_and_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipt.json"
            path.write_text(json.dumps(receipt()))
            link = Path(tmp) / "link"
            link.symlink_to(path)
            self.assertEqual(module.read_cached_receipt(link, now=1001)['status'], 'unknown')
            path.write_bytes(b'x' * (module.MAX_RECEIPT_BYTES + 1))
            self.assertEqual(module.read_cached_receipt(path, now=1001)['status'], 'unknown')

    def test_limits(self):
        self.assertEqual(module.classify_receipt(receipt(ready_rows=10001), now=1001)['status'], 'unknown')
        self.assertEqual(module.classify_receipt(receipt(completed_utc=float('nan')), now=1001)['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()

def test_source_has_no_accidental_literal_newline_escape():
    # Regression: an escaped newline inside an indented comment can swallow
    # the assignment on its right, leaving 'reported' undefined at runtime.
    source = FILE.read_text(encoding='utf-8')
    assert r'\\n    reported' not in source


def test_all_receipt_classifications_execute_without_name_error():
    examples = [
        receipt(),
        receipt(result='unknown', checked=0, ready_rows=0),
        receipt(result='attention', issues={'artifact_missing': 1}),
        receipt(result='attention', issues={'artifact_missing': 2}),
    ]
    for item in examples:
        answer = module.classify_receipt(item, now=1001)
        assert answer['status'] in {'ok', 'unknown', 'attention'}
