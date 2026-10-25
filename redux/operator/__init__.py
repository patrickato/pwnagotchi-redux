"""redux.operator — the autonomous glass-box kill-chain operator.

A deterministic conductor that sequences recon → capture → crack → network-pivot
per in-scope target, gating every firing step on posture + Scope + capability with
a human-readable reason, and feeding its executed-action log straight into the
engagement report. Not an LLM improvising about who to hit — an explainable,
scope-gated operator whose dry-run `plan()` shows exactly what it would do and why,
and whose `run()` executes via injected executors (testable with no radio).
"""
from .operator import Operator, Phase, Step, CHAIN
from .autopilot import autopilot, AutopilotReport, AutopilotCycle
from .adaptive import by_vendor_success

__all__ = ["Operator", "Phase", "Step", "CHAIN",
           "autopilot", "AutopilotReport", "AutopilotCycle", "by_vendor_success"]
