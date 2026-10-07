"""redux.face — Augur's creature face: one deterministic expression engine
shared by the on-device TFT and the web face, so the device has one identity
however you look at it. See `face.py` for the house style (motion marks real
events only; the TFT stays static/event-driven; nothing invented)."""
from .face import AugurState, AugurFace, face_for, eyes

__all__ = ["AugurState", "AugurFace", "face_for", "eyes"]
