"""TFT screen renderer — lean on-device niceties for the 3.5" panel.

The on-device face stays lean on purpose: a 3.5" SPI panel redraws over a slow bus
using Pi CPU, so animation there is real heat and battery. This renderer gives the
panel *nice* without that cost — a bordered, box-drawing layout with block-glyph
gauges, composed as a static text frame that the display layer pushes only when
something changes (no animation loop). Monochrome-safe: it relies on glyphs, never
colour, so it reads the same on a mono panel.

Pure and testable: `render(status, face=…)` turns a `Augur.status()` snapshot
into a list of fixed-width lines. The actual pixels→SPI push is the thin on-device
adapter (needs-hardware); everything here is testable with no panel.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from redux.face import face_for


class Face(str, Enum):
    PLAIN = "plain"       # pwnagotchi-minimal: creature + one status line + narration
    STATUS = "status"     # the fuller glass-box readout with gauges


# glyph sets — unicode (nice on the panel's font) or ascii fallback
_U = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
      "ml": "├", "mr": "┤", "blk": "█", "shade": "░", "dot": "·", "arr": "›",
      "on": "▣", "off": "▢", "bang": "!", "ell": "…"}
_A = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
      "ml": "+", "mr": "+", "blk": "#", "shade": ".", "dot": ".", "arr": ">",
      "on": "[x]", "off": "[ ]", "bang": "!", "ell": "~"}

_BARS = "▁▂▃▄▅▆▇█"
_MODE_FRAC = {"full": 1.0, "guarded": 0.7, "reduced": 0.4, "survival": 0.15}


def _bar(frac: float, width: int, g: Dict[str, str]) -> str:
    frac = max(0.0, min(1.0, frac))
    if g is _A:
        filled = int(round(frac * width))
        return "#" * filled + "-" * (width - filled)
    # unicode: smooth-ish block bar
    full = int(frac * width)
    rem = (frac * width) - full
    out = _BARS[-1] * full
    if full < width:
        out += _BARS[min(len(_BARS) - 1, int(rem * (len(_BARS) - 1)))]
        out += _BARS[0] * (width - full - 1)
    return out[:width]


def _clip(s: str, n: int, ell: str = "…") -> str:
    return s if len(s) <= n else s[: max(0, n - 1)] + ell


def render(status: Dict, *, face: Face = Face.STATUS, width: int = 46,
           ascii: bool = False, pack: str = "augur") -> List[str]:
    """Render a status snapshot into a fixed-width TFT frame (list of rows)."""
    g = _A if ascii else _U
    ell = g["ell"]
    inner = width - 2

    def top():
        return g["tl"] + g["h"] * inner + g["tr"]

    def mid():
        return g["ml"] + g["h"] * inner + g["mr"]

    def bot():
        return g["bl"] + g["h"] * inner + g["br"]

    def row(left: str = "", right: str = ""):
        left = str(left)
        right = str(right)
        space = inner - 2  # one pad each side
        if right:
            avail = space - len(right) - 1
            left = _clip(left, max(0, avail), ell)
            gap = space - len(left) - len(right)
            body = left + " " * max(1, gap) + right
        else:
            body = _clip(left, space, ell)
            body = body + " " * (space - len(body))
        return g["v"] + " " + body + " " + g["v"]

    creature = status.get("creature") or "augur"
    mood = (status.get("mood") or "").upper()
    intent = status.get("intent") or "—"
    persona = status.get("persona")
    posture = status.get("posture")
    narr = status.get("narration") or []
    last = narr[-1] if narr else ""

    # Augur's face — the same deterministic engine the web face uses, so the
    # creature has one identity whichever way you look at it.
    fobj = face_for(status)
    eyes = fobj.eyes(ascii=ascii, pack=pack)
    fstate = fobj.state.value.upper()

    if face is Face.PLAIN:
        sub = status.get("creature") or fobj.reason
        lines = [top(), row(f"{eyes}  augur", fstate), row(sub, mood)]
        tag = f"{intent}" + (f" · {persona}" if persona else "")
        lines.append(row(tag))
        lines.append(mid())
        lines.append(row(g["arr"] + " " + _clip(last, inner - 6, ell) if last else g["arr"]))
        lines.append(bot())
        return lines

    # STATUS face
    eng = status.get("capture_engine") or "none"
    cap = status.get("capture_iface") or "none"
    sightings = status.get("sightings")
    alerts = status.get("recent_alerts")
    gov = status.get("governor") or {}
    mode = (gov.get("mode") or "full").lower()
    sense = status.get("sense")
    sent = status.get("sentinel")

    lines = [top()]
    lines.append(row(f"{eyes}  augur", fstate or mood or "·"))
    lines.append(mid())
    hat = (persona or "none") + (f"/{posture}" if posture else "")
    lines.append(row(f"intent {intent}", hat))
    lines.append(row(f"cap {cap} {g['arr']} {eng}"))
    lines.append(row(f"sightings {sightings if sightings is not None else '—'}",
                     f"alerts {alerts if alerts is not None else '—'}"))
    bar = _bar(_MODE_FRAC.get(mode, 1.0), 10, g)
    lines.append(row(f"gov {mode.upper()}", bar))
    if isinstance(sense, dict) and sense.get("available"):
        lines.append(row(f"csi {sense.get('sense','?')}/{sense.get('occupancy','?')}"))
    if isinstance(sent, dict) and "armed" in sent:
        crit = ""
        lastev = sent.get("last") or {}
        if lastev.get("severity") == "critical":
            crit = f"  {g['bang']}{_clip(lastev.get('summary',''), 16, ell)}"
        armed = g["on"] if sent.get("armed") else g["off"]
        lines.append(row(f"sentinel {armed} d{sent.get('dispatched',0)} s{sent.get('suppressed',0)}{crit}"))
    lines.append(mid())
    lines.append(row((g["arr"] + " " + _clip(last, inner - 6, ell)) if last else (g["arr"] + " …")))
    lines.append(bot())
    return lines


def render_text(status: Dict, **kw) -> str:
    """Convenience: the frame as one newline-joined string."""
    return "\n".join(render(status, **kw))
