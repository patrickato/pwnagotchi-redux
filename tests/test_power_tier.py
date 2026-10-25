"""Power-tier gating: FIELD hides POWER tools, LAB needs per-action confirmation,
FORBIDDEN is never available."""
from redux.core.power_tier import (
    Tier, PowerCapability, register, get, tier_of, is_power, needs_confirmation,
    visible_in, authorize_power,
)


# ---- registry ------------------------------------------------------------ #

def test_seeded_tiers():
    assert tier_of("sdr.replay") == Tier.POWER
    assert tier_of("rf.jam") == Tier.FORBIDDEN
    assert tier_of("badusb.hid") == Tier.POWER
    assert tier_of("unknown.cap") == Tier.NORMAL      # default


def test_power_caps_flagged():
    assert is_power("ir.blast") and needs_confirmation("ir.blast")
    assert not needs_confirmation("rf.jam")           # forbidden isn't "confirmable"


# ---- visibility: the "don't even appear in FIELD" rule ------------------ #

def test_field_hides_power_and_forbidden():
    vis = {c.id for c in visible_in("field")}
    assert "sdr.replay" not in vis                    # POWER hidden in FIELD
    assert "badusb.hid" not in vis
    assert "rf.jam" not in vis                         # FORBIDDEN never shown


def test_lab_shows_power_but_never_forbidden():
    vis = {c.id for c in visible_in("lab")}
    assert "sdr.replay" in vis and "badusb.hid" in vis   # POWER available in LAB
    assert "rf.jam" not in vis and "beacon.spam" not in vis   # FORBIDDEN never


# ---- the gate ------------------------------------------------------------ #

def test_power_refused_in_field():
    ok, why = authorize_power("sdr.replay", lab_mode=False, confirmed=True)
    assert not ok and "not available in FIELD" in why


def test_power_in_lab_needs_confirmation():
    ok, why = authorize_power("sdr.replay", lab_mode=True, confirmed=False)
    assert not ok and "confirm required" in why
    ok2, why2 = authorize_power("sdr.replay", lab_mode=True, confirmed=True)
    assert ok2 and "explicit confirmation" in why2


def test_forbidden_never_authorized():
    for lab in (True, False):
        for conf in (True, False):
            ok, why = authorize_power("rf.jam", lab_mode=lab, confirmed=conf)
            assert not ok and "FORBIDDEN" in why


def test_normal_capability_passes_through():
    ok, why = authorize_power("wifi.scan", lab_mode=False, confirmed=False)
    assert ok and "ordinary gating" in why            # this gate doesn't apply


# ---- extensibility ------------------------------------------------------- #

def test_new_feature_can_register_as_power():
    register(PowerCapability("test.超", "Test power tool", Tier.POWER, "test risk"))
    assert is_power("test.超")
    ok, _ = authorize_power("test.超", lab_mode=True, confirmed=False)
    assert not ok                                      # still needs confirm
