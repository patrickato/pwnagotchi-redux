"""`redux oui` — vendor lookup, classification, and full-DB import via the CLI."""
import io
import contextlib
import json

from redux.cli import main


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_lookup_known_vendor():
    rc, out = _run(["oui", "lookup", "b8:27:eb:11:22:33"])
    assert rc == 0 and "RaspberryPi" in out


def test_classify_randomized():
    rc, out = _run(["oui", "classify", "da:a1:19:44:55:66"])
    assert rc == 0
    d = json.loads(out)
    assert d["randomized"] is True and d["vendor"] is None


def test_stats_reports_counts():
    rc, out = _run(["oui", "stats"])
    assert rc == 0
    d = json.loads(out)
    assert d["prefixes"] >= d["builtin_prefixes"] > 0


def test_import_merges_and_is_then_resolvable(tmp_path):
    src = tmp_path / "oui.txt"
    src.write_text("1A-2B-3C   (hex)\t\tAcme Test Labs\n")
    dest = tmp_path / "oui.tsv"
    rc, out = _run(["oui", "import", str(src), "--dest", str(dest)])
    assert rc == 0 and "imported" in out
    # and it parses back out of the normalized TSV
    from redux.geo import oui as O
    merged = O.full_table(extra_paths=[str(dest)])
    assert merged["1a2b3c"] == "Acme Test Labs"


def test_import_bad_source_fails(tmp_path):
    src = tmp_path / "junk"; src.write_text("nope\n")
    rc, out = _run(["oui", "import", str(src), "--dest", str(tmp_path / "o.tsv")])
    assert rc == 3 and "refused" in out
