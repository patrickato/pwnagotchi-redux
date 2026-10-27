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


def test_auto_until_overridden_then_modified():
    d = Deck(arsenal.groups(), _state())
    cr = next(z for z in d.view().zones if z.kind == "category" and z.value == "CAPTURE & CRACK")
    d.tap(cr.x0 + 2, cr.y0 + 2)
    row = next(z for z in d.view().zones if z.value == "crack run")
    d.tap(row.x0 + 2, row.y0 + 2)
    assert d.screen == "action" and not d.is_modified()      # pure auto out of the gate
    assert d.command() == "redux crack run"                  # auto: engine decides, no flags forced
    v = d.view()
    assert any(s.text == "⟳ AUTO" for s in v.spans)
    run = next(z for z in v.zones if z.kind == "run")
    assert run.label == "RUN (auto)"


def test_override_a_field_shows_modified_and_emits_it():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["crack run"]
    d.screen = "action"
    # pick an attack mode (value=label candidate) → command uses the VALUE, UI the label
    ap = next(z for z in d.view().zones if z.kind == "param" and z.value == "attack")
    d.tap(ap.x0 + 2, ap.y0 + 2)
    opt = next(z for z in d.view().zones if z.kind == "pick" and z.value.startswith("3="))
    d.tap(opt.x0 + 2, opt.y0 + 2)
    assert d.is_modified()
    assert "--attack 3" in d.command()
    v = d.view()
    assert any(s.text == "✎ MODIFIED" for s in v.spans)
    assert any(z.kind == "reset" for z in v.zones)           # reset-to-auto appears


def test_reset_returns_to_auto():
    d = Deck(arsenal.groups(), _state())
    d.action = DA.ACTIONS["crack run"]
    d.screen = "action"
    d.chosen = {"attack": "3=3 · mask"}
    rz = next(z for z in d.view().zones if z.kind == "reset")
    d.tap(rz.x0 + 2, rz.y0 + 2)
    assert not d.is_modified() and d.command() == "redux crack run"


def test_multi_select_captures_toggle_and_emit():
    d = Deck(arsenal.groups(), DeckState(captures=("cap-A.pcapng", "cap-B.pcapng", "cap-C.pcapng")))
    d.action = DA.ACTIONS["crack run"]
    d.screen = "action"
    cap = next(z for z in d.view().zones if z.kind == "param" and z.value == "captures")
    d.tap(cap.x0 + 2, cap.y0 + 2)
    assert d.screen == "picker"
    # toggle two files on
    for name in ("cap-A.pcapng", "cap-C.pcapng"):
        z = next(zz for zz in d.view().zones if zz.kind == "pick" and zz.value == name)
        d.tap(z.x0 + 2, z.y0 + 2)
        assert d.screen == "picker"                          # multi-select stays put
    assert set(d.chosen["captures"]) == {"cap-A.pcapng", "cap-C.pcapng"}
    done = next(z for z in d.view().zones if z.kind == "pickdone")
    d.tap(done.x0 + 2, done.y0 + 2)
    assert d.screen == "action"
    cmd = d.command()
    assert "--capture cap-A.pcapng" in cmd and "--capture cap-C.pcapng" in cmd


def test_toggle_layout_switches_in_one_tap():
    d = Deck(arsenal.groups(), DeckState(mode_lab=False))
    d.action = DA.ACTIONS["mode"]
    d.screen = "action"
    v = d.view()
    assert v.screen == "action"
    toggles = [z for z in v.zones if z.kind == "toggle"]
    assert len(toggles) == 2 and all((z.y1 - z.y0) >= 80 for z in toggles)   # big switches
    lab = next(z for z in toggles if z.value.startswith("lab"))
    assert d.tap(lab.x0 + 2, lab.y0 + 2) == ("run", "redux mode lab")


def test_targets_layout_multiselect_and_arm():
    from redux.display.deck_actions import Target
    st = DeckState(mode_lab=True, targets=(
        Target("NETGEAR-5G", signal=80, security="WPA2", crackable=True, value="high"),
        Target("xfinitywifi", signal=40, security="OPEN", crackable=False, value="low"),
        Target("HomeLab", signal=95, security="WPA2", crackable=True, value="high"),
    ))
    d = Deck(arsenal.groups(), st)
    d.action = DA.ACTIONS["campaign unleashed"]
    d.screen = "action"
    v = d.view()
    trows = [z for z in v.zones if z.kind == "target"]
    assert len(trows) == 3 and trows[0].sig == 80          # signal carried to the row
    # arm is disabled until something is ticked
    run = next(z for z in v.zones if z.kind == "run")
    assert not run.value
    for name in ("NETGEAR-5G", "HomeLab"):
        z = next(zz for zz in d.view().zones if zz.kind == "target" and zz.value == name)
        d.tap(z.x0 + 2, z.y0 + 2)
    assert set(d.chosen["target"]) == {"NETGEAR-5G", "HomeLab"}
    # now ARM & RUN is live and (power-tier) routes through confirm
    run = next(z for z in d.view().zones if z.kind == "run")
    assert run.value == "campaign unleashed"
    assert d.tap(run.x0 + 2, run.y0 + 2) is None and d.screen == "confirm"


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
