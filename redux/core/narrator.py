"""Narrator — the glass-box creature voice (atlas I1).

The whole product bet is legibility: the device tells you *why* it did what it
did. Every subsystem already produces a human-readable reason — the orchestrator's
role assignments (`Assignment.reasons`/`.warnings`), the detectors' alerts
(`Alert.reason`), the crackability classifier (`Assessment.reason`). This turns
those structured reasons into the creature's running narration, with a mood that
reflects what's actually happening — never invented, always traceable to a real
reason.

Pure presentation over data that already exists. It renders; it decides nothing
and transmits nothing.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, List, Optional


class Mood(str, Enum):
    IDLE = "idle"           # nothing much happening
    HUNTING = "hunting"     # actively arranged to capture
    ALERT = "alert"         # a detector fired
    THINKING = "thinking"   # just re-decided its radios


@dataclass(frozen=True)
class Line:
    mood: Mood
    text: str          # the creature's words
    reason: str        # the underlying machine reason (glass-box; always present)


# severity → mood weight for alerts
_CRITICAL = {"critical"}


class Narrator:
    """Collects reasons/alerts and voices them as moody, deduped creature lines."""

    def __init__(self, keep: int = 50):
        self._lines: Deque[Line] = deque(maxlen=keep)
        self._mood = Mood.IDLE
        self._last_text: Optional[str] = None

    @property
    def mood(self) -> Mood:
        return self._mood

    def _emit(self, mood: Mood, text: str, reason: str) -> Optional[Line]:
        self._mood = mood
        # dedup consecutive identical utterances (don't repeat yourself on the TFT)
        if text == self._last_text:
            return None
        self._last_text = text
        line = Line(mood=mood, text=text, reason=reason)
        self._lines.append(line)
        return line

    # --- inputs ------------------------------------------------------------ #

    def say_reason(self, reason: str) -> Optional[Line]:
        """Voice an orchestrator/supervisor decision reason in Augur's register.

        Terse, concrete, honest — lead-ins echo the creature's face lexicon
        (hunt / cache / new-face / read / feathers-up), but the real machine
        reason is always carried verbatim so nothing is lost or invented.
        See docs/AUGUR.md for the voice."""
        r = (reason or "").strip()
        if not r:
            return None
        low = r.lower()
        if low.startswith("warning:"):
            return self._emit(Mood.ALERT, f"feathers up — {r[len('warning:'):].strip()}", r)
        if any(k in low for k in ("handshake", "pmkid", "cracked", "captured", "cached")):
            return self._emit(Mood.HUNTING, f"cached — {r}", r)
        if any(k in low for k in ("new ap", "new client", "new device", "new network", "new face")):
            return self._emit(Mood.HUNTING, f"new face — {r}", r)
        if "capture" in low or "recon" in low or "pointing bettercap" in low:
            return self._emit(Mood.HUNTING, f"on the hunt — {r}", r)
        if "intent" in low or "->" in r or "re-decid" in low:
            return self._emit(Mood.THINKING, f"reading the air — {r}", r)
        return self._emit(self._mood, r, r)

    def say_alert(self, alert) -> Optional[Line]:
        """Voice a detector Alert (its reason is required/glass-box by construction)."""
        reason = getattr(alert, "reason", "") or ""
        sev = getattr(alert, "severity", "warning")
        kind = getattr(getattr(alert, "kind", None), "value", "") or "alert"
        mood = Mood.ALERT
        # calm but clear — a critical alert is marked, never shouted (no "!!")
        lead = "⚠ " if sev in _CRITICAL else "— "
        return self._emit(mood, f"{lead}{kind}: {reason}", reason)

    def say_assessment(self, ssid: str, assessment) -> Optional[Line]:
        """Voice a crackability Assessment for a network (glass-box honesty)."""
        reason = getattr(assessment, "reason", "") or ""
        worth = getattr(assessment, "capture_worth_it", None)
        tag = "worth a look" if worth else "skipping"
        name = ssid or getattr(getattr(assessment, "sec_type", None), "value", "network")
        return self._emit(self._mood, f"{name}: {tag} — {reason}", reason)

    # --- output ------------------------------------------------------------ #

    def lines(self, n: Optional[int] = None) -> List[Line]:
        items = list(self._lines)
        return items[-n:] if n else items

    def latest(self) -> Optional[Line]:
        return self._lines[-1] if self._lines else None

    def tft(self, width: int = 40) -> str:
        """One line for the small TFT: mood glyph + the latest utterance, clipped."""
        line = self.latest()
        if line is None:
            return "…"
        glyph = {Mood.IDLE: "·", Mood.HUNTING: ">", Mood.ALERT: "!", Mood.THINKING: "?"}[line.mood]
        text = line.text
        body = text if len(text) <= width - 2 else text[: width - 3] + "…"
        return f"{glyph} {body}"
