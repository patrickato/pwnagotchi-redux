"""Crack pre-flight — crackability assessment (engine + raw-pcap path + CLI)."""
from types import SimpleNamespace

from redux.crack.preflight import (
    assess_22000_text, assess_capture, Preflight, FrameAssessment,
)


def _pmkid(ssid, ap="001122334455"):
    return f"WPA*01*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}***"


def _hs(ssid, ap="001122334455", mp="02"):
    return f"WPA*02*{'a' * 32}*{ap}*{'bb' * 6}*{ssid.encode().hex()}*{'c' * 64}*{'dd' * 8}*{mp}"


# --- pure decode ------------------------------------------------------------ #

def test_pmkid_is_crackable_and_strong():
    pf = assess_22000_text(_pmkid("LabAP"))
    assert pf.crackable and pf.available and pf.pmkid == 1 and pf.handshakes == 0
    assert pf.frames[0].kind == "pmkid" and pf.frames[0].strong


def test_authenticated_handshake_is_strong_but_challenge_only_is_not():
    strong = assess_22000_text(_hs("Net", mp="02"))    # M2+M3 authenticated
    weak = assess_22000_text(_hs("Net", mp="00"))      # M1+M2 challenge only
    assert strong.frames[0].strong and "authenticated" in strong.frames[0].detail
    assert not weak.frames[0].strong and "M1+M2" in weak.frames[0].detail
    assert strong.crackable and weak.crackable          # both crackable, different confidence


def test_ap_less_bit_is_surfaced():
    pf = assess_22000_text(_hs("Net", mp="12"))        # 0x12: M2+M3 + AP-LESS bit
    assert "AP-LESS" in pf.frames[0].detail and "M2+M3" in pf.frames[0].detail


def test_unique_ap_count_and_mixed_material():
    text = "\n".join([_pmkid("A", "aaaaaaaaaaaa"),
                      _hs("A", "aaaaaaaaaaaa", "03"),     # same AP, second artifact
                      _hs("B", "bbbbbbbbbbbb", "00")])
    pf = assess_22000_text(text)
    assert pf.pmkid == 1 and pf.handshakes == 2 and pf.unique_aps == 2


def test_no_material_is_honest_not_crackable():
    pf = assess_22000_text("# just a comment\n")
    assert pf.available and not pf.crackable and "no crackable material" in pf.reason


def test_malformed_wpa_lines_are_counted():
    pf = assess_22000_text("WPA*02*nothex*xx*yy*zz\n" + _pmkid("Good"))
    assert pf.invalid_lines == 1 and pf.crackable      # the good PMKID still counts


# --- file dispatch / raw pcap ---------------------------------------------- #

def test_assess_hc22000_file(tmp_path):
    p = tmp_path / "cap.hc22000"
    p.write_text(_pmkid("FromFile") + "\n")
    pf = assess_capture(str(p))
    assert pf.crackable and pf.source == str(p)


def test_assess_raw_pcap_converts_then_decodes(tmp_path):
    def fake_hcx(cmd, **kw):
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w") as fh:
            fh.write(_hs("Converted", mp="02") + "\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    pf = assess_capture(str(tmp_path / "x.pcapng"), runner=fake_hcx)
    assert pf.available and pf.crackable and pf.handshakes == 1


def test_assess_raw_pcap_tool_absent_is_honest(tmp_path):
    def missing(cmd, **kw):
        raise FileNotFoundError("hcxpcapngtool")
    pf = assess_capture(str(tmp_path / "x.pcap"), runner=missing)
    assert not pf.available and not pf.crackable and "not installed" in pf.reason


def test_unsupported_file_type_is_rejected(tmp_path):
    pf = assess_capture(str(tmp_path / "notes.txt"))
    assert not pf.available and "unsupported" in pf.reason


# --- CLI -------------------------------------------------------------------- #

def test_cmd_preflight_human_and_json(tmp_path, capsys):
    from redux.cli import cmd_preflight
    import json as _json
    p = tmp_path / "cap.hc22000"
    p.write_text(_pmkid("LabAP") + "\n" + _hs("LabAP", mp="02") + "\n")
    assert cmd_preflight(SimpleNamespace(file=str(p), json=False)) == 0
    out = capsys.readouterr().out
    assert "crackable" in out and "pmkid" in out
    assert cmd_preflight(SimpleNamespace(file=str(p), json=True)) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["crackable"] and payload["pmkid"] == 1 and len(payload["frames"]) == 2


def test_cmd_preflight_toolless_returns_nonzero(tmp_path, capsys, monkeypatch):
    from redux.cli import cmd_preflight
    from redux.crack import preflight as P
    monkeypatch.setattr(P, "assess_capture",
                        lambda path: Preflight(source=path, available=False,
                                               crackable=False, reason="hcxpcapngtool not installed"))
    assert cmd_preflight(SimpleNamespace(file="x.pcap", json=False)) == 2
    assert "N/A" in capsys.readouterr().out
