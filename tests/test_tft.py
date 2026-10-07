"""TFT screen renderer — lean, fixed-width, monochrome-safe faces."""
from redux.tft import Face, render, render_text
from redux.core import Beastcore
from redux.radio import Radio, Intent


SAMPLE = {
    "creature": "> hunting", "mood": "hunting", "intent": "recon",
    "persona": "purple", "posture": "active", "capture_iface": "wlan1",
    "capture_engine": "angryoxide", "sightings": 142, "recent_alerts": 3,
    "governor": {"mode": "guarded"},
    "sense": {"available": True, "sense": "motion", "occupancy": "occupied"},
    "sentinel": {"armed": True, "dispatched": 4, "suppressed": 2,
                 "last": {"summary": "ble_skimmer", "severity": "critical"}},
    "narration": ["ch6 dwell - 3 new APs, 1 PMKID elicited in a very long line indeed here"],
}


# --- layout ------------------------------------------------------------------ #

def test_status_frame_is_fixed_width_and_bordered():
    lines = render(SAMPLE, face=Face.STATUS, width=46)
    assert all(len(l) == 46 for l in lines)          # every row padded to width
    assert lines[0].startswith("┌") and lines[0].endswith("┐")
    assert lines[-1].startswith("└") and lines[-1].endswith("┘")


def test_status_frame_shows_key_fields_and_a_gauge():
    txt = render_text(SAMPLE, face=Face.STATUS)
    assert "intent recon" in txt and "angryoxide" in txt and "wlan1" in txt
    assert "GUARDED" in txt and "█" in txt            # governor gauge bar
    assert "purple/active" in txt


def test_plain_face_is_smaller_and_has_creature():
    plain = render(SAMPLE, face=Face.PLAIN)
    status = render(SAMPLE, face=Face.STATUS)
    assert len(plain) < len(status)
    assert any("hunting" in l for l in plain)


# --- monochrome-safe + ascii fallback ---------------------------------------- #

def test_frame_has_no_ansi_colour_codes():
    assert "\x1b" not in render_text(SAMPLE)          # relies on glyphs, never colour


def test_ascii_fallback_is_pure_ascii():
    lines = render(SAMPLE, face=Face.STATUS, ascii=True)
    assert all(l.isascii() for l in lines)
    assert all(len(l) == 46 for l in lines)
    assert "#" in "\n".join(lines)                    # ascii gauge
    assert "~" in "\n".join(lines)                    # ascii ellipsis on the clipped line


# --- robustness -------------------------------------------------------------- #

def test_missing_optional_sections_do_not_crash():
    minimal = {"intent": "recon"}                     # no creature/sense/sentinel/governor
    lines = render(minimal, face=Face.STATUS)
    assert all(len(l) == 46 for l in lines)
    txt = "\n".join(lines)
    assert "csi" not in txt and "sentinel" not in txt  # absent sections are omitted


def test_long_narration_is_clipped():
    lines = render(SAMPLE, face=Face.STATUS)
    assert any("…" in l for l in lines)               # the long line got an ellipsis


# --- Beastcore hook ---------------------------------------------------------- #

def test_beastcore_tft_frame():
    r = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)
    bc = Beastcore(radios=[r], intent=Intent.RECON)
    frame = bc.tft_frame(face="status")
    assert isinstance(frame, list) and frame and all(len(l) == len(frame[0]) for l in frame)
