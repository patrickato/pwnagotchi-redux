"""redux.sentinel — deploy-and-watch guardian (blue/purple).

Point the detector suite + CSI presence-sensing at a space and route glass-box
alerts out over a notifier (LoRa in the field, log/webhook anywhere). A consumer
of alerts the detector suite already emits (duck-typed, never edits that lane) and
CSI readings from the sense engine: severity classification, windowed de-dup, and
an armed/home state so CSI motion only alerts when you've left. Pairs with
`redux persona apply blue`.
"""
from .sentinel import (
    Sentinel, SentinelEvent, Severity, CollectingNotifier, CallableNotifier,
)

__all__ = [
    "Sentinel", "SentinelEvent", "Severity", "CollectingNotifier", "CallableNotifier",
]
