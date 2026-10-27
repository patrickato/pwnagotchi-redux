"""Default-credential pack keyed to device type."""
from redux.netrecon.default_creds import (
    creds_for, creds_for_identity, users_and_passwords, _COMMON,
)


def test_category_defaults_come_first_then_common():
    pairs = creds_for(category="camera")
    assert ("admin", "12345") in pairs             # a camera default
    # common set is appended after category-specific
    assert ("root", "root") in pairs


def test_vendor_refines_before_category():
    pairs = creds_for(category="router", vendor="Ubiquiti Networks")
    assert pairs[0] == ("ubnt", "ubnt")            # vendor-specific leads


def test_blank_password_represented_as_empty_string():
    pairs = creds_for(category="printer")
    assert ("admin", "") in pairs                  # printer admin / blank


def test_include_common_can_be_disabled():
    only = creds_for(category="camera", include_common=False)
    assert ("admin", "12345") in only
    assert ("user", "user") not in only            # common omitted


def test_dedupe_no_duplicate_pairs():
    pairs = creds_for(category="router", vendor="tp-link")
    assert len(pairs) == len(set(pairs))


def test_users_and_passwords_split():
    users, passwords = users_and_passwords(creds_for(category="camera"))
    assert "admin" in users and "root" in users
    assert "12345" in passwords and "" in passwords


def test_creds_for_identity_from_identify_dict():
    identity = {"category": "camera", "vendor": "Hikvision", "product": "IP camera"}
    pairs = creds_for_identity(identity)
    assert ("admin", "12345") in pairs             # Hikvision default


def test_unknown_category_still_gets_common():
    pairs = creds_for(category="voice-assistant")   # empty specific set
    assert all(p in pairs for p in _COMMON[:3])     # still gets the common defaults


def test_lists_are_substantial_per_device():
    # a real run works through a sizable list, not 2-3
    assert len(creds_for(category="camera")) >= 25
    assert len(creds_for(category="router")) >= 20


def test_dahua_and_hikvision_vendor_specifics():
    assert ("888888", "888888") in creds_for(category="camera", vendor="Dahua")
    assert creds_for(category="camera", vendor="Hikvision")[0] == ("admin", "12345")
