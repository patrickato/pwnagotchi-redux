"""Synthetic Linux evdev tests for optional XPT2046 touch page navigation."""
import os
from types import SimpleNamespace
import stat

import pytest

from redux.display import touch_nav as t


def event(type_code, code, value):
    return t._EVENT.pack(0, 0, type_code, code, value)


def test_touch_opener_refuses_guessed_or_non_input_paths():
    opened = []
    def opener(device, flags):
        opened.append((device, flags))
        return 14
    ok = t.open_touch("/dev/input/event4", opener=opener,
                      checker=lambda _: SimpleNamespace(st_mode=stat.S_IFCHR),
                      closer=lambda _: None)
    assert ok == 14
    assert opened[0][1] & os.O_RDONLY == 0
    assert opened[0][1] & os.O_NONBLOCK
    assert opened[0][1] & os.O_NOFOLLOW
    for path in ("/dev/fb1", "/dev/input/event4;rm", "event4",
                 "/tmp/event4", "/dev/input/event-1"):
        with pytest.raises(ValueError):
            t.open_touch(path, opener=opener)
    assert len(opened) == 1


def test_touch_opener_closes_non_character_device():
    closed = []
    with pytest.raises(ValueError, match="character"):
        t.open_touch("/dev/input/event7", opener=lambda *_: 23,
                     checker=lambda _: SimpleNamespace(st_mode=stat.S_IFREG),
                     closer=closed.append)
    assert closed == [23]


def swipe(start, end, *, axis=t.ABS_X):
    return b"".join([
        event(t.EV_KEY, t.BTN_TOUCH, 1),
        event(t.EV_ABS, axis, start),
        event(t.EV_SYN, 0, 0),
        event(t.EV_ABS, axis, end),
        event(t.EV_KEY, t.BTN_TOUCH, 0),
        event(t.EV_SYN, 0, 0),
    ])


def test_left_and_right_swipes_switch_pages_only_when_complete():
    tracker = t.SwipeTracker()
    moves, rem = t.consume(swipe(2900, 900), tracker)
    assert rem == b"" and moves == [1]
    assert t.navigate("overview", moves[0]) == "captures"
    moves, rem = t.consume(swipe(900, 2900), tracker)
    assert moves == [-1]
    assert t.navigate("overview", moves[0]) == "radios"
    assert t.navigate("doctor", 1) == "radios"


def test_swipe_parser_keeps_partial_records_across_reads():
    tracker = t.SwipeTracker()
    data = swipe(2900, 1400)
    part, left = t.consume(data[:19], tracker)
    assert part == [] and left == data[:19]
    part2, left = t.consume(left + data[19:], tracker)
    assert part2 == [1] and left == b""


def test_short_tap_is_not_false_swipe_and_stale_axis_is_ignored():
    tracker = t.SwipeTracker()
    assert t.consume(swipe(1000, 950), tracker)[0] == []
    # New contact with no fresh ABS positions must not reuse last value.
    assert t.consume(event(t.EV_KEY, t.BTN_TOUCH, 1)
                     + event(t.EV_KEY, t.BTN_TOUCH, 0), tracker)[0] == []


def test_axis_and_orientation_must_be_explicit_for_rotated_touch():
    tracker = t.SwipeTracker(axis="y", reverse=True)
    assert t.consume(swipe(2900, 1200, axis=t.ABS_Y), tracker)[0] == [-1]
    assert t.consume(swipe(1000, 100, axis=t.ABS_X), tracker)[0] == []
    with pytest.raises(ValueError):
        t.SwipeTracker(axis="z")
    with pytest.raises(ValueError):
        t.SwipeTracker(threshold=0)


def test_syn_dropped_refuses_partial_gesture():
    tracker = t.SwipeTracker()
    data = (event(t.EV_KEY, t.BTN_TOUCH, 1)
            + event(t.EV_ABS, t.ABS_X, 3000)
            + event(t.EV_SYN, 3, 0)
            + event(t.EV_ABS, t.ABS_X, 300)
            + event(t.EV_KEY, t.BTN_TOUCH, 0))
    assert t.consume(data, tracker)[0] == []


def test_syn_dropped_ignores_all_unsynced_events_until_syn_report():
    tracker = t.SwipeTracker()
    partial = (event(t.EV_KEY, t.BTN_TOUCH, 1)
               + event(t.EV_ABS, t.ABS_X, 3100)
               + event(t.EV_SYN, 3, 0))
    assert t.consume(partial, tracker)[0] == []
    assert tracker.desynced is True
    # The kernel says events before SYN_REPORT may be corrupted, including a
    # *complete-looking* BTN_TOUCH and swipe. It must not navigate.
    invalid = (event(t.EV_KEY, t.BTN_TOUCH, 1)
               + event(t.EV_ABS, t.ABS_X, 3100)
               + event(t.EV_ABS, t.ABS_X, 900)
               + event(t.EV_KEY, t.BTN_TOUCH, 0))
    assert t.consume(invalid, tracker)[0] == []
    assert tracker.desynced is True
    assert t.consume(event(t.EV_SYN, 0, 0), tracker)[0] == []
    assert tracker.desynced is False
    # A genuinely fresh post-resync gesture still works.
    assert t.consume(swipe(3100, 900), tracker)[0] == [1]


def test_syn_dropped_across_partial_records_stays_desynced():
    tracker = t.SwipeTracker()
    records = event(t.EV_SYN, 3, 0) + swipe(3100, 900)
    first, fragment = t.consume(records[:37], tracker)
    assert first == [] and tracker.desynced
    second, fragment = t.consume(fragment + records[37:], tracker)
    assert second == [] and fragment == b""
    # Only SYN_REPORT exits the lost-event region.
    t.consume(event(t.EV_SYN, 0, 0), tracker)
    assert t.consume(swipe(3100, 900), tracker)[0] == [1]


def test_touch_options_cannot_read_input_during_check_or_once(monkeypatch):
    from redux.display import fbui
    calls = []
    monkeypatch.setattr(fbui, "framebuffer_info",
                        lambda _: {"ok": True, "stride": 960})
    monkeypatch.setattr(t, "open_touch",
                        lambda *_args: calls.append("opened"))
    with pytest.raises(SystemExit) as failure:
        fbui.main(["--framebuffer", "/dev/fb1", "--once",
                   "--touch-device", "/dev/input/event4"])
    assert failure.value.code == 2
    assert calls == []


def test_raw_touch_read_refuses_manual_calibration_guesses():
    assert t.navigate("captures", 1) == "doctor"
    with pytest.raises(ValueError, match="unknown page"):
        t.navigate("unsupported", 1)
