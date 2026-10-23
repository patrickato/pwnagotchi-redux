"""Opt-in connectivity management. No Bluetooth side effects in base runtime."""
from .online import OnlineStatus, check_online

__all__ = ["OnlineStatus", "check_online"]
