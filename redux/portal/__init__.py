"""Captive portal — for AUTHORIZED client testing only.

A standard assessment capability (does a client submit credentials to a portal
on an untrusted network?). redux ships the *mechanism*: serve a page, record
what's submitted, for an engagement you've explicitly authorized. The built-in
template is deliberately **generic** — it imitates no real brand or
organization. For a real engagement the operator supplies their own page; redux
does not ship brand-impersonation templates.

Exposure follows the repo rule (least-exposed bind scope, URL always logged), and
the server refuses to start unless the engagement is explicitly authorized.
"""
from .portal import CaptivePortal, Submission, make_handler, serve, DEFAULT_TEMPLATE

__all__ = ["CaptivePortal", "Submission", "make_handler", "serve", "DEFAULT_TEMPLATE"]
