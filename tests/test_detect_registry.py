"""Hardware-free tests for detector registry (Grok D5)."""
from redux.detect.registry import REGISTRY, build_from_registry, list_detectors


def test_list_detectors():
    names = list_detectors()
    assert "deauth_flood" in names
    assert "beacon_spam" in names


def test_build_all():
    dets = build_from_registry()
    assert len(dets) == len(REGISTRY)


def test_build_subset():
    dets = build_from_registry(["deauth_flood"])
    assert len(dets) == 1
