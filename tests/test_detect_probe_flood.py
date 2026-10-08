from redux.detect.frames import Frame, FrameType
from redux.detect.probe_flood import ProbeFloodDetector


def test_fires_on_burst():
    d = ProbeFloodDetector(window_s=5.0, threshold=5)
    alert = None
    for i in range(5):
        alert = d.feed(Frame(type=FrameType.PROBE_REQ, ts=float(i), src=f"aa:aa:aa:aa:aa:{i:02x}"))
    assert alert is not None
    assert "probe flood" in alert.reason


def test_quiet_on_sparse():
    d = ProbeFloodDetector(window_s=5.0, threshold=20)
    for i in range(5):
        assert d.feed(Frame(type=FrameType.PROBE_REQ, ts=float(i * 10))) is None
