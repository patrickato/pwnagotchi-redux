"""Bettercap driver (skeleton).

The engine that replaces pwnagotchi's supervisor role: talk to bettercap over
its REST/websocket API directly, with no pwnagotchi in the path. This is the
contract surface; the live HTTP client is a TASKS.md item.

Scope: capture/recon on authorized/own networks. Any firing capability stays
behind the empty-by-default authorized-target allowlist (see AGENTS.md).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BettercapConfig:
    host: str = "127.0.0.1"
    port: int = 8081
    scheme: str = "http"
    # bind_scope governs exposure of any UI/API this starts; default least-exposed.
    bind_scope: str = "localhost"   # localhost | tailscale | lan


class BettercapDriver:
    """Minimal contract. Methods raise NotImplementedError until the live
    client lands — kept explicit so the interface is reviewable now."""

    def __init__(self, config: BettercapConfig | None = None):
        self.config = config or BettercapConfig()

    @property
    def base_url(self) -> str:
        return f"{self.config.scheme}://{self.config.host}:{self.config.port}/api"

    # --- lifecycle ---
    def start(self) -> None:
        raise NotImplementedError("live bettercap process control — see TASKS.md")

    def stop(self) -> None:
        raise NotImplementedError

    # --- control (session commands) ---
    def set_interface(self, iface: str) -> None:
        """Point bettercap at the interface the Radio Orchestrator chose."""
        raise NotImplementedError

    def set_channels(self, channels: list[int]) -> None:
        raise NotImplementedError

    def event_stream(self):
        """Yield bettercap events (ap.new, handshake, etc.) for Beastcore."""
        raise NotImplementedError
