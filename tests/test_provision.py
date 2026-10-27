"""First-boot provisioning — /captures skeleton + starter wordlist seed."""
from redux.core.provision import provision, ensure_captures_skeleton, seed_starter_wordlist, _starter_path


def test_starter_wordlist_ships_in_the_package():
    p = _starter_path()
    assert p.is_file(), "baked starter wordlist must ship with the package"
    lines = [l for l in p.read_text().splitlines() if l.strip()]
    assert len(lines) > 500                       # a real list, not a token
    assert all(8 <= len(l) <= 63 for l in lines)  # every candidate is WPA-valid length


def test_ensure_skeleton_creates_all_subdirs(tmp_path):
    made = ensure_captures_skeleton(str(tmp_path / "captures"))
    base = tmp_path / "captures"
    for d in ("wordlists", "loot/files", "loot/netntlm", "ready", "crack", "live", "engagements"):
        assert (base / d).is_dir(), d
    assert made                                   # reported what it created


def test_skeleton_is_idempotent(tmp_path):
    root = str(tmp_path / "captures")
    ensure_captures_skeleton(root)
    assert ensure_captures_skeleton(root) == []   # nothing new the second time


def test_seed_registers_and_selects_a_crackable_wordlist(tmp_path):
    root = str(tmp_path / "captures")
    ensure_captures_skeleton(root)
    seeded = seed_starter_wordlist(root)
    assert seeded and seeded.endswith("starter.txt")
    from redux.crack.wordlists import WordlistStore
    store = WordlistStore(str(tmp_path / "captures" / "wordlists"))
    assert store.list()                           # a wordlist is registered
    assert store.selected() is not None           # and selected → crack has one


def test_seed_does_not_clobber_existing_wordlists(tmp_path):
    root = str(tmp_path / "captures")
    wl = tmp_path / "captures" / "wordlists"
    wl.mkdir(parents=True)
    (wl / "mine.txt").write_text("password123\nhunter2hunter\n")
    from redux.crack.wordlists import WordlistStore
    WordlistStore(str(wl)).register(str(wl / "mine.txt"))
    assert seed_starter_wordlist(root) is None    # operator already has lists — untouched


def test_provision_is_idempotent_and_reports(tmp_path):
    root = str(tmp_path / "captures")
    r1 = provision(root)
    assert r1["wordlist_seeded"] and r1["dirs_created"]
    r2 = provision(root)
    assert r2["dirs_created"] == [] and r2["wordlist_seeded"] is None   # second run no-ops
