"""Target-aware wordlist/mask forge (engine + CLI)."""
from types import SimpleNamespace

from redux.crack import forge as F
from redux.crack.forge import forge, mutate_word, Forge, Mask


def test_mutate_word_makes_case_leet_and_suffix_variants():
    out = mutate_word("home")
    assert "home" in out and "Home" in out and "HOME" in out
    assert "h0m3" in out                      # leet
    assert "home2024" in out and "Home123" in out


def test_forge_filters_to_wpa_lengths_and_uses_seeds():
    f = forge("Linksys", tokens=["patrick"], min_len=8)
    assert all(8 <= len(w) <= 63 for w in f.words)
    assert "patrick123" in f.words            # OSINT seed mutated
    assert any(w.lower().startswith("linksys") for w in f.words)  # SSID seed
    assert f.seeds[0] == "Linksys" and "patrick" in f.seeds


def test_short_words_are_dropped():
    f = forge("hi", min_len=8)                # "hi" and its short variants gone
    assert all(len(w) >= 8 for w in f.words)
    assert "hi" not in f.words


def test_pnl_names_become_seeds():
    f = forge("", pnl=["CoffeeShop"], min_len=8)
    assert any("coffeeshop" in w.lower() for w in f.words)


def test_vendor_from_bssid_added_as_seed(monkeypatch):
    monkeypatch.setattr(F, "lookup_oui", lambda mac: "AcmeCorp")
    f = forge("Net", bssid="00:11:22:33:44:55")
    assert f.vendor == "AcmeCorp" and "AcmeCorp" in f.seeds
    assert any(w.startswith("AcmeCorp") or w.startswith("acmecorp") for w in f.words)


def test_masks_present_and_labelled():
    f = forge("Net")
    assert f.masks and all(m.mask.startswith("?") and m.note for m in f.masks)
    assert any(m.mask == "?d?d?d?d?d?d?d?d" for m in f.masks)


def test_max_words_cap_is_respected():
    f = forge("Net", tokens=[f"tok{i}" for i in range(50)], max_words=25)
    assert len(f.words) == 25


def test_deterministic():
    assert forge("Target", tokens=["a", "b"]).words == forge("Target", tokens=["a", "b"]).words


# --- CLI -------------------------------------------------------------------- #

def _ns(**kw):
    base = dict(ssid="", token=None, pnl=None, bssid=None, out=None,
                min_len=8, max=2000, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_cmd_forge_human_and_json(capsys):
    from redux.cli import cmd_forge
    import json as _json
    assert cmd_forge(_ns(ssid="NetgearHome", token=["patrick"], json=False)) == 0
    out = capsys.readouterr().out
    assert "FORGE" in out and "masks" in out
    assert cmd_forge(_ns(ssid="NetgearHome", token=["patrick"], json=True)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["words"] and payload["masks"] and "patrick123" in payload["words"]


def test_cmd_forge_out_writes_file(tmp_path, capsys):
    from redux.cli import cmd_forge
    out = tmp_path / "wl.txt"
    assert cmd_forge(_ns(ssid="Target", token=["patrick"], out=str(out))) == 0
    lines = out.read_text().splitlines()
    assert lines and all(8 <= len(w) <= 63 for w in lines)
    assert "wrote" in capsys.readouterr().out


def test_cmd_forge_reads_pnl(tmp_path, capsys):
    from redux.cli import cmd_forge
    from redux.portal.karma import ProbeHarvester
    pnl = tmp_path / "pnl.json"
    h = ProbeHarvester(path=str(pnl))
    h.note("fa:11:22:33:44:01", "BackyardNet")
    assert cmd_forge(_ns(ssid="", pnl=str(pnl), json=True)) == 0
    import json as _json
    payload = _json.loads(capsys.readouterr().out)
    assert any("backyardnet" in w.lower() for w in payload["words"])
