"""Waterfall: rtl_power sweeps -> time×freq grid -> ASCII + PGM (no radio)."""
from redux.sdr.waterfall import Waterfall, parse_rtl_power_rows


def _sweeps():
    # three sweeps over 8 bins; a signal appears in the middle over time
    lines = []
    for t, (idx, strong) in enumerate([(None, None), (4, -20.0), (4, -10.0)]):
        vals = [-60.0] * 8
        if idx is not None:
            vals[idx] = strong
        db = ",".join(str(v) for v in vals)
        lines.append(f"2026-01-01, 12:00:0{t}, 100000000, 100175000, 25000, 50, {db}")
    return lines


def test_parse_groups_by_timestamp():
    rows = parse_rtl_power_rows(_sweeps())
    assert len(rows) == 3                       # one row per timestamp
    assert len(rows[0].db) == 8
    assert rows[0].freqs_hz[0] == 100000000


def test_waterfall_shape_and_range():
    wf = Waterfall.from_rtl_power(_sweeps())
    assert wf.shape == (3, 8)
    assert wf.db_min == -60.0 and wf.db_max == -10.0


def test_ascii_render_shows_growing_signal():
    wf = Waterfall.from_rtl_power(_sweeps())
    art = wf.to_ascii(width=8)
    body = art.splitlines()[1:]                 # drop the freq header line
    assert len(body) == 3
    # the floor-only first row is the faintest; later rows have a strong cell
    assert body[0].strip(" ") == ""             # all floor -> spaces
    assert body[2].strip(" ") != ""             # strong signal -> a non-space glyph
    # the strongest row should contain the densest ramp char
    assert "@" in body[2]


def test_ascii_empty():
    assert "empty" in Waterfall.from_rtl_power([]).to_ascii()


def test_pgm_is_valid_p5():
    wf = Waterfall.from_rtl_power(_sweeps())
    pgm = wf.to_pgm(width=8)
    assert pgm.startswith(b"P5\n8 3\n255\n")
    # header + 8*3 pixel bytes
    body = pgm.split(b"255\n", 1)[1]
    assert len(body) == 8 * 3
    # the strongest cell maps to the brightest byte (255)
    assert max(body) == 255


def test_resample_preserves_strong_narrow_signal():
    # 100 bins, one strong spike; downsampled to 10 cols -> spike survives (max, not mean)
    vals = [-60.0] * 100
    vals[50] = -5.0
    db = ",".join(str(v) for v in vals)
    row = f"d, t, 100000000, 102475000, 25000, 50, {db}"
    wf = Waterfall.from_rtl_power([row])
    art = wf.to_ascii(width=10)
    assert "@" in art                           # the spike isn't averaged away
