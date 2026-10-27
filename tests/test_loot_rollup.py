"""Loot rollup — one aggregated view across every loot source."""
from redux.loot.rollup import rollup


class _Entry:
    def __init__(self, kind):
        self.kind = kind


def test_empty_is_honest(tmp_path):
    r = rollup(vault_entries=[], loot_dir=str(tmp_path / "none"),
               netntlm_dir=str(tmp_path / "none"))
    assert r["total_items"] == 0 and "no loot gathered" in r["note"]


def test_counts_credentials_by_kind(tmp_path):
    r = rollup(vault_entries=[_Entry("wifi-psk"), _Entry("service-cred"), _Entry("service-cred")],
               loot_dir=str(tmp_path / "none"), netntlm_dir=str(tmp_path / "none"))
    assert r["credentials"]["total"] == 3 and r["credentials"]["service"] == 2
    assert r["credentials"]["by_kind"]["service-cred"] == 2


def test_counts_files_and_scans_secrets(tmp_path):
    d = tmp_path / "files"
    d.mkdir()
    (d / "app.env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\nx=1\n")
    (d / "notes.txt").write_text("nothing interesting here\n")
    r = rollup(vault_entries=[], loot_dir=str(d), netntlm_dir=str(tmp_path / "none"))
    assert r["files"]["count"] == 2
    assert r["secrets"]["total"] >= 1                      # the AWS key is found
    assert any("AKIA" in s["redacted"] for s in r["top_secrets"])


def test_counts_netntlm_hash_lines(tmp_path):
    nd = tmp_path / "netntlm"
    nd.mkdir()
    (nd / "netntlm-5600.txt").write_text("user::DOM:1122:deadbeef:0101\n\nuser2::DOM:3344:cafe:0101\n")
    r = rollup(vault_entries=[], loot_dir=str(tmp_path / "none"), netntlm_dir=str(nd))
    assert r["netntlm_hashes"] == 2                        # blank line ignored


def test_total_and_note_aggregate(tmp_path):
    d = tmp_path / "files"
    d.mkdir()
    (d / "c.env").write_text("password: hunter2hunter\n")
    r = rollup(vault_entries=[_Entry("service-cred")], loot_dir=str(d),
               netntlm_dir=str(tmp_path / "none"))
    assert r["total_items"] >= 2
    assert "credential(s)" in r["note"] and "file(s)" in r["note"]


def test_scan_secrets_can_be_disabled(tmp_path):
    d = tmp_path / "files"
    d.mkdir()
    (d / "c.env").write_text("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
    r = rollup(vault_entries=[], loot_dir=str(d), netntlm_dir=str(tmp_path / "n"),
               scan_secrets=False)
    assert r["files"]["count"] == 1 and r["secrets"]["total"] == 0
