"""Offline navigation calibration bridge regression tests."""
import pytest

from redux.display.calibration import Calibration
from redux.display.calibrated_nav import swipe_from_calibration
from redux.display.touch_nav import ABS_X, ABS_Y, BTN_TOUCH, EV_ABS, EV_KEY


def candidate(*, swap=False, reverse_x=False, width=480, span=3500, error=0):
    return Calibration(swap, reverse_x, False, 100, 100 + span,
                       200, 3700, width, 320, error)


@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_qualified_swipes_are_screen_relative(swap, reverse):
    tracker = swipe_from_calibration(candidate(swap=swap, reverse_x=reverse),
                                    physically_verified=True)
    axis = ABS_Y if swap else ABS_X
    assert tracker.axis == ("y" if swap else "x")
    assert tracker.threshold == round(60 * 3500 / 479)
    start, end = (3000, 1000) if not reverse else (1000, 3000)
    tracker.feed(EV_KEY, BTN_TOUCH, 1)
    tracker.feed(EV_ABS, axis, start)
    tracker.feed(EV_ABS, axis, end)
    assert tracker.feed(EV_KEY, BTN_TOUCH, 0) == 1


def test_no_automatic_activation_from_an_unverified_fit():
    with pytest.raises(ValueError, match="physical"):
        swipe_from_calibration(candidate(), physically_verified=False)


@pytest.mark.parametrize("bad", [0, 19, 201, True, 1.5])
def test_swipe_distance_rejects_invalid_values(bad):
    with pytest.raises(ValueError):
        swipe_from_calibration(candidate(), physically_verified=True,
                               minimum_pixels=bad)


def test_rejects_nonstandard_geometry_bad_residual_and_bad_raw_span():
    for bad in [candidate(width=320), candidate(error=36), candidate(span=0),
                candidate(span=90000)]:
        with pytest.raises(ValueError):
            swipe_from_calibration(bad, physically_verified=True)
