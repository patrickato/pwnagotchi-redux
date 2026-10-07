"""Frame replay harness — load JSON frames and run detectors."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Union

from redux.detect.alerts import Alert
from redux.detect.engine import DetectEngine
from redux.detect.frames import Frame, FrameType

PathLike = Union[str, Path]


def frame_from_dict(d: Dict[str, Any]) -> Frame:
    """Build a Frame from a plain dict (JSON object)."""
    t = d.get("type", "other")
    if isinstance(t, FrameType):
        ft = t
    else:
        ft = FrameType(str(t).lower())
    return Frame(
        type=ft,
        ts=float(d.get("ts", 0.0)),
        bssid=str(d.get("bssid", "") or ""),
        ssid=str(d.get("ssid", "") or ""),
        src=str(d.get("src", "") or ""),
        dst=str(d.get("dst", "") or ""),
        channel=d.get("channel"),
        security=str(d.get("security", "") or ""),
        rssi=d.get("rssi"),
        wps_opcode=str(d.get("wps_opcode", "") or ""),
        ble_addr=str(d.get("ble_addr", "") or ""),
        ble_name=str(d.get("ble_name", "") or ""),
        ble_company_id=str(d.get("ble_company_id", "") or ""),
        ble_service_uuid=str(d.get("ble_service_uuid", "") or ""),
        eapol_msg=int(d.get("eapol_msg", 0) or 0),
        pmf=str(d.get("pmf", "") or ""),
    )


def load_frames_json(data: Union[str, Path, Sequence[Dict[str, Any]]]) -> List[Frame]:
    """Load frames from a JSON path, JSON string, or list of dicts."""
    if isinstance(data, (str, Path)):
        p = Path(data)
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
        else:
            raw = json.loads(str(data))
    else:
        raw = list(data)
    if isinstance(raw, dict) and "frames" in raw:
        raw = raw["frames"]
    return [frame_from_dict(x) for x in raw]


def replay(
    frames: Iterable[Frame],
    engine: DetectEngine | None = None,
) -> List[Alert]:
    """Run frames through DetectEngine; return all alerts."""
    eng = engine if engine is not None else DetectEngine()
    return eng.feed_many(frames)


def replay_json(
    data: Union[str, Path, Sequence[Dict[str, Any]]],
    engine: DetectEngine | None = None,
) -> List[Alert]:
    return replay(load_frames_json(data), engine=engine)
