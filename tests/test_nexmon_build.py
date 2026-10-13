"""No-hardware compatibility checks for observed cfg80211 callback APIs."""
import importlib.util
from pathlib import Path

import pytest


def test_multi_kernel_build_cleans_stale_objects_and_checks_vermagic(tmp_path):
    import os
    import subprocess
    import sys
    if sys.platform == "win32":
        pytest.skip("Linux build scripts")
    helper = path.with_name("target-kernel.sh")
    driver = tmp_path / "driver"
    driver.mkdir()
    # Fixture models the observed Kbuild cache: without clean, the next kernel
    # inherits the first kernel's module metadata.
    script = '''
set -eu
source "$1"
driver=$2
make() {
    case ${@: -1} in
        clean) rm -f "$driver/kernel" ;;
        modules) test -f "$driver/kernel" || basename "${2%/build}" > "$driver/kernel" ;;
    esac
}
modinfo() { printf '%s SMP aarch64\n' "$(cat "$driver/kernel")"; }
nexmon_build_module 6.12.109+rpt-rpi-2712 /modules/6.12.109+rpt-rpi-2712 "$driver"
nexmon_build_module 6.12.109+rpt-rpi-v8 /modules/6.12.109+rpt-rpi-v8 "$driver"
test "$(cat "$driver/kernel")" = 6.12.109+rpt-rpi-v8
modinfo() { echo '6.12.109+rpt-rpi-2712 SMP aarch64'; }
if nexmon_build_module 6.12.109+rpt-rpi-v8 /modules/6.12.109+rpt-rpi-v8 "$driver"; then exit 9; fi
'''
    result = subprocess.run(["bash","-c",script,"bash",str(helper),str(driver)],text=True,capture_output=True,timeout=20,env=os.environ)
    assert result.returncode == 0, result.stderr
    assert "Driver kernel mismatch" in result.stderr

path = Path(__file__).resolve().parents[1] / "image/stage-redux/00-redux/files/patch-nexmon-driver.py"
spec = importlib.util.spec_from_file_location("nexmon_compat", path)
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)

OLD_HEADER = "int (*set_monitor_channel)(struct wiphy *wiphy, struct cfg80211_chan_def *chandef);"
NEW_HEADER = """int (*set_monitor_channel)(struct wiphy *wiphy,
                    struct net_device *dev,
                    struct cfg80211_chan_def *chandef);"""
SOURCE = """static s32
brcmf_cfg80211_nexmon_set_channel(struct wiphy *wiphy,struct cfg80211_chan_def *chandef) {
    return 0;
}
.set_monitor_channel = brcmf_cfg80211_nexmon_set_channel;
"""


def test_old_kernel_api_preserves_source():
    updated, reason = compat.adapt_driver(OLD_HEADER, SOURCE)
    assert updated == SOURCE
    assert "unchanged" in reason


def test_new_kernel_api_adds_argument_and_preserves_callback_body():
    updated, reason = compat.adapt_driver(NEW_HEADER, SOURCE)
    assert "struct net_device *redux_dev" in updated
    assert "(void)redux_dev" in updated
    assert "return 0;" in updated
    assert ".set_monitor_channel = brcmf_cfg80211_nexmon_set_channel;" in updated
    assert "PHY-wide channel control preserved" in reason


def test_adapter_is_idempotent():
    updated, _ = compat.adapt_driver(NEW_HEADER, SOURCE)
    repeated, reason = compat.adapt_driver(NEW_HEADER, updated)
    assert repeated == updated
    assert "unchanged" in reason


CHANNEL_SOURCE = '''static s32
brcmf_cfg80211_nexmon_set_channel(struct wiphy *wiphy,struct cfg80211_chan_def *chandef) {
    struct brcmf_cfg80211_info *cfg = wiphy_to_cfg(wiphy);
    struct brcmf_if *ifp = netdev_priv(cfg_to_ndev(cfg));
    s32 err = 0;
    u16 chanspec;
    chanspec = chandef_to_chanspec(&cfg->d11inf, chandef);
    err = brcmf_fil_iovar_int_set(ifp, "chanspec", chanspec);
    if (err < 0) {
        brcmf_err("Set Channel failed: chspec=%d, %d\\n", chanspec, err);
    }
    return 0;
}
static int unrelated(void) {
    return 0;
}
'''


@pytest.mark.parametrize("header", [OLD_HEADER, NEW_HEADER])
def test_channel_failure_is_propagated_after_kernel_api_adaptation(header):
    source, _ = compat.adapt_driver(header, CHANNEL_SOURCE)
    updated, reason = compat.propagate_monitor_error(source)
    assert 'err = brcmf_fil_iovar_int_set(ifp, "chanspec", chanspec);' in updated
    assert "return err;\n}" in updated
    assert "static int unrelated(void) {\n    return 0;\n}" in updated
    assert "propagate firmware error" in reason
    assert compat.propagate_monitor_error(updated)[0] == updated


@pytest.mark.parametrize("source", [
    SOURCE,
    CHANNEL_SOURCE.replace('"chanspec"', '"different_iovar"'),
    CHANNEL_SOURCE.replace("return 0;", "return -1;", 1),
    CHANNEL_SOURCE.replace("if (err < 0) {", "if (err < 0) { return 0;"),
    CHANNEL_SOURCE + CHANNEL_SOURCE,
])
def test_unknown_channel_callback_body_refuses_adjustment(source):
    with pytest.raises(ValueError, match="channel"):
        compat.propagate_monitor_error(source)


def test_existing_guarded_channel_error_propagation_is_preserved():
    source = CHANNEL_SOURCE.replace(
        "    chanspec =", "    /* Older code used to return 0 unconditionally. */\n"
        "    if (!chandef->chan) { return -EINVAL; }\n    chanspec =", 1
    ).replace("    return 0;", "    return err;", 1)
    updated, reason = compat.propagate_monitor_error(source)
    assert updated == source
    assert "already propagated" in reason


def test_build_entrypoint_applies_channel_error_fix(tmp_path):
    import subprocess
    import sys
    header = tmp_path / "cfg80211.h"
    source = tmp_path / "cfg80211.c"
    core = tmp_path / "core.c"
    header.write_text(NEW_HEADER)
    source.write_text(CHANNEL_SOURCE)
    core.write_text(MONITOR_CORE)
    result = subprocess.run([sys.executable, str(path), str(header), str(source),
                             "--core-source", str(core)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert "struct net_device *redux_dev" in source.read_text()
    assert "return err;\n}" in source.read_text()
    assert "propagate firmware error" in result.stdout
    assert "if (!err)\n\t\tndev->priv_destructor = brcmf_cfg80211_free_netdev;" in core.read_text()
    assert "--core-source \"$driver/core.c\"" in path.with_name("install-nexmon.sh").read_text()


@pytest.mark.parametrize("header,source", [
    (NEW_HEADER.replace("struct net_device *dev", "unsigned int link_id"), SOURCE),
    (NEW_HEADER, "/* no nexmon callback definition */"),
    (OLD_HEADER, compat.adapt_driver(NEW_HEADER, SOURCE)[0]),
])
def test_unknown_or_unsafe_changes_fail_closed(header, source):
    with pytest.raises(ValueError):
        compat.adapt_driver(header, source)


SDIO_SOURCE = "#include <linux/mmc/sdio_ids.h>\ncase SDIO_DEVICE_ID_BROADCOM_CYPRESS_43752: break;"


def test_original_sdio_id_needs_no_alias():
    updated, _ = compat.adapt_sdio_id("#define SDIO_DEVICE_ID_BROADCOM_CYPRESS_43752 0xaae8", SDIO_SOURCE)
    assert updated == SDIO_SOURCE


def test_renamed_sdio_id_uses_existing_kernel_value_and_is_idempotent():
    header = "#define SDIO_DEVICE_ID_BROADCOM_43752 0xaae8"
    updated, reason = compat.adapt_sdio_id(header, SDIO_SOURCE)
    assert "#define SDIO_DEVICE_ID_BROADCOM_CYPRESS_43752 SDIO_DEVICE_ID_BROADCOM_43752" in updated
    assert "0xaae8" not in updated  # Use the real target header, not a guessed constant.
    assert "adapted" in reason
    assert compat.adapt_sdio_id(header, updated)[0] == updated


def test_bcmsdh_indirect_include_gets_explicit_ids_header():
    source = SDIO_SOURCE.replace("sdio_ids.h", "sdio_func.h")
    updated, _ = compat.adapt_sdio_id("#define SDIO_DEVICE_ID_BROADCOM_43752 0xaae8", source)
    assert "#include <linux/mmc/sdio_func.h>\n#include <linux/mmc/sdio_ids.h>" in updated
    assert updated.index("sdio_ids.h") < updated.index("#ifndef")


@pytest.mark.parametrize("header,source", [
    ("/* unsupported identifiers */", SDIO_SOURCE),
    ("#define SDIO_DEVICE_ID_BROADCOM_43752 0xaae8", SDIO_SOURCE.replace("#include <linux/mmc/sdio_ids.h>", "")),
])
def test_unknown_sdio_mapping_or_include_fails_closed(header, source):
    with pytest.raises(ValueError):
        compat.adapt_sdio_id(header, source)


# Synthetic lifecycle fixture for the pinned 6.12 and 6.18 monitor attach paths.
MONITOR_CORE = '''int brcmf_net_mon_attach(struct brcmf_if *ifp)
{
\tstruct net_device *ndev = ifp->ndev;
\tint err;
\tndev->netdev_ops = &brcmf_netdev_ops_mon;
\terr = cfg80211_register_netdevice(ndev);
\tif (err)
\t\tbphy_err(drvr, "Failed to register %s device\\n", ndev->name);
\treturn err;
}
static int unrelated(void) {
    return 0;
}
'''


def test_monitor_cleanup_only_installed_after_successful_registration():
    updated, reason = compat.adapt_monitor_cleanup(MONITOR_CORE)
    assert "if (!err)\n\t\tndev->priv_destructor = brcmf_cfg80211_free_netdev;" in updated
    assert updated.index("cfg80211_register_netdevice") < updated.index("priv_destructor")
    assert updated.endswith("static int unrelated(void) {\n    return 0;\n}\n")
    assert "release cfg80211 VIF" in reason
    assert compat.adapt_monitor_cleanup(updated)[0] == updated


@pytest.mark.parametrize("source", [
    "/* callback missing */",
    MONITOR_CORE + MONITOR_CORE,
    MONITOR_CORE.replace("cfg80211_register_netdevice", "register_netdev"),
    MONITOR_CORE.replace("return err;", "return 0;"),
    MONITOR_CORE.replace("return err;", "ndev->priv_destructor = another_destructor;\n\treturn err;"),
])
def test_unknown_monitor_cleanup_lifecycle_refuses_adjustment(source):
    with pytest.raises(ValueError, match="monitor"):
        compat.adapt_monitor_cleanup(source)
