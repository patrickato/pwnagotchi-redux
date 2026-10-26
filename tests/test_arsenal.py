"""The arsenal — one-place operate index; every verb must be a real command."""
from redux.core import arsenal
from redux.cli import build_parser


def _real_commands():
    p = build_parser()
    for action in p._subparsers._group_actions:
        if hasattr(action, "choices") and action.choices:
            return set(action.choices.keys())
    return set()


def test_every_arsenal_verb_is_a_real_redux_command():
    real = _real_commands()
    assert real, "could not introspect CLI commands"
    for entry in arsenal.all_entries():
        assert entry.verb in real, f"arsenal lists unknown command: {entry.command}"


def test_covers_the_round6_wave():
    cmds = {e.command for e in arsenal.all_entries()}
    # the new capabilities must be reachable from the one-place index
    for needed in ("pmkid sweep", "responder harvest", "loot files", "dex --dna",
                   "airwave timeline", "stealth pace", "scrub status",
                   "mesh correlate", "engagement open", "scenario list", "phone plan",
                   "capabilities qualified", "forge --environment"):
        assert needed in cmds, f"arsenal missing {needed}"


def test_groups_partition_all_entries():
    g = arsenal.groups()
    assert sum(len(v) for v in g.values()) == len(arsenal.all_entries())
    assert len(g) >= 7


def test_search_matches_command_title_and_group():
    assert any(e.command == "pmkid sweep" for e in arsenal.search("pmkid"))
    assert any("NetNTLM" in e.title for e in arsenal.search("netntlm"))
    assert arsenal.search("stealth") and all("stealth" in (e.command + e.title + e.group).lower()
                                             for e in arsenal.search("stealth"))
    assert len(arsenal.search("")) == len(arsenal.all_entries())


def test_menu_groups_are_tft_ready():
    rows = arsenal.menu_groups()
    assert rows and all(len(r) == 3 for r in rows)      # (name, line1, line2)
    names = {r[0] for r in rows}
    assert "LOOT & PIVOT" in names and "STEALTH & OPSEC" in names
