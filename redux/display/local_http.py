"""Restricted, proxy-free HTTP reader for local Redux TFT/status preflights.

Only the built-in Redux status endpoint on 127.0.0.1 may be opened.
An inherited HTTP_PROXY/HTTPS_PROXY or an unexpected HTTP redirect must
never send local diagnostic traffic to a remote proxy or other host.
"""
from __future__ import annotations

import urllib.request
from urllib.parse import urlsplit


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Unexpected redirects fail with HTTPError instead of being followed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_status(url: str, timeout: float = 2):
    """Open only loopback Redux /api/status with no proxies or redirects.

    The response is context-managed by the caller. No global urllib proxy
    handler or process environment is modified. A new opener is used only
    for this bounded preflight request.
    """
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username is not None or parsed.password is not None
                or parsed.path != "/api/status" or parsed.query or parsed.fragment
                or parsed.port is None or not 1 <= parsed.port <= 65535):
            raise ValueError("only the local Redux status endpoint is allowed")
    except (TypeError, ValueError) as exc:
        raise ValueError("only the local Redux status endpoint is allowed") from exc

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect())
    return opener.open(url, timeout=timeout)
