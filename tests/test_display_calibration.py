"""Synthetic five-point touchscreen orientation tests; no hardware claims."""
from redux.display.calibration import TouchPoint, evaluate


def measured(*, swap=False, reverse_x=False, reverse_y=False):
    points = []
    for px, py in [(0, 0), (479, 0), (0, 319), (479, 319), (239.5, 159.5)]:
        x, y = px / 479, py / 319
        if reverse_x:
            x = 1 - x
        if reverse_y:
            y = 1 - y
        raw_x, raw_y = 500 + x * 3500, 300 + y * 3500
        if swap:
            raw_x, raw_y = raw_y, raw_x
        points.append(TouchPoint(raw_x, raw_y, px, py))
    return points


def test_all_eight_orientations():
    for swap in (False, True):
        for reverse_x in (False, True):
            for reverse_y in (False, True):
                fit = evaluate(measured(swap=swap, reverse_x=reverse_x,
                                        reverse_y=reverse_y))
                assert fit is not None
                assert (fit.swap_axes, fit.reverse_x, fit.reverse_y) == (
                    swap, reverse_x, reverse_y)
                assert fit.maximum_error_px == 0


def test_rejects_incomplete_degenerate_inconsistent_and_nonfinite():
    points = measured()
    assert evaluate(points[:4]) is None
    assert evaluate([TouchPoint(1, 1, p.pixel_x, p.pixel_y)
                     for p in points]) is None
    corrupt = points.copy()
    corrupt[-1] = TouchPoint(800, 3800, 239.5, 159.5)
    assert evaluate(corrupt) is None
    corrupt[-1] = TouchPoint(float('nan'), 1, 239.5, 159.5)
    assert evaluate(corrupt) is None
    assert evaluate(points, tolerance_px=float('nan')) is None


def test_candidate_does_not_claim_hardware_verified():
    fit = evaluate(measured())
    assert fit.project(-100, 500)[0] < 0
    assert not hasattr(fit, "hardware_verified")


def test_invalid_types_fail_closed_instead_of_crashing():
    points = measured()
    assert evaluate(points, width=True) is None
    assert evaluate(points, tolerance_px="12") is None
    invalid = points.copy()
    invalid[0] = TouchPoint(None, 300, 0, 0)
    assert evaluate(invalid) is None
    invalid[0] = TouchPoint(True, 300, 0, 0)
    assert evaluate(invalid) is None


def test_five_targets_must_have_five_distinct_samples():
    points = measured()
    # Tiny panel target windows overlap, while raw points can be perfectly
    # separated; candidate must still reject insufficient labeled evidence.
    assert evaluate(points[:4] + [points[0]], width=2, height=2) is None


def test_absurdly_large_numeric_values_fail_closed_without_overflow():
    points = measured()
    giant = 10 ** 1000
    assert evaluate(points, width=giant) is None
    assert evaluate(points, height=giant) is None
    assert evaluate(points, tolerance_px=giant) is None
    corrupted = points.copy()
    corrupted[0] = TouchPoint(giant, 200, 0, 0)
    assert evaluate(corrupted) is None
    corrupted[0] = TouchPoint(100, giant, 0, 0)
    assert evaluate(corrupted) is None
