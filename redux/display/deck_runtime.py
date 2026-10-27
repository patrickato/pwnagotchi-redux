"""Runtime glue between the control-deck model and the live device: a real runner
that executes the deck's assembled `redux` commands and streams their output, and
a thin controller the fbui loop drives (render the current view, feed a tap).

The model (deck_model) stays pure and headless-testable; this is where it meets
subprocess + the framebuffer. The runner refuses anything that isn't a `redux`
command — the deck can only ever launch the project's own CLI, never arbitrary
shell.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
import threading
from typing import Callable, Optional

from .deck_model import Deck
from . import deck_render


def make_subprocess_runner(*, python: Optional[str] = None, timeout: int = 1800,
                           cwd: Optional[str] = None) -> Callable:
    """Build a runner(cmd, on_line, on_done) that runs a `redux …` command in a
    background thread and streams combined stdout/stderr line-by-line. Returns the
    thread so callers (tests) can join; the fbui just lets it run and redraws."""
    py = python or sys.executable

    def runner(cmd: str, on_line: Callable, on_done: Callable):
        try:
            parts = shlex.split(cmd)
        except ValueError:
            on_line("refused: unparseable command")
            on_done()
            return None
        if not parts or parts[0] != "redux":
            on_line("refused: the deck only runs redux commands")
            on_done()
            return None
        argv = [py, "-m", "redux.cli", *parts[1:]]

        def worker():
            try:
                proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        bufsize=1, cwd=cwd)
                assert proc.stdout is not None
                for line in proc.stdout:
                    on_line(line.rstrip("\n"))
                proc.wait(timeout=timeout)
                on_line(f"[exit {proc.returncode}]")
            except Exception as exc:
                on_line(f"error: {type(exc).__name__}: {exc}")
            finally:
                on_done()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        return t

    return runner


class DeckController:
    """What the fbui holds when the control deck is active: render the current view
    to a frame, route a (calibrated, display-space) tap into the deck."""

    def __init__(self, deck: Deck, runner: Optional[Callable] = None):
        self.deck = deck
        if runner is not None:
            self.deck.runner = runner

    def frame(self):
        """The 480×320 PIL image for the current deck view (same pixels the fb gets)."""
        return deck_render.render(self.deck.view())

    def on_tap(self, x: int, y: int):
        """Feed a tap; returns the deck's intent (or None). Execution is handled
        inside the deck (start_run), so the fbui just needs to redraw after."""
        return self.deck.tap(int(x), int(y))

    @property
    def active(self) -> bool:
        return self.deck.screen != "menu" or True      # deck is always interactive
