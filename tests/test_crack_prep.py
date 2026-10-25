"""Crack-prep — preflight+triage+forge composed into one work order."""
from types import SimpleNamespace

from redux.crack.prep import crack_prep, PrepItem
from redux.crack.triage import triage_crack_queue


class _FakeForge:
    """Stand-in for forge.forge: returns a fixed, inspectable word list."""
    def __init__(self, ssid, tokens=()):
        self.words = tuple(f"{ssid}{n}" for n in range(3)) + tuple(tokens)


def _pmkid(ssid, ap):
    return f"WPA*01*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}***"


def _hs(ssid, ap, mp="02"):
    return f"WPA*02*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}*{'c' * 64}*{'dd' * 8}*{mp}"


def test_prep_ranks_and_forges_per_target():
    triaged = triage_crack_queue([
        ("NetgearXX", "00:00:00:00:00:01", "pmkid"),    # vendor -> high
        ("mycustomnet", "00:00:00:00:00:02", "handshake"),  # custom -> low
    ])
    items = crack_prep(triaged, strong_bssids={"00:00:00:00:00:01"}, forge_fn=_FakeForge)
    assert [it.bssid[-1] for it in items] == ["1", "2"]     # ROI order preserved
    assert items[0].strong is True and items[1].strong is False
    assert items[0].forged_words and items[0].forged_words[0].startswith("NetgearXX")


def test_limit_caps_the_work_order():
    triaged = triage_crack_queue([(f"n{i}", f"00:00:00:00:00:0{i}", "pmkid") for i in range(1, 5)])
    assert len(crack_prep(triaged, set(), forge_fn=_FakeForge, limit=2)) == 2


def test_hidden_ssid_forges_nothing_but_still_listed():
    triaged = triage_crack_queue([("", "00:00:00:00:00:09", "pmkid")])
    items = crack_prep(triaged, set(), forge_fn=_FakeForge)
    assert len(items) == 1 and items[0].forged_words == ()


# --- CLI -------------------------------------------------------------------- #

def test_cmd_crackprep_human_json_and_wordlist_files(tmp_path, capsys):
    from redux.cli import cmd_crackprep
    import json as _json
    ready = tmp_path / "ready"; ready.mkdir()
    (ready / "a.hc22000").write_text(_pmkid("NetgearXX", "aabbccddeeff") + "\n")       # strong (pmkid)
    (ready / "b.hc22000").write_text(_hs("mycustomnet", "001122334455", "00") + "\n")  # weak (M1+M2)
    out_dir = tmp_path / "wl"
    base = dict(dir=str(ready), file=None, top=0, token=None, out_dir=str(out_dir))
    assert cmd_crackprep(SimpleNamespace(json=False, **base)) == 0
    out = capsys.readouterr().out
    assert "CRACK-PREP" in out and "NetgearXX" in out and "strong" in out and "weak" in out
    # wordlist files written per target
    assert (out_dir / "aabbccddeeff.txt").exists()
    # JSON form
    assert cmd_crackprep(SimpleNamespace(json=True, **base)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload[0]["ssid"] == "NetgearXX" and payload[0]["strong"] is True
    assert payload[0]["forged"] > 0 and payload[0]["wordlist_file"]


def test_cmd_crackprep_empty_is_honest(tmp_path, capsys):
    from redux.cli import cmd_crackprep
    assert cmd_crackprep(SimpleNamespace(dir=str(tmp_path / "none"), file=None, top=0,
                                         token=None, out_dir=None, json=False)) == 0
    assert "no crackable captures found" in capsys.readouterr().out
