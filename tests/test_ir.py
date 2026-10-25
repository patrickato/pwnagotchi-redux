"""IR: Flipper .ir parsing, library, and POWER-gated blast/replay."""
from redux.ir import (
    IRCode, IRLibrary, parse_flipper_ir, blast_plan, replay_plan, TV_POWER_CODES,
)


FLIPPER_IR = """\
Filetype: IR signals file
Version: 1
#
name: Power
type: parsed
protocol: NECext
address: 04 00 00 00
command: 08 00 00 00
#
name: Volume_up
type: raw
frequency: 38000
duty_cycle: 0.330000
data: 9000 4500 560 560 560 1690 560
#
"""


def test_parse_flipper_parsed_and_raw():
    codes = parse_flipper_ir(FLIPPER_IR)
    by = {c.name: c for c in codes}
    assert by["Power"].protocol == "NECext" and by["Power"].command == "08 00 00 00"
    assert by["Volume_up"].kind == "raw" and by["Volume_up"].frequency == 38000
    assert by["Volume_up"].raw[:2] == [9000, 4500]


def test_tv_power_codes_present():
    names = [c.name for c in TV_POWER_CODES]
    assert any("Samsung" in n for n in names) and any("LG" in n for n in names)


def test_library_add_import_remove(tmp_path):
    lib = IRLibrary(tmp_path / "ir.json")
    lib.add(IRCode("MyTV power", "NEC", "01", "02"))
    assert lib.get("MyTV power").command == "02"
    n = lib.import_flipper(FLIPPER_IR)
    assert n == 2 and len(lib.list()) == 3
    assert lib.remove("Power") and not lib.remove("nope")


# ---- gating -------------------------------------------------------------- #

def test_blast_hidden_in_field():
    p = blast_plan(lab_mode=False, confirmed=True)
    assert not p.runnable and "FIELD" in p.reason


def test_blast_needs_confirm_in_lab():
    assert not blast_plan(lab_mode=True, confirmed=False).runnable
    p = blast_plan(lab_mode=True, confirmed=True)
    assert p.runnable and p.count == len(TV_POWER_CODES)
    assert p.argv[0] == "irsend"


def test_replay_gated():
    code = IRCode("MyTV power", "NEC", "01", "02")
    assert not replay_plan(code, lab_mode=False, confirmed=True).runnable
    assert not replay_plan(code, lab_mode=True, confirmed=False).runnable
    p = replay_plan(code, lab_mode=True, confirmed=True)
    assert p.runnable and "MyTV_power" in p.argv
