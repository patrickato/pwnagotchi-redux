"""Augur's face — deterministic, honest, derived only from real state."""
from redux.face import AugurState, AugurFace, face_for, eyes
from redux.core import Augur
from redux.radio import Radio, Intent


ONBOARD = Radio("wlan0", bands=frozenset({"2.4"}), monitor=True, inject=False, driver="brcmfmac", onboard=True)


# --- the state mapping ------------------------------------------------------- #

def test_blind_when_no_engine_and_no_sense():
    # the honesty state: it can't perceive, so it says so — never a busy face over nothing
    f = face_for({"intent": "recon"})
    assert f.state is AugurState.BLIND and "blind" in f.reason.lower()


def test_sense_only_is_not_blind():
    # CSI available but no capture engine → it CAN perceive, so not blind
    f = face_for({"capture_engine": "none", "sense": {"available": True}})
    assert f.state is not AugurState.BLIND


def test_moods_map_to_faces():
    eng = {"capture_engine": "bettercap"}
    assert face_for({**eng, "mood": "hunting"}).state is AugurState.HUNT
    assert face_for({**eng, "mood": "thinking"}).state is AugurState.READ
    assert face_for({**eng, "mood": "alert"}).state is AugurState.RUFFLE
    assert face_for({**eng, "mood": "idle"}).state is AugurState.WATCH


def test_critical_alert_wins_and_carries_its_reason():
    f = face_for({"capture_engine": "bettercap", "mood": "hunting",
                  "sentinel": {"last": {"severity": "critical", "summary": "ble_skimmer"}}})
    assert f.state is AugurState.RUFFLE and f.reason == "ble_skimmer"


def test_transient_hints():
    eng = {"capture_engine": "bettercap"}
    assert face_for({**eng, "face_hint": "cache"}).state is AugurState.CACHE
    assert face_for({**eng, "face_hint": "mark"}).state is AugurState.MARK


def test_roost_when_mesh_peers_present():
    f = face_for({"capture_engine": "bettercap", "mesh": {"peers": ["kit-2"]}})
    assert f.state is AugurState.ROOST


# --- glyphs ------------------------------------------------------------------ #

def test_every_state_has_both_glyph_sets():
    for st in AugurState:
        assert eyes(st) and eyes(st, ascii=True)


def test_ascii_eyes_are_pure_ascii_and_nice_arent():
    for st in AugurState:
        assert eyes(st, ascii=True).isascii()           # mono-safe fallback
    assert not eyes(AugurState.WATCH).isascii()          # the nice set uses the ‹ › frame


def test_face_object_eyes_match_module_eyes():
    f = AugurFace(AugurState.HUNT, "x")
    assert f.eyes() == eyes(AugurState.HUNT) and f.eyes(ascii=True) == eyes(AugurState.HUNT, ascii=True)


def test_face_is_deterministic():
    snap = {"capture_engine": "bettercap", "mood": "hunting"}
    assert face_for(snap).state is face_for(dict(snap)).state


# --- Augur.status() integration --------------------------------------------- #

def test_status_embeds_face_fields_consistently():
    bc = Augur(radios=[ONBOARD], intent=Intent.RECON)
    st = bc.status()
    assert st["face"] and st["face_ascii"] and st["face_state"] and st["face_reason"]
    # the embedded glyphs match the declared state (one source of truth)
    state = AugurState(st["face_state"])
    assert st["face"] == eyes(state) and st["face_ascii"] == eyes(state, ascii=True)
