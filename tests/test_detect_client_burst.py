from redux.detect.client_burst import ClientBurstDetector
from redux.detect.frames import Frame, FrameType


def test_fires():
    d = ClientBurstDetector(window_s=10.0, unique_threshold=5)
    alert = None
    for i in range(5):
        alert = d.feed(Frame(type=FrameType.PROBE_REQ, ts=float(i), src=f"aa:aa:aa:aa:aa:{i:02x}"))
    assert alert is not None and "client burst" in alert.reason


def test_quiet_same_src():
    d = ClientBurstDetector(window_s=10.0, unique_threshold=5)
    for i in range(10):
        assert d.feed(Frame(type=FrameType.PROBE_REQ, ts=float(i), src="aa:aa:aa:aa:aa:01")) is None
