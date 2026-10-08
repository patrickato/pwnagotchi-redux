"""Face-packs — selectable looks for Augur's face.

The face *state machine* (what expression, and why) is fixed in `face.py` — it's
driven by real machine state and that never changes. A pack only swaps the *glyphs*
that draw each state, so you can run the default corvid, a stark owl, or the fox
(the fox-hunt mascot) without touching the honesty of what the face means.

Every pack covers all eight states and ships a pure-ASCII fallback per state (for
mono panels / `--ascii`), and all glyphs are single code points so the fixed-width
TFT layout stays aligned.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .face import AugurState as S

DEFAULT_PACK = "augur"

# pack -> state -> (nice, ascii)
PACKS: Dict[str, Dict[S, Tuple[str, str]]] = {
    # the corvid — the default identity
    "augur": {
        S.WATCH:  ("‹·_·›", "<._.>"),
        S.READ:   ("‹o_·›", "<o_.>"),
        S.HUNT:   ("‹►_◄›", "<=_=>"),
        S.MARK:   ("‹!_·›", "<!_.>"),
        S.CACHE:  ("‹^_^›", "<^_^>"),
        S.RUFFLE: ("‹✺_✺›", "<*_*>"),
        S.BLIND:  ("‹-_-›", "<-_->"),
        S.ROOST:  ("‹u_u›", "<u_u>"),
    },
    # round-eyed night watcher (all ASCII already — maximally mono-safe)
    "owl": {
        S.WATCH:  ("(o.o)", "(o.o)"),
        S.READ:   ("(o.-)", "(o.-)"),
        S.HUNT:   ("(O.O)", "(O.O)"),
        S.MARK:   ("(!.o)", "(!.o)"),
        S.CACHE:  ("(^.^)", "(^.^)"),
        S.RUFFLE: ("(@.@)", "(@.@)"),
        S.BLIND:  ("(-.-)", "(-.-)"),
        S.ROOST:  ("(u.u)", "(u.u)"),
    },
    # pointy-eared — the fox-hunt (RSSI direction-finding) mascot
    "fox": {
        S.WATCH:  ("^·_·^", "^._.^"),
        S.READ:   ("^o_·^", "^o_.^"),
        S.HUNT:   ("^>_<^", "^>_<^"),
        S.MARK:   ("^!_·^", "^!_.^"),
        S.CACHE:  ("^^_^^", "^^_^^"),
        S.RUFFLE: ("^x_x^", "^x_x^"),
        S.BLIND:  ("^-_-^", "^-_-^"),
        S.ROOST:  ("^u_u^", "^u_u^"),
    },
}


def list_packs() -> List[str]:
    return sorted(PACKS)


def eyes_for(state: S, pack: str = DEFAULT_PACK, ascii: bool = False) -> str:
    """Glyphs for a state in a pack. Unknown pack → the default; a pack missing a
    state → that state from the default (so a pack can never render blank)."""
    table = PACKS.get(pack, PACKS[DEFAULT_PACK])
    nice, asc = table.get(state, PACKS[DEFAULT_PACK][state])
    return asc if ascii else nice
