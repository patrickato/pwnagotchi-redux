"""Deck runtime glue — the real subprocess runner + the fbui-facing controller."""
import time

from redux.core import arsenal
from redux.display.deck_actions import DeckState, Action
from redux.display.deck_model import Deck
from redux.display.deck_runtime import make_subprocess_runner, DeckController


def _drain(flag, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if flag():
            return True
        time.sleep(0.03)
    return False


def test_runner_refuses_non_redux_commands():
    out = []
    done = []
    make_subprocess_runner()("rm -rf /", out.append, lambda: done.append(True))
    assert out == ["refused: the deck only runs redux commands"] and done


def test_runner_runs_a_real_redux_command_and_streams_exit():
    lines = []
    done = {"v": False}
    t = make_subprocess_runner()("redux capabilities completion",
                                 lines.append, lambda: done.__setitem__("v", True))
    if t is not None:
        t.join(timeout=20)
    assert _drain(lambda: done["v"])
    assert any(l.startswith("[exit ") for l in lines)          # process ran to completion
    assert any("capabilities" in l for l in lines)             # and produced real output


def test_controller_frame_and_tap_route_through_the_deck():
    ctl = DeckController(Deck(arsenal.groups(), DeckState()))
    assert ctl.frame().size == (480, 320)
    # tapping a category tile drills in through the controller
    cat = next(z for z in ctl.deck.view().zones if z.kind == "category")
    ctl.on_tap(cat.x0 + 4, cat.y0 + 4)
    assert ctl.deck.screen == "category"


def test_controller_run_uses_injected_runner():
    seen = {}
    ctl = DeckController(Deck(arsenal.groups(), DeckState()),
                         runner=lambda cmd, on_line, on_done: (seen.__setitem__("cmd", cmd), on_done()))
    ctl.deck.action = Action("survey", "Survey", "RECON & IDENTITY")
    ctl.deck.screen = "action"
    run = next(z for z in ctl.deck.view().zones if z.kind == "run")
    ctl.on_tap(run.x0 + 4, run.y0 + 4)
    assert seen["cmd"] == "redux survey" and ctl.deck.screen == "running"
