"""The control-deck interaction model — a pure state machine over the 480×320
TFT: drill down menu → category → action → pick-list → confirm → run, by taps.

It owns screen geometry (so taps can be hit-tested) but no pixels: every drawable
is a Zone (tappable rect) or a Span (static text) carrying a *style token*, and
deck_render maps those tokens to the real fonts/colors. Tap targets are large
(≥44px rows, ≥40px buttons) for a finger on resistive glass; text styles never go
below the 7px floor.

Design rules baked in here:
  • drill-down, not density — one decision per screen, few big rows.
  • pick-lists, not typing — a param's blank is filled from candidates
    (deck_actions.candidates_for); only a free_text param opens the keyboard.
  • glass-box — a power-tier action (transmit/fire/write) always routes through a
    confirm screen showing the exact command before it runs.

Headless and deterministic: construct a Deck with an arsenal view + a DeckState,
call tap(x, y), read view(). deck_render turns view() into the frame; the fbui
input loop feeds taps and executes the intents tap() returns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import deck_actions
from .deck_actions import (Action, DeckState, Param, candidates_for,
                           choice_value, choice_label)


# --- geometry (480×320) ------------------------------------------------------ #
W, H = 480, 320
HEADER_H = 30
FOOTER_Y = 286
CONTENT_TOP = 34
ROW_H = 44                   # list row height — a comfortable finger target
GRID_COLS = 2
GRID_ROWS = 4
TILE_W = (W - 24) // GRID_COLS      # 228
TILE_H = 58
RUN_H = 46
PARAM_ROW = 36               # param field height on the action screen (fits ~5)
LIST_ROWS = 5                # rows visible before the list scrolls


# --- drawables --------------------------------------------------------------- #
@dataclass(frozen=True)
class Zone:
    """A tappable rectangle. `kind` tells tap() what to do; `value` is its subject
    (category name, action id, param name, candidate value, or a nav keyword)."""
    x0: int
    y0: int
    x1: int
    y1: int
    kind: str                 # category|action|param|run|confirm|cancel|back|home|pick|scroll|toggle|target
    value: str
    label: str = ""
    style: str = "row"        # render token: row|tile|run|confirm|cancel|nav|pick|toggle*|target*
    sub: str = ""             # secondary line (e.g. current choice, note, badges)
    sig: int = -1             # signal 0..100 for a target row (-1 = none)

    def contains(self, x: int, y: int) -> bool:
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


@dataclass(frozen=True)
class Span:
    x: int
    y: int
    text: str
    style: str = "label"      # title|label|muted|value|accent|warn|tiny


@dataclass(frozen=True)
class View:
    screen: str
    title: str
    zones: Tuple[Zone, ...]
    spans: Tuple[Span, ...]
    scroll: bool = False      # list is longer than the window


def hit(zones, x: int, y: int) -> Optional[Zone]:
    for z in zones:
        if z.contains(x, y):
            return z
    return None


def _short_title(command: str) -> str:
    """A compact, readable row name for an arsenal command that has no catalog
    entry: drop flags, title-case the verb(s). 'capture plan' → 'Capture Plan'."""
    base = [w for w in command.split() if not w.startswith("--")]
    return " ".join(base).title() if base else command


# --- the deck ---------------------------------------------------------------- #
class Deck:
    """Touch-driven control surface over the arsenal. `groups` is an ordered
    mapping {group_name: [arsenal.Entry,…]} (arsenal.groups()); `state` supplies
    the live pick-list candidates."""

    def __init__(self, groups: "Dict[str, list]", state: Optional[DeckState] = None):
        self._groups = list(groups.items())
        self.state = state or DeckState()
        self.screen = "menu"
        self.category: Optional[str] = None
        self.action: Optional[Action] = None
        self.chosen: Dict[str, str] = {}
        self.picker_param: Optional[Param] = None
        self.offset = 0                     # scroll offset for the current list
        self.last_run: str = ""             # the command the last RUN/confirm emitted
        self.device: Optional[Dict[str, object]] = None   # identify → detail context
        self._action_from = "category"      # where the current action screen was entered from

    # ---- command assembly ---------------------------------------------------- #
    def command(self, action: Optional[Action] = None) -> str:
        a = action or self.action
        if a is None:
            return ""
        parts = ["redux", *a.id.split()]
        for p in a.visible_params(self.chosen):     # hidden-by-context fields never emit
            val = self.chosen.get(p.name)
            if not val:
                continue
            values = val if isinstance(val, list) else [val]
            for v in values:
                cv = choice_value(v)
                if p.flag:
                    parts += [p.flag, cv]
                else:
                    parts.append(cv)
        if a.lab_only and not self.state.mode_lab:
            parts.append("--lab")
        return " ".join(parts)

    def is_modified(self) -> bool:
        """True once the operator overrides any field away from its auto default —
        the deck's AUTO↔MODIFIED signal. Untouched = the pipeline's own choices."""
        a = self.action
        if a is None:
            return False
        for p in a.params:
            v = self.chosen.get(p.name)
            if v in (None, "", [], p.default):
                continue
            return True
        return False

    def reset_auto(self) -> None:
        """Drop all overrides — back to pure auto (defaults only)."""
        if self.action is not None:
            self.chosen = {p.name: p.default for p in self.action.params if p.default}

    def _field_display(self, p: Param) -> str:
        """What the action screen shows in a param's field: the chosen label(s), or
        the auto hint when the operator hasn't overridden it."""
        v = self.chosen.get(p.name)
        if p.multi:
            picks = v if isinstance(v, list) else ([v] if v else [])
            if picks:
                return f"{len(picks)} selected"
            return f"auto: {p.auto}" if p.auto else "pick…"
        if v:
            return choice_label(v)
        if p.auto:
            return f"auto: {p.auto}"
        return "(keyboard)" if p.is_free_text else "pick…"

    # ---- the action for an arsenal command (catalog, or a 0-param RUN) -------- #
    def _resolve(self, command: str, group: str, title: str) -> Action:
        a = deck_actions.action_for(command)
        return a or Action(id=command, title=title or command, group=group)

    # ---- view building ------------------------------------------------------- #
    def view(self) -> View:
        if self.screen == "menu":
            return self._view_menu()
        if self.screen == "category":
            return self._view_category()
        if self.screen == "action":
            lay = self.action.layout if self.action else "form"
            if lay == "toggle":
                return self._view_toggle()
            if lay == "targets":
                return self._view_targets()
            return self._view_action()
        if self.screen == "picker":
            return self._view_picker()
        if self.screen == "confirm":
            return self._view_confirm()
        if self.screen == "detail":
            return self._view_detail()
        return self._view_menu()

    def _header(self, title: str, *, back: bool) -> List[Zone]:
        z: List[Zone] = []
        if back:
            z.append(Zone(0, 0, 64, HEADER_H, "back", "", "‹ back", "nav"))
        z.append(Zone(W - 64, 0, W, HEADER_H, "home", "", "home", "nav"))
        return z

    def _view_menu(self) -> View:
        zones: List[Zone] = list(self._header("ARSENAL", back=False))
        spans = [Span(W // 2 - 40, 8, "ARSENAL", "title")]
        for i, (name, _entries) in enumerate(self._groups[:GRID_COLS * GRID_ROWS]):
            col, row = i % GRID_COLS, i // GRID_COLS
            x0 = 8 + col * (TILE_W + 8)
            y0 = CONTENT_TOP + row * (TILE_H + 4)
            zones.append(Zone(x0, y0, x0 + TILE_W, y0 + TILE_H, "category", name,
                              name, "tile"))
        return View("menu", "ARSENAL", tuple(zones), tuple(spans))

    def _entries_for(self, group: str) -> list:
        for name, entries in self._groups:
            if name == group:
                return list(entries)
        return []

    def _view_category(self) -> View:
        entries = self._entries_for(self.category or "")
        zones: List[Zone] = list(self._header(self.category or "", back=True))
        spans = [Span(70, 8, (self.category or "")[:26], "title")]
        window = entries[self.offset:self.offset + LIST_ROWS]
        for i, e in enumerate(window):
            y0 = CONTENT_TOP + i * ROW_H
            a = self._resolve(e.command, self.category or "", e.title)
            tier = "  ⚡" if a.power_tier else ""
            short = a.title if e.command in deck_actions.ACTIONS else _short_title(e.command)
            zones.append(Zone(8, y0, W - 8, y0 + ROW_H - 4, "action", e.command,
                              short + tier, "row", e.title))
        scroll = len(entries) > LIST_ROWS
        if scroll:
            zones += self._scrollers(len(entries))
        return View("category", self.category or "", tuple(zones), tuple(spans), scroll)

    def _scrollers(self, total: int) -> List[Zone]:
        z = []
        if self.offset > 0:
            z.append(Zone(W - 44, CONTENT_TOP, W, CONTENT_TOP + 40, "scroll", "up", "▲", "nav"))
        if self.offset + LIST_ROWS < total:
            z.append(Zone(W - 44, FOOTER_Y - 44, W, FOOTER_Y, "scroll", "down", "▼", "nav"))
        return z

    def show_device(self, device: Dict[str, object]) -> None:
        """Enter the identify→detail screen for a device the recon pipeline saw."""
        self.device = device
        self.screen, self.offset = "detail", 0

    def _view_detail(self) -> View:
        """What a device IS + a playbook of what you can do to it, each tappable to
        set up (chains recon → action: your A + B → C)."""
        dv = self.device or {}
        product = str(dv.get("product", "device"))
        zones: List[Zone] = list(self._header(product, back=True))
        spans = [Span(70, 8, f"{product}  ·  {dv.get('category', '?')}"[:26], "title"),
                 Span(10, HEADER_H + 3,
                      f"{dv.get('vendor', '')} · {dv.get('confidence', '')} confidence"[:58],
                      "muted"),
                 Span(10, HEADER_H + 18, "WHAT YOU CAN DO — tap to set up", "accent")]
        acts = (dv.get("actions") or [])[:5]
        y = CONTENT_TOP + 32
        for a in acts:
            via = str(a.get("via", ""))
            zones.append(Zone(8, y, W - 8, y + (ROW_H - 6), "playbook", via,
                              str(a.get("do", ""))[:48], "row", via.replace("redux ", "")))
            y += ROW_H - 2
        return View("detail", product, tuple(zones), tuple(spans))

    def _launch_from_via(self, via: str) -> None:
        """Resolve a playbook 'via' command into a deck action, pre-filled from the
        device, and open its action screen so you can tweak then RUN."""
        cmd = via.replace("redux ", "").strip()
        base: Optional[Action] = None
        for aid in sorted(deck_actions.ACTIONS, key=len, reverse=True):
            if cmd == aid or cmd.startswith(aid + " "):
                base = deck_actions.ACTIONS[aid]
                break
        if base is None:
            toks = cmd.split()
            twoword = len(toks) > 1 and not toks[1].startswith("-")
            base = Action(id=(" ".join(toks[:2]) if twoword else toks[:1][0] if toks else cmd),
                          title=cmd[:20], group="")
        self.action = base
        self.chosen = {p.name: p.default for p in base.params if p.default}
        cat = (self.device or {}).get("category")
        if cat:
            for p in base.params:
                if p.name == "category" or "device-category" in p.flag:
                    self.chosen[p.name] = str(cat)
        self.screen, self.offset, self._action_from = "action", 0, "detail"

    def _view_toggle(self) -> View:
        """A big two/few-state switch (FIELD/LAB, on/off) — one tap flips it."""
        a = self.action
        assert a is not None
        p = next((q for q in a.params if q.name == a.toggle_param), a.params[0])
        opts = candidates_for(p.source, self.state, self.chosen)
        zones: List[Zone] = list(self._header(a.title, back=True))
        spans = [Span(70, 8, a.title[:24], "title"),
                 Span(10, HEADER_H + 2, a.note[:60], "muted")]
        current = ("lab" if self.state.mode_lab else "field") if a.id == "mode" \
            else choice_value(self.chosen.get(p.name, "") or "")
        n = max(1, len(opts))
        bw = (W - 16 - (n - 1) * 10) // n
        for i, opt in enumerate(opts):
            x0 = 8 + i * (bw + 10)
            on = choice_value(opt) == current
            zones.append(Zone(x0, CONTENT_TOP + 40, x0 + bw, CONTENT_TOP + 140,
                              "toggle", opt, choice_label(opt),
                              "toggleon" if on else "toggleoff"))
        spans += (Span(14, CONTENT_TOP + 158,
                       "tap a posture to switch — takes effect immediately", "muted"),)
        return View("action", a.title, tuple(zones), tuple(spans))

    def _view_targets(self) -> View:
        """A ranked target list: multi-select which APs to run the action on, with
        signal + crackability at a glance. ARM & RUN uses the ones you ticked."""
        a = self.action
        assert a is not None
        p = a.params[0]
        zones: List[Zone] = list(self._header(a.title, back=True))
        spans = [Span(70, 8, a.title[:22], "title"),
                 Span(10, HEADER_H + 2, a.note[:60], "muted")]
        tgts = self.state.targets
        cur = self.chosen.get(p.name)
        sel = set(cur) if isinstance(cur, list) else set()
        if not tgts:
            spans += (Span(14, CONTENT_TOP + 24,
                           "no targets yet — run Survey / Rank targets first", "muted"),)
            return View("action", a.title, tuple(zones), tuple(spans))
        rows = 4                                   # room for the ARM & RUN bar
        window = tgts[self.offset:self.offset + rows]
        for i, t in enumerate(window):
            y0 = CONTENT_TOP + 6 + i * ROW_H
            on = t.label in sel
            badge = "crackable" if t.crackable else "hard"
            sub = f"{t.security or '—'}  ·  {badge}" + (f"  ·  {t.value}" if t.value else "")
            zones.append(Zone(8, y0, W - 8, y0 + ROW_H - 4, "target", t.label,
                              t.label, "targeton" if on else "targetoff", sub, t.signal))
        if len(tgts) > rows:
            zones += self._scrollers(len(tgts))
        ready = bool(sel)
        label = f"ARM & RUN  ({len(sel)})" if ready else "tick targets ↑"
        zones.append(Zone(8, FOOTER_Y - RUN_H, W - 8, FOOTER_Y, "run",
                          a.id if ready else "", label, "confirm"))
        return View("action", a.title, tuple(zones), tuple(spans))

    def _view_action(self) -> View:
        a = self.action
        assert a is not None
        modified = self.is_modified()
        zones: List[Zone] = list(self._header(a.title, back=True))
        spans = [Span(70, 8, a.title[:24], "title"),
                 Span(10, HEADER_H + 2, a.note[:60], "muted"),
                 Span(W - 104, HEADER_H + 2,
                      "✎ MODIFIED" if modified else "⟳ AUTO",
                      "warn" if modified else "accent")]
        y = CONTENT_TOP + 16
        for p in a.visible_params(self.chosen):     # cascading: only valid fields show
            zones.append(Zone(8, y, W - 8, y + PARAM_ROW - 3, "param", p.name,
                              p.label, "pick", self._field_display(p)))
            y += PARAM_ROW
        # RUN button; a reset-to-auto control appears once anything is overridden.
        ready = a.ready(self.chosen)
        style = "confirm" if a.power_tier else "run"
        if not ready:
            label = "pick required ↑"
        elif a.power_tier:
            label = "ARM & RUN"
        else:
            label = "RUN" if modified else "RUN (auto)"
        run_x1 = int(W * 0.66) if modified else W - 8
        zones.append(Zone(8, FOOTER_Y - RUN_H, run_x1, FOOTER_Y, "run",
                          a.id if ready else "", label, style))
        if modified:
            zones.append(Zone(run_x1 + 6, FOOTER_Y - RUN_H, W - 8, FOOTER_Y,
                              "reset", "", "⟳ auto", "cancel"))
        if a.power_tier:
            spans += (Span(10, FOOTER_Y - RUN_H - 15,
                           "⚡ transmits/fires — confirm next", "warn"),)
        return View("action", a.title, tuple(zones), tuple(spans))

    def _view_picker(self) -> View:
        p = self.picker_param
        assert p is not None
        opts = candidates_for(p.source, self.state, self.chosen)
        zones: List[Zone] = list(self._header(p.label, back=True))
        title = ("Select " if p.multi else "Pick ") + p.label
        spans = [Span(70, 8, title[:26], "title")]
        if not opts:
            spans += (Span(14, CONTENT_TOP + 20,
                           "none yet — arm/scan first, or type it", "muted"),)
            if p.is_free_text:
                zones.append(Zone(8, CONTENT_TOP + 50, W - 8, CONTENT_TOP + 50 + ROW_H,
                                  "pick", "__keyboard__", "⌨ type it", "pick"))
            return View("picker", p.label, tuple(zones), tuple(spans))
        current = self.chosen.get(p.name)
        sel = set(current) if isinstance(current, list) else ({current} if current else set())
        rows = LIST_ROWS - 1 if p.multi else LIST_ROWS      # leave room for DONE
        window = opts[self.offset:self.offset + rows]
        for i, opt in enumerate(window):
            y0 = CONTENT_TOP + i * ROW_H
            if p.multi:
                style = "checkon" if opt in sel else "checkoff"
                zones.append(Zone(8, y0, W - 8, y0 + ROW_H - 4, "pick", opt,
                                  choice_label(opt), style))
            else:
                mark = "● " if opt in sel else ""
                zones.append(Zone(8, y0, W - 8, y0 + ROW_H - 4, "pick", opt,
                                  mark + choice_label(opt), "row"))
        scroll = len(opts) > rows
        if scroll:
            zones += self._scrollers(len(opts))
        if p.multi:
            zones.append(Zone(8, FOOTER_Y - RUN_H, W - 8, FOOTER_Y, "pickdone", "",
                              f"DONE  ({len(sel)} chosen)", "run"))
        return View("picker", p.label, tuple(zones), tuple(spans), scroll)

    def _view_confirm(self) -> View:
        a = self.action
        assert a is not None
        cmd = self.command(a)
        zones: List[Zone] = list(self._header("CONFIRM", back=True))
        spans = [Span(W // 2 - 50, 8, "CONFIRM", "title"),
                 Span(14, HEADER_H + 8, ("⚡ " + a.title) if a.power_tier else a.title, "warn"),
                 Span(14, HEADER_H + 30, "this will run:", "muted"),
                 Span(14, HEADER_H + 48, cmd[:62], "value")]
        if len(cmd) > 62:
            spans += (Span(14, HEADER_H + 64, cmd[62:124], "value"),)
        if a.lab_only and not self.state.mode_lab:
            spans += (Span(14, HEADER_H + 84, "LAB-only — enabling for this run", "warn"),)
        zones.append(Zone(8, FOOTER_Y - RUN_H, (W // 2) - 4, FOOTER_Y, "cancel", "",
                          "CANCEL", "cancel"))
        zones.append(Zone((W // 2) + 4, FOOTER_Y - RUN_H, W - 8, FOOTER_Y, "confirm",
                          a.id, "CONFIRM ⚡" if a.power_tier else "CONFIRM", "confirm"))
        return View("confirm", "CONFIRM", tuple(zones), tuple(spans))

    # ---- tap dispatch -------------------------------------------------------- #
    def tap(self, x: int, y: int) -> Optional[Tuple[str, str]]:
        """Apply a tap. Returns an intent for the caller to execute:
          ("run", command)      — safe action, run it
          ("confirm", command)  — power-tier action confirmed, run it
          ("keyboard", param)   — open the on-screen keyboard for a free-text param
        or None for pure navigation."""
        z = hit(self.view().zones, x, y)
        if z is None:
            return None
        if z.kind == "home":
            self._reset()
            return None
        if z.kind == "back":
            return self._back()
        if z.kind == "scroll":
            self._scroll(z.value)
            return None
        if z.kind == "category":
            self.category, self.offset, self.screen = z.value, 0, "category"
            return None
        if z.kind == "action":
            entry_title = z.label.replace("  ⚡", "")
            self.action = self._resolve(z.value, self.category or "", entry_title)
            self.chosen = {p.name: p.default for p in self.action.params if p.default}
            self.offset, self.screen, self._action_from = 0, "action", "category"
            return None
        if z.kind == "param":
            p = next((q for q in self.action.params if q.name == z.value), None)
            if p is None:
                return None
            if p.is_free_text and not candidates_for(p.source, self.state, self.chosen):
                return ("keyboard", p.name)
            self.picker_param, self.offset, self.screen = p, 0, "picker"
            return None
        if z.kind == "pick":
            if z.value == "__keyboard__":
                return ("keyboard", self.picker_param.name if self.picker_param else "")
            p = self.picker_param
            if p is not None and p.multi:
                cur = self.chosen.get(p.name)
                lst = list(cur) if isinstance(cur, list) else ([cur] if cur else [])
                lst.remove(z.value) if z.value in lst else lst.append(z.value)
                self.chosen[p.name] = lst
                return None                        # stay on the picker (toggle)
            if p is not None:
                self.chosen[p.name] = z.value
            self.screen, self.offset = "action", 0
            return None
        if z.kind == "pickdone":
            self.screen, self.offset = "action", 0
            return None
        if z.kind == "reset":
            self.reset_auto()
            return None
        if z.kind == "toggle":
            if self.action is not None:
                self.chosen[self.action.toggle_param] = z.value
                self.last_run = self.command()
                return ("run", self.last_run)
            return None
        if z.kind == "target":
            if self.action is not None:
                pname = self.action.params[0].name
                cur = self.chosen.get(pname)
                lst = list(cur) if isinstance(cur, list) else ([cur] if cur else [])
                lst.remove(z.value) if z.value in lst else lst.append(z.value)
                self.chosen[pname] = lst
            return None
        if z.kind == "playbook":
            self._launch_from_via(z.value)
            return None
        if z.kind == "run":
            if not z.value:                       # not ready — required pick missing
                return None
            if self.action and self.action.power_tier:
                self.screen = "confirm"
                return None
            self.last_run = self.command()
            return ("run", self.last_run)
        if z.kind == "confirm":
            self.last_run = self.command()
            return ("confirm", self.last_run)
        if z.kind == "cancel":
            self.screen = "action"
            return None
        return None

    def _scroll(self, direction: str) -> None:
        self.offset = max(0, self.offset + (1 if direction == "down" else -1))

    def _back(self) -> None:
        if self.screen == "action":
            if self._action_from == "detail" and self.device:
                self.screen, self.offset = "detail", 0
            else:
                self.screen, self.offset = "category", 0
        elif self.screen == "picker":
            self.screen, self.offset = "action", 0
        elif self.screen == "confirm":
            self.screen = "action"
        elif self.screen == "detail":
            self._reset()
        elif self.screen == "category":
            self._reset()
        else:
            self._reset()
        return None

    def _reset(self) -> None:
        self.screen, self.category, self.action = "menu", None, None
        self.chosen, self.picker_param, self.offset = {}, None, 0

    def set_text(self, param: str, value: str) -> None:
        """The keyboard calls back here with the typed value for a free-text param."""
        self.chosen[param] = value
        self.screen = "action"
