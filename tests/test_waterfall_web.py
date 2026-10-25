"""Waterfall tier-2 render (SVG heatmap) + the rtl_power producer (injected runner)."""
from redux.sdr.waterfall import Waterfall, _heat, waterfall_from_scan


def _sweeps():
    lines = []
    for t, (idx, strong) in enumerate([(None, None), (4, -20.0), (4, -10.0)]):
        vals = [-60.0] * 8
        if idx is not None:
            vals[idx] = strong
        db = ",".join(str(v) for v in vals)
        lines.append(f"d, 12:00:0{t}, 100000000, 100175000, 25000, 50, {db}")
    return lines


def test_heat_ramp_endpoints():
    assert _heat(0.0) == "#0000ff"      # blue (floor)
    assert _heat(1.0) == "#ff0000"      # red (strong)
    assert _heat(0.5).startswith("#")   # something in between


def test_to_svg_has_rects_for_signal():
    wf = Waterfall.from_rtl_power(_sweeps())
    svg = wf.to_svg(width=8)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "<rect" in svg                # the strong cell(s) render as rects
    assert "#0b0e13" in svg              # dark background


def test_to_svg_empty():
    svg = Waterfall.from_rtl_power([]).to_svg()
    assert "<svg" in svg and "no sweeps" in svg


def test_producer_builds_waterfall_from_injected_runner(tmp_path):
    out = tmp_path / "wf.csv"
    def fake_runner(argv, **kw):
        # emulate rtl_power writing a CSV, then return
        out.write_text("\n".join(_sweeps()) + "\n")
        class R: returncode = 0
        return R()
    wf = waterfall_from_scan("100M:100.2M:25k", seconds=1, out_path=str(out),
                             runner=fake_runner)
    assert wf.shape[0] == 3              # three sweeps parsed
    assert wf.shape[1] == 8


def test_producer_safe_when_runner_fails(tmp_path):
    def boom(argv, **kw):
        raise OSError("no rtl_power")
    wf = waterfall_from_scan(out_path=str(tmp_path / "x.csv"), runner=boom)
    assert wf.shape == (0, 0)
