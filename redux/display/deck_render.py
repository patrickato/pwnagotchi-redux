"""Render a control-deck View to the real 480×320 TFT frame.

Uses the fbui's own fonts, colors and panel helpers so the deck looks like the
rest of Augur — dark instrument theme, cyan accents. Style tokens from deck_model
map to fonts/colors here (the model stays pixel-free). Text never goes below ~8px
(the operator's readable floor is 7px); tap zones are drawn big enough to see what
you're about to hit.
"""
from __future__ import annotations

from typing import Tuple

from . import fbui
from .fbui import (BACKGROUND, PANEL, PANEL2, STROKE, INK, SUBTLE, TEAL, CY2,
                   AMBER, RED, GRN, MAG, MUT, _font, _panel, fit_text)
from .deck_model import View, Zone, Span, W, H


# style token → (size, bold, color)
_SPAN = {
    "title": (15, True, INK),
    "label": (13, False, INK),
    "muted": (11, False, SUBTLE),
    "value": (12, True, CY2),
    "accent": (13, True, TEAL),
    "warn": (11, True, AMBER),
    "tiny": (8, False, SUBTLE),       # 8px — above the 7px readable floor
}


def _text(draw, x, y, text, style):
    size, bold, fill = _SPAN.get(style, _SPAN["label"])
    draw.text((x, y), text, font=_font(size, bold=bold), fill=fill)


def _center(draw, box, text, font, fill):
    x0, y0, x1, y1 = box
    tw = draw.textlength(text, font=font)
    bb = draw.textbbox((0, 0), text, font=font)
    th = bb[3] - bb[1]
    draw.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2 - bb[1]),
              text, font=font, fill=fill)


def _signal(draw, x, cy, pct):
    """A 4-bar signal glyph, filled proportional to pct (0..100)."""
    bars = 4
    filled = round(max(0, min(100, pct)) / 100 * bars)
    for i in range(bars):
        h = 5 + i * 4
        bx = x + i * 8
        col = TEAL if i < filled else (40, 60, 70)
        draw.rectangle((bx, cy - h // 2, bx + 5, cy + 6), fill=col)


def _draw_zone(draw, z: Zone):
    box = (z.x0, z.y0, z.x1, z.y1)
    if z.style in ("toggleon", "toggleoff"):
        on = z.style == "toggleon"
        edge = TEAL if on else MUT
        _panel(draw, box, radius=12, fill=(10, 28, 30) if on else PANEL2,
               outline=edge, width=3 if on else 2)
        _center(draw, (z.x0, z.y0, z.x1, z.y1 - 22), z.label,
                _font(24, bold=True), edge if on else INK)
        _center(draw, (z.x0, z.y1 - 30, z.x1, z.y1 - 6),
                "● ACTIVE" if on else "tap to switch",
                _font(10, bold=True), TEAL if on else MUT)
        return
    if z.style in ("targeton", "targetoff"):
        on = z.style == "targeton"
        _panel(draw, box, radius=7, fill=PANEL2, outline=TEAL if on else STROKE,
               width=2 if on else 1)
        cy = (z.y0 + z.y1) // 2
        bx = z.x0 + 12
        draw.rounded_rectangle((bx, cy - 9, bx + 18, cy + 9), radius=4,
                               fill=(10, 28, 30) if on else PANEL,
                               outline=TEAL if on else MUT, width=2)
        if on:
            draw.line((bx + 4, cy + 1, bx + 8, cy + 5), fill=TEAL, width=2)
            draw.line((bx + 8, cy + 5, bx + 15, cy - 5), fill=TEAL, width=2)
        draw.text((bx + 30, z.y0 + 6), fit_text(draw, z.label, _font(13, bold=True), 180),
                  font=_font(13, bold=True), fill=INK)
        crackable = "crackable" in z.sub
        draw.text((bx + 30, z.y0 + 22), fit_text(draw, z.sub, _font(10), 230),
                  font=_font(10), fill=GRN if crackable else SUBTLE)
        if z.sig >= 0:
            _signal(draw, z.x1 - 52, cy, z.sig)
        return
    if z.style == "tile":
        _panel(draw, box, radius=9, fill=PANEL, outline=STROKE, width=1)
        draw.rectangle((z.x0, z.y0 + 8, z.x0 + 3, z.y1 - 8), fill=TEAL)   # accent bar
        _center(draw, (z.x0 + 10, z.y0, z.x1, z.y1), fit_text(draw, z.label, _font(12, bold=True), z.x1 - z.x0 - 20),
                _font(12, bold=True), INK)
        return
    if z.style == "row":
        _panel(draw, box, radius=7, fill=PANEL2, outline=STROKE, width=1)
        label = fit_text(draw, z.label, _font(13, bold=True), z.x1 - z.x0 - 28)
        if z.sub:                                   # two-line row: name + description
            draw.text((z.x0 + 12, z.y0 + 6), label, font=_font(13, bold=True), fill=INK)
            desc = fit_text(draw, z.sub, _font(10), z.x1 - z.x0 - 28)
            draw.text((z.x0 + 12, z.y0 + 23), desc, font=_font(10), fill=SUBTLE)
        else:                                       # single-line row (pick-list)
            draw.text((z.x0 + 12, z.y0 + (z.y1 - z.y0) / 2 - 8), label,
                      font=_font(13, bold=True), fill=INK)
        draw.text((z.x1 - 18, z.y0 + (z.y1 - z.y0) / 2 - 9), "›",
                  font=_font(15, bold=True), fill=MUT)
        return
    if z.style == "pick":
        _panel(draw, box, radius=7, fill=PANEL2, outline=STROKE, width=1)
        draw.text((z.x0 + 12, z.y0 + 5), z.label, font=_font(10), fill=SUBTLE)
        choice = fit_text(draw, z.sub, _font(13, bold=True), z.x1 - z.x0 - 40)
        auto_or_empty = z.sub.startswith("auto:") or z.sub in ("pick…", "(keyboard)", "")
        draw.text((z.x0 + 12, z.y0 + 18), choice,
                  font=_font(13, bold=True), fill=(MUT if auto_or_empty else TEAL))
        draw.text((z.x1 - 20, z.y0 + (z.y1 - z.y0) / 2 - 9), "⌄",
                  font=_font(14, bold=True), fill=MUT)
        return
    if z.style in ("checkon", "checkoff"):          # multi-select row w/ drawn checkbox
        _panel(draw, box, radius=7, fill=PANEL2, outline=STROKE, width=1)
        cy = (z.y0 + z.y1) // 2
        bx = z.x0 + 14
        on = z.style == "checkon"
        draw.rounded_rectangle((bx, cy - 9, bx + 18, cy + 9), radius=4,
                               fill=(10, 28, 30) if on else PANEL,
                               outline=TEAL if on else MUT, width=2)
        if on:                                      # a drawn check, not a font glyph
            draw.line((bx + 4, cy + 1, bx + 8, cy + 5), fill=TEAL, width=2)
            draw.line((bx + 8, cy + 5, bx + 15, cy - 5), fill=TEAL, width=2)
        label = fit_text(draw, z.label, _font(13, bold=True), z.x1 - z.x0 - 64)
        draw.text((bx + 30, cy - 8), label, font=_font(13, bold=True),
                  fill=INK if on else SUBTLE)
        return
    if z.style in ("run", "confirm", "cancel"):
        edge = {"run": TEAL, "confirm": AMBER, "cancel": MUT}[z.style]
        fill = {"run": (10, 28, 30), "confirm": (34, 26, 10), "cancel": PANEL2}[z.style]
        if not z.value and z.kind == "run":                 # disabled (pick required)
            edge, fill = STROKE, PANEL2
        _panel(draw, box, radius=9, fill=fill, outline=edge, width=2)
        col = edge if z.value or z.kind != "run" else MUT
        _center(draw, box, z.label, _font(15, bold=True), col)
        return
    if z.style in ("kbkey", "kbctl"):
        _panel(draw, box, radius=6, fill=PANEL if z.style == "kbkey" else PANEL2,
               outline=STROKE, width=1)
        _center(draw, box, z.label, _font(16 if z.style == "kbkey" else 13, bold=True),
                INK if z.style == "kbkey" else CY2)
        return
    if z.style == "nav":
        _center(draw, box, z.label, _font(12, bold=True), SUBTLE)
        return
    _panel(draw, box, radius=6, fill=PANEL2, outline=STROKE, width=1)
    _center(draw, box, z.label, _font(12), INK)


def render(view: View):
    """Return a 480×320 PIL image of the given deck View."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BACKGROUND)
    draw = ImageDraw.Draw(img)
    # header separator
    draw.line((0, 30, W, 30), fill=STROKE, width=1)
    # spans (static text)
    for s in view.spans:
        _text(draw, s.x, s.y, s.text, s.style)
    # zones (tappable)
    for z in view.zones:
        _draw_zone(draw, z)
    return img


def render_png(view: View) -> bytes:
    from io import BytesIO
    out = BytesIO()
    render(view).save(out, format="PNG")
    return out.getvalue()
