"""Synthetic classic evdev gesture tests. No physical touchscreen access."""
import pytest

from redux.display.calibration import Calibration
from redux.display.gesture_nav import NavigationGestures, consume_gestures
from redux.display.touch_nav import _EVENT, EV_ABS, EV_SYN, EV_KEY, ABS_X, ABS_Y, BTN_TOUCH


def calibration():
    return Calibration(False, False, False, 0, 479, 0, 319, 480, 320, 0)


def ev(kind, code, value):
    return _EVENT.pack(0, 0, kind, code, value)


def contact(start, end=None, *, prepress=False):
    end = end or start
    def pos(point):
        return ev(EV_ABS, ABS_X, point[0]) + ev(EV_ABS, ABS_Y, point[1])
    begin = (pos(start) + ev(EV_KEY, BTN_TOUCH, 1) if prepress else
             ev(EV_KEY, BTN_TOUCH, 1) + pos(start))
    return begin + ev(EV_SYN, 0, 0) + pos(end) + ev(EV_KEY, BTN_TOUCH, 0)


@pytest.mark.parametrize("x,page", [(35, "overview"), (235, "captures"), (423, "doctor")])
def test_footer_taps_select_exact_pages(x, page):
    tracker = NavigationGestures(calibration(), physically_verified=True)
    actions, tail = consume_gestures(contact((x, 308)), tracker)
    assert actions == [("page", page)]
    assert tail == b""


def test_accepts_prepress_coordinates_but_never_replays_old_contact():
    tracker = NavigationGestures(calibration(), physically_verified=True)
    assert consume_gestures(contact((240, 305), prepress=True), tracker)[0] == [
        ("page", "captures")]
    assert consume_gestures(ev(EV_KEY, BTN_TOUCH, 1) +
                            ev(EV_KEY, BTN_TOUCH, 0), tracker)[0] == []


@pytest.mark.parametrize("dx,step", [(-160, 1), (160, -1)])
def test_swipes_have_screen_relative_direction(dx, step):
    tracker = NavigationGestures(calibration(), physically_verified=True)
    assert consume_gestures(contact((240, 170), (240 + dx, 175)), tracker)[0] == [
        ("swipe", step)]


def test_rotation_180_maps_visible_footer_and_swipes():
    tracker = NavigationGestures(calibration(), physically_verified=True, rotation=180)
    # Physical top-left is displayed logical bottom-right after screen rotation.
    assert consume_gestures(contact((55, 11)), tracker)[0] == [("page", "doctor")]
    assert consume_gestures(contact((320, 170), (120, 170)), tracker)[0] == [
        ("swipe", -1)]


def test_drag_diagonal_offscreen_and_crossing_tabs_are_not_taps():
    tracker = NavigationGestures(calibration(), physically_verified=True)
    assert consume_gestures(contact((135, 309), (175, 309)), tracker)[0] == []
    assert consume_gestures(contact((200, 200), (310, 290)), tracker)[0] == []
    assert consume_gestures(contact((5, 305), (-5, 305)), tracker)[0] == []


def test_syn_dropped_aborts_contact_and_does_not_reuse_coordinates():
    tracker = NavigationGestures(calibration(), physically_verified=True)
    data = (ev(EV_KEY, BTN_TOUCH, 1) + ev(EV_ABS, ABS_X, 30) +
            ev(EV_ABS, ABS_Y, 309) + ev(EV_SYN, 3, 0) +
            ev(EV_KEY, BTN_TOUCH, 1) + ev(EV_ABS, ABS_X, 240) +
            ev(EV_ABS, ABS_Y, 309) + ev(EV_KEY, BTN_TOUCH, 0))
    assert consume_gestures(data, tracker)[0] == []
    assert tracker.desynced
    # SYN_REPORT resynchronizes, and only a fresh contact then qualifies.
    assert consume_gestures(ev(EV_SYN, 0, 0) +
                            contact((240, 309)), tracker)[0] == [
        ("page", "captures")]


def test_multitouch_during_contact_rejects_action():
    tracker = NavigationGestures(calibration(), physically_verified=True)
    data = (ev(EV_KEY, BTN_TOUCH, 1) +
            ev(EV_ABS, ABS_X, 50) + ev(EV_ABS, ABS_Y, 307) +
            ev(EV_ABS, 47, 0) + ev(EV_KEY, BTN_TOUCH, 0))
    assert consume_gestures(data, tracker)[0] == []


def test_fragmented_read_and_duplicate_release():
    tracker = NavigationGestures(calibration(), physically_verified=True)
    data = contact((390, 308))
    actions, fragment = consume_gestures(data[:13], tracker)
    assert actions == []
    actions, fragment = consume_gestures(fragment + data[13:], tracker)
    assert actions == [("page", "doctor")] and fragment == b""
    assert consume_gestures(ev(EV_KEY, BTN_TOUCH, 0), tracker)[0] == []


def test_unverified_calibration_and_invalid_dimensions_refused():
    with pytest.raises(ValueError):
        NavigationGestures(calibration())
    with pytest.raises(ValueError):
        NavigationGestures(calibration(), physically_verified=True, rotation=90)
    with pytest.raises(ValueError):
        NavigationGestures(calibration(), physically_verified=True, tap_slop_pixels=0)
    with pytest.raises(ValueError):
        consume_gestures(b"x" * 8193,
                         NavigationGestures(calibration(), physically_verified=True))
