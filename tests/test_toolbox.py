"""Toolbox catalog: curated, safe, one-command installs mapped to redux features."""
from redux.toolbox import (
    CATALOG, CATEGORIES, list_tools, get, find, install_commands, Tool,
    is_installed, probe_binary,
)


def test_catalog_is_coherent():
    names = [t.name for t in CATALOG]
    assert len(names) == len(set(names)), "duplicate tool name"
    for t in CATALOG:
        assert t.category in CATEGORIES
        assert t.summary and t.install        # every tool has a purpose + an install command


def test_get_and_find():
    assert get("hackrf").category == "tx"
    assert get("HACKRF").name == "hackrf"      # case-insensitive
    assert get("nope") is None
    assert any(t.name == "rtl-433" for t in find("ism"))
    assert all(t.category == "sdr" for t in find(category="sdr"))


def test_install_commands_are_exact():
    cmds = install_commands("gpsd")
    assert cmds and "apt-get install" in cmds[0] and "gpsd" in cmds[0]
    assert install_commands("nope") is None


def test_tx_backends_present_and_flagged_cage():
    rpitx = get("rpitx")
    assert rpitx.category == "tx" and "CAGE ONLY" in rpitx.note
    # the TFT pin constraint is surfaced where a GPIO tool is wired
    assert "25-40" in rpitx.note
    assert "25-40" in get("lirc").note


def test_companions_marked_as_desktop():
    for name in ("sdrpp", "urh"):
        t = get(name)
        assert t.category == "companion"
        assert "laptop" in t.note.lower() or "desktop" in t.summary.lower() or "pc" in t.note.lower()


def test_catalog_maps_tools_to_features():
    # the SDR decode tools point at the redux command they enable
    assert "pocsag" in get("multimon-ng").enables
    assert "wps" in get("reaver").enables
    assert "crack" in get("hashcat").enables


def test_probe_binary_handles_divergent_names():
    # when the package/tool name isn't the binary, probe points at the real binary
    assert probe_binary(get("rtl-sdr")) == "rtl_power"
    assert probe_binary(get("hcxtools")) == "hcxpcapngtool"
    assert probe_binary(get("hackrf")) == "hackrf_info"
    assert probe_binary(get("dig")) == "dig"            # name already is the binary
    # default: no override -> the name itself
    assert probe_binary(get("nmap")) == "nmap"


def test_is_installed_uses_injected_which_on_the_real_binary():
    present = {"rtl_power", "nmap"}
    which = lambda b: ("/usr/bin/" + b) if b in present else None
    assert is_installed(get("rtl-sdr"), which=which) is True     # probed via rtl_power
    assert is_installed(get("nmap"), which=which) is True
    assert is_installed(get("hcxtools"), which=which) is False   # hcxpcapngtool absent
    assert is_installed(get("masscan"), which=which) is False


def test_baked_tools_are_flagged_and_match_image_toolset():
    # a representative slice of the stock image toolset is catalogued and marked baked
    for name in ("aircrack-ng", "hashcat", "hcxtools", "nmap", "masscan", "hydra",
                 "binwalk", "sqlmap", "bluez", "kismet", "gpsd"):
        assert get(name).baked is True, f"{name} should be flagged baked"
    # add-on / GUI tools are not claimed as baked
    assert get("rpitx").baked is False
    assert get("sdrpp").baked is False
    assert get("acarsdec").baked is False


def test_enables_is_only_set_where_redux_actually_drives_the_tool():
    # honest wiring: tools redux never invokes carry no `enables` claim
    for name in ("masscan", "john", "medusa", "binwalk", "sqlmap", "nikto", "wavemon"):
        assert get(name).enables == "", f"{name} must not claim a redux feature it has none"
    # tools redux does drive point at the real command
    assert "netrecon" in get("hydra").enables
    assert "netrecon" in get("nmap").enables
