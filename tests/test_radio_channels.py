"""Hardware-free tests for all-band channel sweeping."""
from __future__ import annotations

from redux.radio.channels import BAND_24, BAND_5, all_band_channels, parse_channels


def test_all_bands_cover_24_and_5_not_just_1_6_11():
    chans = all_band_channels()
    # the whole point: not the old 2.4-only 1/6/11 list
    assert {1, 6, 11}.issubset(chans)
    assert {2, 3, 4, 5, 7, 8, 9, 10, 12, 13}.issubset(chans)  # every 2.4 channel
    assert 36 in chans and 149 in chans and 100 in chans        # 5 GHz UNII-1/2e/3
    assert len(chans) > 30


def test_band_selection():
    only24 = all_band_channels(bands=("2.4",))
    assert only24 == BAND_24 and 36 not in only24
    only5 = all_band_channels(bands=("5",))
    assert only5 == BAND_5 and 1 not in only5


def test_24_then_5_ordering():
    chans = all_band_channels()
    assert chans[:len(BAND_24)] == BAND_24      # 2.4 first
    assert chans[len(BAND_24)] == BAND_5[0]     # then 5 GHz


def test_parse_channels_override():
    assert parse_channels("1,6,11,36,149") == [1, 6, 11, 36, 149]
    assert parse_channels("1 6 11") == [1, 6, 11]          # spaces ok
    assert parse_channels("36, , 40,,44") == [36, 40, 44]  # junk/empties ignored
    assert parse_channels("6,6,6,11") == [6, 11]           # de-duped, order kept
    assert parse_channels("abc,-3,0,6") == [6]             # non-positive/garbage dropped
    assert parse_channels("") == []
