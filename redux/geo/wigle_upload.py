"""Opt-in, upload-when-online orchestration for the WiGLE export queue.

`WigleExportQueue` packages pending rows but deliberately never transmits (repo
rule: nothing phones home uninvited). This wires the missing last step *safely*:
upload the pending package, but ONLY when (1) the operator has turned it on,
(2) the device is actually online, and (3) there is something new to send — each
a glass-box gate, fail-closed. The HTTP transport is an INJECTED `uploader`, so
the sequencing/gating is fully tested with no network, and the operator supplies
the real WiGLE/wpa-sec uploader (with their own credentials) at the edge.

On a successful upload the queue marks those rows exported; on any failure or
when offline, nothing is marked and the rows stay queued for the next online
window — so a dropped link never loses data and never double-sends.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Callable, Optional

from redux.connectivity.online import OnlineStatus, check_online
from redux.geo.db import SightingStore
from redux.geo.wigle import write_wigle_csv
from redux.geo.wigle_queue import WigleExportQueue

# uploader(csv_bytes) -> bool : True == the service accepted the package.
Uploader = Callable[[bytes], bool]


@dataclass(frozen=True)
class UploadReport:
    uploaded: int        # rows actually accepted this run
    attempted: bool      # did we call the uploader at all
    online: bool
    reason: str          # glass-box


def upload_when_online(
    store: SightingStore,
    queue: WigleExportQueue,
    *,
    uploader: Uploader,
    enabled: bool,
    online_status: Optional[OnlineStatus] = None,
    online_check: Optional[Callable[[], OnlineStatus]] = None,
    kind: Optional[str] = None,
    app_release: str = "redux-geo",
) -> UploadReport:
    """Gate → package → upload → mark. Returns a glass-box UploadReport.

    enabled: the operator opt-in. Being online is necessary but never sufficient.
    online_status/online_check: inject an OnlineStatus or a zero-arg check for
    tests; by default it calls connectivity.online.check_online().
    """
    if not enabled:
        return UploadReport(0, False, False,
                            "refused: upload disabled — opt-in (set the upload flag to enable)")

    status = online_status if online_status is not None else (online_check or check_online)()
    pending_n = queue.pending_count(store, kind=kind)
    if not status.online:
        return UploadReport(0, False, False,
                            f"deferred: {status.reason} — {pending_n} row(s) queued for the next online window")
    if pending_n == 0:
        return UploadReport(0, False, True, "nothing to upload — all rows already exported")

    rows = queue.pending(store, kind=kind)
    buf = io.StringIO()
    write_wigle_csv(rows, buf, app_release=app_release)
    payload = buf.getvalue().encode("utf-8")

    try:
        accepted = uploader(payload)
    except Exception as e:  # operator-supplied transport — report, don't crash, don't mark
        return UploadReport(0, True, True,
                            f"upload failed: {e} — {pending_n} row(s) left queued")
    if not accepted:
        return UploadReport(0, True, True,
                            f"upload rejected by the service — {pending_n} row(s) left queued")

    queue.mark_exported(rows)
    return UploadReport(len(rows), True, True,
                        f"uploaded {len(rows)} new row(s) to WiGLE ({status.reason})")
