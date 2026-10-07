"""Expeditions — a named field session + a glance "Wrapped" summary.

An expedition frames what the device already records (sightings over time) into a
session that feels like it mattered: start it, walk, end it, and get a one-glance
recap — what you saw, what was new, how long you were out. Pure derivation over
the sightings store; it invents nothing.
"""
from .session import Expedition, ExpeditionLog, wrapped

__all__ = ["Expedition", "ExpeditionLog", "wrapped"]
