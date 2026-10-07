"""redux.tft — lean on-device TFT niceties for the 3.5" panel.

A bordered, box-drawing layout with block-glyph gauges, composed as a static text
frame (no animation = no SPI/CPU heat cost). Selectable faces (plain pwnagotchi-
minimal, or the fuller status readout), monochrome-safe (glyphs not colour, with an
ascii fallback). Pure renderer — the pixels→SPI push is the thin on-device adapter.
"""
from .screen import Face, render, render_text

__all__ = ["Face", "render", "render_text"]
