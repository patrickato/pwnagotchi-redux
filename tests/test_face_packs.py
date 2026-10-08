"""Selectable face-packs — swap the look, keep the honest state machine."""
from redux.face import AugurState, eyes, eyes_for, list_packs, PACKS, DEFAULT_PACK
from redux.face.face import AugurFace


def test_three_packs_available_including_default():
    packs = list_packs()
    assert {"augur", "owl", "fox"} <= set(packs)
    assert DEFAULT_PACK == "augur"


def test_every_pack_covers_every_state_both_glyph_sets():
    for pack in list_packs():
        for st in AugurState:
            assert eyes_for(st, pack=pack) and eyes_for(st, pack=pack, ascii=True)


def test_ascii_variant_of_every_pack_is_pure_ascii():
    for pack in list_packs():
        for st in AugurState:
            assert eyes_for(st, pack=pack, ascii=True).isascii()


def test_default_eyes_match_augur_pack():
    # the module-level eyes() defaults to the augur pack (keeps old behavior)
    for st in AugurState:
        assert eyes(st) == eyes_for(st, pack="augur")
        assert eyes(st, ascii=True) == eyes_for(st, pack="augur", ascii=True)


def test_packs_actually_differ():
    assert eyes_for(AugurState.HUNT, "augur") != eyes_for(AugurState.HUNT, "fox")
    assert eyes_for(AugurState.WATCH, "owl") != eyes_for(AugurState.WATCH, "augur")


def test_unknown_pack_falls_back_to_default_not_blank():
    for st in AugurState:
        assert eyes_for(st, pack="does-not-exist") == eyes_for(st, pack="augur")


def test_augur_face_eyes_respects_pack():
    f = AugurFace(AugurState.HUNT, "x")
    assert f.eyes(pack="fox") == eyes_for(AugurState.HUNT, "fox")
    assert f.eyes() == eyes_for(AugurState.HUNT, "augur")      # default unchanged


def test_tft_renders_with_a_pack():
    from redux.tft import render
    sample = {"mood": "hunting", "capture_engine": "angryoxide", "intent": "recon"}
    fox = "\n".join(render(sample, pack="fox", width=46))
    augur = "\n".join(render(sample, pack="augur", width=46))
    assert "^>_<^" in fox and "^>_<^" not in augur               # the fox HUNT eyes show
    assert all(len(l) == 46 for l in render(sample, pack="fox", width=46))
