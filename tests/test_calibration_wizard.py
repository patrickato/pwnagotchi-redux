"""Five-tap offline calibration wizard: synthetic measured-report fixtures only."""
import json
import stat

import pytest

from redux.display import calibration_file, calibration_wizard as wizard


def report(*, swap=False, reverse_x=False, reverse_y=False):
    taps = []
    for px, py in wizard.TARGETS:
        x, y = px / 479, py / 319
        if reverse_x:
            x = 1 - x
        if reverse_y:
            y = 1 - y
        raw_x, raw_y = round(500 + x * 3500), round(500 + y * 3500)
        if swap:
            raw_x, raw_y = raw_y, raw_x
        taps.append({"raw_x": raw_x, "raw_y": raw_y,
                     "x_percent": None, "y_percent": None})
    return {"schema": 1, "touch_sample": {
        "status": "observed", "dropped_syncs": 0,
        "raw_axis_bounds": {"x": {"min": 0, "max": 4095},
                            "y": {"min": 0, "max": 4095}},
        "taps": taps,
        "physical_touch_verified": False,
    }}


@pytest.mark.parametrize("swap", (False, True))
@pytest.mark.parametrize("rx", (False, True))
@pytest.mark.parametrize("ry", (False, True))
def test_all_eight_wiring_orientations_from_measured_order(swap, rx, ry):
    payload, fit = wizard.candidate(report(swap=swap, reverse_x=rx, reverse_y=ry))
    assert len(payload["points"]) == 5
    assert (fit.swap_axes, fit.reverse_x, fit.reverse_y) == (swap, rx, ry)
    assert fit.maximum_error_px < 1


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(schema=2),
    lambda r: r["touch_sample"].update(status="no_complete_taps"),
    lambda r: r["touch_sample"].update(dropped_syncs=1),
    lambda r: r["touch_sample"].update(dropped_syncs=True),
    lambda r: r["touch_sample"].update(taps=r["touch_sample"]["taps"][:4]),
    lambda r: r["touch_sample"]["taps"][0].update(raw_x=None),
    lambda r: r["touch_sample"]["taps"][0].update(raw_x=99999),
    lambda r: r["touch_sample"].update(raw_axis_bounds={}),
    lambda r: r["touch_sample"]["taps"].reverse(),
])
def test_invalid_or_untrustworthy_five_tap_evidence_is_refused(mutation):
    data = report()
    mutation(data)
    with pytest.raises(ValueError):
        wizard.candidate(data)


def test_candidate_is_private_and_compatible_with_existing_calibration_loader(tmp_path):
    payload, fit = wizard.candidate(report())
    name = wizard.save_candidate(str(tmp_path), payload)
    assert name == "touch-calibration.json"
    target = tmp_path / name
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    verified_candidate = calibration_file.load_calibration(str(target))
    assert verified_candidate.maximum_error_px == fit.maximum_error_px
    with pytest.raises(FileExistsError):
        wizard.save_candidate(str(tmp_path), payload)
    assert json.loads(target.read_text()) == payload


def test_output_directory_symlinks_refused(tmp_path):
    source = tmp_path / "real"
    source.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(source, target_is_directory=True)
    with pytest.raises(OSError):
        wizard.save_candidate(str(link), wizard.candidate(report())[0])


def test_report_reader_refuses_invalid_files_and_symlinks(tmp_path):
    raw = tmp_path / "report.json"
    raw.write_text(json.dumps(report()))
    assert wizard.read_report(str(raw))["schema"] == 1
    symlink = tmp_path / "link.json"
    symlink.symlink_to(raw)
    with pytest.raises(OSError):
        wizard.read_report(str(symlink))
    raw.write_bytes(b"x" * (wizard._MAX_INPUT + 1))
    with pytest.raises(ValueError):
        wizard.read_report(str(raw))


def test_cli_requires_explicit_operator_order_and_does_not_claim_hardware(tmp_path, capsys):
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report()))
    output = tmp_path / "out"
    output.mkdir()
    args = ["--report", str(source), "--output-dir", str(output)]
    with pytest.raises(SystemExit) as error:
        wizard.main(args)
    assert error.value.code == 2
    assert list(output.iterdir()) == []
    assert wizard.main(args + ["--confirm-order"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["physical_touch_verified"] is False
    assert result["hardware_acceptance"] == "pending_operator_verification"
    assert (output / result["file"]).exists()
