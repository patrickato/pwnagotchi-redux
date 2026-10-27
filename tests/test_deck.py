"""Control-deck interaction model — drill-down nav, pick-lists, power-tier confirm.
Pure logic, no framebuffer, no hardware."""
from redux.core import arsenal
from redux.display import deck_actions as DA
from redux.display.deck_actions import DeckState, candidates_for
from redux.display.deck_model import Deck, hit


def _state():
    return DeckState(
        scope_targets=("aa:bb:cc:dd:ee:ff", "HomeLab"),
        subnets=("10.0.0.0/24",),
        wordlists=("starter", "rockyou"),
        device_categories=("router", "camera", "doorbell"),
        hosts=("10.0.0.5",),
        mode_lab=True,
    )


# --- catalog integrity ------------------------------------------------------- #
def test_every_catalog_action_is_a_real_arsenal_command():
    real = {e.command for e in arsenal.all_entries()}
    for a in DA.ACTIONS.values():
        assert a.id in real, f"deck action {a.id!r} is not an arsenal command"


def test_every_param_source_resolves():
    st = _state()
    for a in DA.ACTIONS.values():
        for p in a.params:
            if p.source.startswith("static:"):
                assert candidates_for(p.source, st)          # non-empty inline list
            elif p.source == "free_text":
                assert p.is_free_text
            else:
                assert hasattr(st, p.source), f"{a.id}:{p.name} bad source {p.source}"


def test_static_and_live_candidates():
    st = _state()
    assert candidates_for("static:lab|field|status", st) == ["lab", "field", "status"]
    assert candidates_for("wordlists", st) == ["starter", "rockyou"]
    assert candidates_for("subnets", DeckState()) == []        # honest empty on fresh box


# --- navigation -------------------------------------------------------------- #
def test_menu_lists_every_arsenal_group_as_a_tile():
    d = Deck(arsenal.groups(), _state())
    v = d.view()
    tiles = [z for z in v.zones if z.kind == "category"]
    assert len(tiles) == min(8, len(arsenal.groups()))
    # tiles are big finger targets
    for z in tiles:
        assert (z.x1 - z.x0) >= 200 and (z.y1 - z.y0) >= 50


def test_drill_into_category_then_action():
    d = Deck(arsenal.groups(), _state())
    cap = next(z for z in d.view().zones if z.kind == "category"
               and z.value == "CAPTURE & CRACK")
    d.tap(cap.x0 + 5, cap.y0 + 5)
    assert d.screen == "category" and d.category == "CAPTURE & CRACK"
    row = next(z for z in d.view().zones if z.kind == "action" and z.value == "crack run")
    d.tap(row.x0 + 5, row.y0 + 5)
    assert d.screen == "action" and d.action.id == "crack run"


# --- pick-lists fill the blanks (no typing) ---------------------------------- #
def test_param_pick_fills_the_command():
    d = Deck(arsenal.groups(), _state())
    d.category = "CAPTURE & CRACK"
    d.screen = "category"
    row = next(z for z in d.view().zones if z.value == "crack run")
    d.tap(row.x0 + 2, row.y0 + 2)                     # → action screen
    pz = next(z for z in d.view().zones if z.kind == "param" and z.value == "wordlist")
    d.tap(pz.x0 + 2, pz.y0 + 2)                       # → picker
    assert d.screen == "picker"
    opt = next(z for z in d.view().zones if z.kind == "pick" and z.value == "rockyou")
    d.tap(opt.x0 + 2, opt.y0 + 2)                     # pick it
    assert d.screen == "action" and d.chosen["wordlist"] == "rockyou"
    assert d.command() == "redux crack run --wordlist rockyou"


def test_positional_vs_flag_assembly():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["identify"]
    d.chosen = {"target": "HomeLab"}
    assert d.command() == "redux identify HomeLab"         # positional
    d.action = DA.ACTIONS["loot pivot"]
    d.chosen = {"subnet": "10.0.0.0/24", "category": "camera"}
    assert d.command() == "redux loot pivot --subnet 10.0.0.0/24 --device-category camera"


# --- power-tier always confirms before running ------------------------------- #
def test_power_tier_routes_through_confirm():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["loot pivot"]
    d.screen = "action"
    d.chosen = {"subnet": "10.0.0.0/24"}
    run = next(z for z in d.view().zones if z.kind == "run")
    assert d.tap(run.x0 + 2, run.y0 + 2) is None          # no run yet
    assert d.screen == "confirm"
    conf = next(z for z in d.view().zones if z.kind == "confirm")
    intent = d.tap(conf.x0 + 2, conf.y0 + 2)
    assert intent == ("confirm", "redux loot pivot --subnet 10.0.0.0/24")


def test_safe_action_runs_without_confirm():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["survey"]
    d.screen = "action"
    run = next(z for z in d.view().zones if z.kind == "run")
    assert d.tap(run.x0 + 2, run.y0 + 2) == ("run", "redux survey")


def test_cancel_from_confirm_returns_to_action():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["wps"]
    d.screen = "confirm"
    cancel = next(z for z in d.view().zones if z.kind == "cancel")
    d.tap(cancel.x0 + 2, cancel.y0 + 2)
    assert d.screen == "action"


def test_lab_only_adds_lab_flag_when_not_in_lab():
    d = Deck(arsenal.groups(), DeckState(scope_targets=("HomeLab",), mode_lab=False))
    d.action = DA.ACTIONS["campaign unleashed"]
    assert d.command().endswith("--lab")


def test_back_and_home_navigation():
    d = Deck(arsenal.groups(), _state())
    d.category, d.screen = "LOOT & PIVOT", "category"
    d.action = DA.ACTIONS["loot pivot"]
    d.screen = "action"
    back = next(z for z in d.view().zones if z.kind == "back")
    d.tap(back.x0 + 2, back.y0 + 2)
    assert d.screen == "category"
    home = next(z for z in d.view().zones if z.kind == "home")
    d.tap(home.x0 + 2, home.y0 + 2)
    assert d.screen == "menu"
