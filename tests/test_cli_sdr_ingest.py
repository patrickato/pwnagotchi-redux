"""`redux sdr ingest` makes SDR captures reach the sighting store."""
import io
import contextlib

from redux.cli import main
from redux.geo.db import SightingStore


def _run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = main(argv)
    return rc, out.getvalue()


def test_rtl433_ingest_stores_sightings(tmp_path):
    src = tmp_path / "ism.jsonl"
    src.write_text(
        '{"model":"Acurite-Tower","id":1234,"temperature_C":21.5}\n'
        '\n'                                            # blank line ignored
        'not json\n'                                    # malformed line ignored
        '{"model":"TPMS","id":99}\n')
    db = tmp_path / "s.db"
    rc, out = _run(["sdr", "ingest", str(src), "--rtl433",
                    "--lat", "42.5", "--lon", "-83.1", "--db", str(db)])
    assert rc == 0 and "ingested 2 ISM" in out
    assert SightingStore(str(db)).stats()["count"] == 2


def test_adsb_ingest_uses_aircraft_position(tmp_path):
    src = tmp_path / "adsb.jsonl"
    src.write_text(
        '{"hex":"a1b2c3","flight":"UAL123","lat":42.1,"lon":-83.2}\n'
        '{"no_icao":true}\n')                           # dropped: no ICAO
    db = tmp_path / "s.db"
    rc, out = _run(["sdr", "ingest", str(src), "--adsb", "--db", str(db)])
    assert rc == 0 and "ingested 1 ADS-B" in out
    assert SightingStore(str(db)).stats()["count"] == 1
