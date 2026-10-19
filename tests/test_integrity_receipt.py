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


def test_atomic_publisher_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / 'status.json'
        result = dict(result='pass', checked=1, ready_rows=1, issues={})
        module.publish_receipt(dest, result, completed_utc=1000)
        assert module.read_cached_receipt(dest, now=1001)['status'] == 'ok'
        assert dest.stat().st_mode & 0o077 == 0


def test_atomic_publisher_rejects_invalid_result():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / 'status.json'
        for bad in (None, [], {'result': 'pass'}, dict(result='pass', checked=0, ready_rows=0, issues={})):
            with unittest.TestCase().assertRaises(ValueError):
                module.publish_receipt(dest, bad, completed_utc=1000)
        assert not dest.exists()


def test_atomic_publisher_refuses_link_destination():
    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp) / 'private'
        outside.write_text('DO NOT TOUCH')
        link = Path(tmp) / 'receipt'
        link.symlink_to(outside)
        with unittest.TestCase().assertRaises(ValueError):
            module.publish_receipt(link, dict(result='pass', checked=1, ready_rows=1, issues={}), completed_utc=1000)
        assert outside.read_text() == 'DO NOT TOUCH'


def test_publisher_replaces_existing_regular_receipt_atomically():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "receipt.json"
        old = {"result": "attention", "checked": 1, "ready_rows": 1,
               "issues": {"artifact_missing": 1}}
        ok = {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
        module.publish_receipt(dest, old, completed_utc=1000)
        assert module.read_cached_receipt(dest, now=1001)["status"] == "attention"
        module.publish_receipt(dest, ok, completed_utc=1010)
        assert module.read_cached_receipt(dest, now=1011)["status"] == "ok"
        assert not list(Path(tmp).glob(".receipt-*"))


def test_publisher_refuses_untrusted_receipt_directory():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "shared"
        parent.mkdir()
        parent.chmod(0o777)
        dest = parent / "receipt.json"
        valid = {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
        with unittest.TestCase().assertRaises(ValueError):
            module.publish_receipt(dest, valid, completed_utc=1000)
        assert not dest.exists()


def test_publisher_rejects_symlink_directory():
    with tempfile.TemporaryDirectory() as tmp:
        real_parent = Path(tmp) / "private"
        real_parent.mkdir()
        linked_parent = Path(tmp) / "linked"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        dest = linked_parent / "receipt.json"
        valid = {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
        with unittest.TestCase().assertRaises(OSError):
            module.publish_receipt(dest, valid, completed_utc=1000)
        assert not (real_parent / "receipt.json").exists()


def test_failed_publication_leaves_old_receipt_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "receipt.json"
        dest.write_bytes(b"original receipt")
        invalid = {"result": "pass", "checked": 0, "ready_rows": 0, "issues": {}}
        with unittest.TestCase().assertRaises(ValueError):
            module.publish_receipt(dest, invalid, completed_utc=1000)
        assert dest.read_bytes() == b"original receipt"
        assert not list(Path(tmp).glob(".receipt-*"))


def test_cached_reader_rejects_symlinked_parent():
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp) / "private"
        real.mkdir()
        dest = real / "receipt.json"
        module.publish_receipt(dest,
            {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}},
            completed_utc=1000)
        symlink = Path(tmp) / "linked"
        symlink.symlink_to(real, target_is_directory=True)
        result = module.read_cached_receipt(symlink / "receipt.json", now=1001)
        assert result["status"] == "unknown"


def test_cached_reader_rejects_world_writable_parent():
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "untrusted"
        directory.mkdir()
        path = directory / "receipt.json"
        path.write_text(json.dumps(receipt()))
        directory.chmod(0o777)
        assert module.read_cached_receipt(path, now=1001)["status"] == "unknown"


def test_cached_reader_rejects_inplace_file_change(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "receipt.json"
        dest.write_text(json.dumps(receipt()))
        original_read = module.os.read
        modified = [False]

        def changing_read(fd, count):
            chunk = original_read(fd, count)
            if chunk and not modified[0]:
                modified[0] = True
                dest.write_bytes(b"x" * len(chunk))
            return chunk

        monkeypatch.setattr(module.os, "read", changing_read)
        assert module.read_cached_receipt(dest, now=1001)["status"] == "unknown"
        assert modified[0]


def test_huge_untrusted_timestamps_are_unknown_without_overflow():
    massive = 10 ** 500
    for invalid in [massive, -massive, float("inf"), float("-inf"), float("nan")]:
        bad_stamp = receipt(completed_utc=invalid)
        assert module.classify_receipt(bad_stamp, now=1001)["status"] == "unknown"
        assert module.classify_receipt(receipt(), now=invalid)["status"] == "unknown"
        assert module.classify_receipt(receipt(), now=1001, max_age=invalid)["status"] == "unknown"


def test_huge_timestamp_in_json_receipt_cannot_crash_reader():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipt.json"
        valid = receipt(completed_utc=10 ** 500)
        path.write_text(json.dumps(valid))
        assert module.read_cached_receipt(path, now=1001)["status"] == "unknown"


def test_huge_epoch_cannot_be_published():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "receipt.json"
        valid = {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
        with unittest.TestCase().assertRaises(ValueError):
            module.publish_receipt(path, valid, completed_utc=10 ** 500)
        assert not path.exists()


def test_untrusted_cached_json_extreme_numbers_are_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "receipt.json"
        for stamp in ["1e9999", "NaN", "Infinity", "-Infinity",
                      str(10 ** 500)]:
            dest.write_text(
                '{"schema":1,"completed_utc":' + stamp +
                ',"result":"pass","checked":1,"ready_rows":1,"issues":{}}')
            verdict = module.read_cached_receipt(dest, now=1001)
            assert verdict["status"] == "unknown"


def test_untrusted_cached_json_nonregular_or_truncated_is_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        directory = parent / "directory-receipt"
        directory.mkdir()
        assert module.read_cached_receipt(directory, now=1001)["status"] == "unknown"
        dest = parent / "receipt.json"
        dest.write_text('{"schema":1,"completed_utc":')
        assert module.read_cached_receipt(dest, now=1001)["status"] == "unknown"
        dest.write_bytes(b"x" * (module.MAX_RECEIPT_BYTES + 1))
        assert module.read_cached_receipt(dest, now=1001)["status"] == "unknown"


def test_invalid_receipt_cannot_demote_existing_ok_cache():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "receipt.json"
        valid = {"result": "pass", "checked": 1, "ready_rows": 1, "issues": {}}
        module.publish_receipt(dest, valid, completed_utc=1000)
        original = dest.read_bytes()
        invalid = {"result": "attention", "checked": 1, "ready_rows": 1,
                   "issues": {"artifact_missing": 2}}
        with unittest.TestCase().assertRaises(ValueError):
            module.publish_receipt(dest, invalid, completed_utc=1001)
        assert dest.read_bytes() == original


def test_deeply_nested_cache_json_cannot_crash_live_consumer():
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "receipt.json"
        destination.write_text("[" * 2000 + "0" + "]" * 2000)
        report = module.read_cached_receipt(destination, now=1001)
        assert report["status"] == "unknown"
        assert report["reason"] in {"receipt_unavailable", "unverified_receipt"}


def test_cached_reader_catches_parser_recursion_error(tmp_path, monkeypatch):
    source = tmp_path / "receipt.json"
    source.write_text(json.dumps(receipt()))

    def parser_recursion(*args, **kwargs):
        raise RecursionError("malformed nested JSON")

    monkeypatch.setattr(module.json, "loads", parser_recursion)
    result = module.read_cached_receipt(source, now=1001)
    assert result["status"] == "unknown"
    assert result["reason"] == "receipt_unavailable"


def test_global_and_row_discrepancies_remain_actionable():
    # Auditor can discover missing content, then discover its pinned output
    # directory was also replaced. Two problems from one checked row are valid.
    combined = receipt(result='attention', checked=1, ready_rows=1,
                       issues={'artifact_missing': 1,
                               'output_directory_changed': 1})
    assert module.classify_receipt(combined, now=1001)['status'] == 'attention'

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / 'integrity.json'
        module.publish_receipt(dest, {
            'result': 'attention', 'checked': 1, 'ready_rows': 1,
            'issues': {'artifact_missing': 1,
                       'output_directory_changed': 1},
        }, completed_utc=1000)
        assert module.read_cached_receipt(dest, now=1001)['status'] == 'attention'


def test_row_limit_rejection_preserves_attention_evidence():
    over_limit = receipt(result='attention', checked=0, ready_rows=10001,
                         issues={'row_limit_exceeded': 1})
    assert module.classify_receipt(over_limit, now=1001)['status'] == 'attention'
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'receipt.json'
        module.publish_receipt(path, {
            'result': 'attention', 'checked': 0, 'ready_rows': 10001,
            'issues': {'row_limit_exceeded': 1},
        }, completed_utc=1000)
        assert module.read_cached_receipt(path, now=1001)['status'] == 'attention'


def test_impossible_global_and_row_counters_stay_unknown():
    cases = [
        receipt(result='attention', checked=1, ready_rows=1,
                issues={'artifact_missing': 2, 'output_directory_changed': 1}),
        receipt(result='attention', checked=1, ready_rows=1,
                issues={'output_directory_changed': 2}),
        receipt(result='attention', checked=0, ready_rows=10001,
                issues={'artifact_missing': 1, 'row_limit_exceeded': 1}),
        receipt(result='attention', checked=0, ready_rows=1,
                issues={'row_limit_exceeded': 1}),
        receipt(result='attention', checked=0, ready_rows=2 ** 63,
                issues={'row_limit_exceeded': 1}),
    ]
    for item in cases:
        assert module.classify_receipt(item, now=1001)['status'] == 'unknown'


def test_ledger_replacement_receipt_is_actionable():
    row = receipt(result='attention', checked=1, ready_rows=1,
                  issues={'ledger_changed': 1})
    assert module.classify_receipt(row, now=1001)['status'] == 'attention'
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / "integrity.json"
        module.publish_receipt(destination, {
            "result": "attention", "checked": 1, "ready_rows": 1,
            "issues": {"ledger_changed": 1},
        }, completed_utc=1000)
        assert module.read_cached_receipt(destination, now=1001)['status'] == 'attention'
