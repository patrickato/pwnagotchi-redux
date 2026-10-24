"""Sub-GHz library: capture/catalog (RX) + gated replay (POWER-tier, TX backend)."""
from redux.sdr.subghz import (
    SubGhzSignal, SubGhzLibrary, capture_command, looks_rolling,
    detect_tx_backend, replay_plan, TX_BACKENDS,
)


# ---- capture command (RX; RTL-SDR) -------------------------------------- #

def test_capture_decode_uses_rtl433():
    argv = capture_command(433.92e6, seconds=10)
    assert argv[0] == "rtl_433" and "-f" in argv and "433.92M" in argv
    assert "-T" in argv and "10" in argv


def test_capture_raw_uses_rtl_sdr():
    argv = capture_command(315e6, seconds=5, out_path="/tmp/cap.cu8", decode=False)
    assert argv[0] == "rtl_sdr" and "/tmp/cap.cu8" in argv


# ---- rolling-code honesty ------------------------------------------------ #

def test_looks_rolling():
    assert looks_rolling({"model": "KeeLoq"}, "")
    assert looks_rolling({}, "HCS301")
    assert not looks_rolling({"model": "Generic-Remote", "code": "0x5A"}, "")


# ---- library ------------------------------------------------------------- #

def test_library_add_list_get_remove(tmp_path):
    lib = SubGhzLibrary(tmp_path / "sg.json", now=lambda: 100.0)
    s = lib.add("garage", 390e6, protocol="Generic", decoded={"code": "0x1234"})
    assert s.id == "sg001" and s.freq_hz == 390e6 and not s.rolling
    lib.add("carfob", 433.92e6, protocol="KeeLoq", decoded={"model": "KeeLoq"})
    assert len(lib.list()) == 2
    assert lib.get("garage").name == "garage"
    assert lib.get("sg002").rolling                       # keeloq flagged rolling
    assert lib.remove("sg001") and len(lib.list()) == 1
    assert not lib.remove("nope")


# ---- TX backend detection ------------------------------------------------ #

def test_detect_tx_backend():
    assert detect_tx_backend(which=lambda b: "/usr/bin/hackrf_transfer" if b == "hackrf_transfer" else None) == "hackrf"
    assert detect_tx_backend(which=lambda b: None) is None   # RTL-SDR only -> no TX


# ---- replay gating (the whole point) ------------------------------------ #

def _sig(**kw):
    base = dict(id="sg001", name="garage", freq_hz=390e6, capture_path="")
    base.update(kw)
    return SubGhzSignal(**base)


def test_replay_refused_in_field():
    p = replay_plan(_sig(), lab_mode=False, confirmed=True,
                    which=lambda b: "/x/hackrf_transfer")
    assert not p.runnable and "FIELD" in p.reason


def test_replay_needs_confirmation_in_lab():
    p = replay_plan(_sig(), lab_mode=True, confirmed=False,
                    which=lambda b: "/x/hackrf_transfer")
    assert not p.runnable and "confirm" in p.reason.lower()


def test_replay_rolling_code_refused():
    p = replay_plan(_sig(rolling=True), lab_mode=True, confirmed=True,
                    which=lambda b: "/x/hackrf_transfer")
    assert not p.runnable and "ROLLING" in p.reason


def test_replay_refused_without_tx_backend():
    # LAB + confirmed + not rolling, but only an RTL-SDR (no TX) present
    p = replay_plan(_sig(), lab_mode=True, confirmed=True, which=lambda b: None)
    assert not p.runnable and "cannot transmit" in p.reason


def test_replay_hackrf_needs_capture_file(tmp_path):
    p = replay_plan(_sig(capture_path=""), lab_mode=True, confirmed=True,
                    backend="hackrf", which=lambda b: "/x/hackrf_transfer")
    assert not p.runnable and "no capture file" in p.reason


def test_replay_runs_with_everything_present(tmp_path):
    cap = tmp_path / "c.cu8"; cap.write_bytes(b"\x00\x01")
    p = replay_plan(_sig(capture_path=str(cap)), lab_mode=True, confirmed=True,
                    backend="hackrf", which=lambda b: "/x/hackrf_transfer")
    assert p.runnable and p.backend == "hackrf"
    assert p.argv[0] == "hackrf_transfer" and str(cap) in p.argv


def test_replay_rpitx_backend(tmp_path):
    p = replay_plan(_sig(capture_path="s.ook"), lab_mode=True, confirmed=True,
                    backend="rpitx", which=lambda b: "/x/sendook")
    assert p.runnable and p.argv[0] == "sendook"
