"""Lab notebook: record a session timeline + render a Markdown report."""
from redux.notebook import LabNotebook, NotebookEntry


def test_record_and_entries(tmp_path):
    nb = LabNotebook(tmp_path / "nb.jsonl", now=lambda: 1000.0)
    nb.record("capture", "handshake from dad24", detail={"bssid": "aa:bb"})
    nb.record("crack", "recovered PSK")
    es = nb.entries()
    assert len(es) == 2 and es[0].kind == "capture"
    assert es[0].detail["bssid"] == "aa:bb"


def test_summary_counts(tmp_path):
    nb = LabNotebook(tmp_path / "nb.jsonl")
    for k in ("recon", "capture", "capture", "crack"):
        nb.record(k, k)
    s = nb.summary()
    assert s["entries"] == 4 and s["by_kind"]["capture"] == 2


def test_report_markdown(tmp_path):
    nb = LabNotebook(tmp_path / "nb.jsonl", now=lambda: 1000.0)
    nb.record("capture", "handshake from dad24")
    nb.record("signal", "catalogued garage remote @ 390 MHz")
    rep = nb.report(title="Tinker shop session")
    assert rep.startswith("# Tinker shop session")
    assert "## Timeline" in rep
    assert "handshake from dad24" in rep and "garage remote" in rep
    assert "capture" in rep and "signal" in rep


def test_report_empty(tmp_path):
    rep = LabNotebook(tmp_path / "nb.jsonl").report()
    assert "No activity recorded" in rep


def test_clear(tmp_path):
    nb = LabNotebook(tmp_path / "nb.jsonl")
    nb.record("note", "x")
    assert "cleared" in nb.clear()
    assert nb.entries() == []
    assert "already empty" in nb.clear()


def test_corrupt_lines_tolerated(tmp_path):
    p = tmp_path / "nb.jsonl"
    p.write_text('{"ts":1,"kind":"note","summary":"ok","detail":{}}\nGARBAGE\n\n')
    assert len(LabNotebook(p).entries()) == 1
