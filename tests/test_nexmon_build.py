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
    MONITOR_CORE.replace("return err;", "ndev->priv_destructor = brcmf_cfg80211_free_netdev;\n\treturn err;"),
    MONITOR_CORE.replace("err = cfg80211_register_netdevice(ndev);",
                         "if (!err) ndev->priv_destructor = brcmf_cfg80211_free_netdev;\n"
                         "\terr = cfg80211_register_netdevice(ndev);"),
])
def test_unknown_monitor_cleanup_lifecycle_refuses_adjustment(source):
    with pytest.raises(ValueError, match="monitor"):
        compat.adapt_monitor_cleanup(source)


def compile_lifecycle_fixture(tmp_path, source):
    """Execute patched C with a synthetic, reference-counted netdev owner.

    This deliberately models kernel ownership; it does not compile a driver
    or claim to exercise a real kernel's unregister_netdevice implementation.
    """
    import ctypes
    prefix = r'''
struct net_device {
    void (*priv_destructor)(struct net_device *);
    void *netdev_ops;
    int references;
    int registered;
    int vif_live;
};
struct brcmf_if { struct net_device *ndev; };
static int brcmf_netdev_ops_mon;
static int register_result, releases, duplicate_releases;
#define bphy_err(...) ((void)0)
static int cfg80211_register_netdevice(struct net_device *ndev) {
    if (!register_result) ndev->registered = 1;
    return register_result;
}
static void brcmf_cfg80211_free_netdev(struct net_device *ndev) {
    if (!ndev->vif_live) { duplicate_releases++; return; }
    ndev->vif_live = 0;
    releases++;
}
static void release_netdev_reference(struct net_device *ndev) {
    if (ndev->references && --ndev->references == 0 && ndev->priv_destructor)
        ndev->priv_destructor(ndev);
}
'''
    # Remove the unrelated function to keep -Wall -Werror useful.
    source = source.split("static int unrelated", 1)[0]
    suffix = r'''
int run_lifecycle(int failure, int held_reference) {
    int cycle;
    register_result = failure;
    releases = duplicate_releases = 0;
    for (cycle = 0; cycle < 32; cycle++) {
        struct net_device ndev = {0};
        struct brcmf_if ifp = {&ndev};
        ndev.references = held_reference ? 2 : 1;
        ndev.vif_live = 1;
        if (brcmf_net_mon_attach(&ifp) != failure) return 1;
        if (failure) {
            if (ndev.registered || ndev.priv_destructor) return 2;
            /* brcmf_mon_add_vif owns failure cleanup. */
            brcmf_cfg80211_free_netdev(&ndev);
        } else {
            if (!ndev.registered) return 3;
            ndev.registered = 0;
            release_netdev_reference(&ndev);
            if (held_reference) {
                if (!ndev.vif_live || releases != cycle) return 4;
                release_netdev_reference(&ndev);
            }
        }
        /* Repeated release cannot rerun a kernel-owned destructor. */
        release_netdev_reference(&ndev);
        if (ndev.vif_live) return 101; /* Demonstrate the original stale VIF. */
        if (releases != cycle + 1 || duplicate_releases) return 5;
    }
    return 0;
}
'''
    lib = compile_host_fixture(tmp_path, prefix + source + suffix)
    lib.run_lifecycle.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.run_lifecycle.restype = ctypes.c_int
    return lib


def compile_host_fixture(tmp_path, source):
    """Execute adapted callback logic with synthetic firmware/kernel stubs."""
    import ctypes
    import os
    import shlex
    import shutil
    import subprocess
    import sys
    if sys.platform != "linux":
        pytest.skip("native ELF callback fixture requires Linux")
    compiler = shlex.split(os.environ.get("CC", "cc"))
    assert compiler and shutil.which(compiler[0]), "Callback tests require a host C compiler (CC)"
    linker = shutil.which("ld")
    assert linker, "Callback tests require the host ELF linker (ld)"
    cfile = tmp_path / "callback.c"
    obj = tmp_path / "callback.o"
    library = tmp_path / "callback.so"
    cfile.write_text(source)
    result = subprocess.run([*compiler, "-Wall", "-Werror", "-fPIC", "-c",
                             str(cfile), "-o", str(obj)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([linker, "-shared", "-z", "noexecstack", "-o",
                             str(library), str(obj)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return ctypes.CDLL(str(library))


@pytest.mark.parametrize("failure,held_reference", [(0, 0), (0, 1), (-52, 0), (-52, 1)])
def test_compiled_monitor_lifetime_releases_vif_once(tmp_path, failure, held_reference):
    updated, _ = compat.adapt_monitor_cleanup(MONITOR_CORE)
    lib = compile_lifecycle_fixture(tmp_path, updated)
    assert lib.run_lifecycle(failure, held_reference) == 0


def test_compiled_lifecycle_detects_original_stale_vif(tmp_path):
    lib = compile_lifecycle_fixture(tmp_path, MONITOR_CORE)
    assert lib.run_lifecycle(0, 0) == 101
    assert lib.run_lifecycle(-52, 0) == 0


@pytest.mark.parametrize("header", [OLD_HEADER, NEW_HEADER])
@pytest.mark.parametrize("patched", [False, True])
def test_compiled_channel_callback_preserves_firmware_result(tmp_path, header, patched):
    """An unpatched control demonstrates the silent-success firmware defect.

    Host stubs execute the adapted C callback for both API signatures; this
    does not exercise a radio, firmware or real kernel callback dispatch.
    """
    import ctypes
    source, _ = compat.adapt_driver(header, CHANNEL_SOURCE)
    if patched:
        source, _ = compat.propagate_monitor_error(source)
    source = source[:source.index("static int unrelated")]
    prefix = r'''
typedef int s32;
typedef unsigned short u16;
struct wiphy { int unused; };
struct cfg80211_chan_def { int unused; };
struct net_device { int unused; };
struct brcmf_if { int unused; };
struct brcmf_cfg80211_info { int d11inf; };
static struct brcmf_cfg80211_info stub_cfg;
static struct brcmf_if stub_ifp;
static int firmware_result, calls;
#define wiphy_to_cfg(w) (&stub_cfg)
#define cfg_to_ndev(c) ((void)(c), &stub_ifp)
#define netdev_priv(n) (n)
#define chandef_to_chanspec(d, c) ((u16) 0x1234)
#define brcmf_err(...) ((void)0)
static int brcmf_fil_iovar_int_set(struct brcmf_if *i, const char *name, u16 chanspec)
{
    (void)i;
    if (name[0] != 'c' || chanspec != 0x1234) return -999;
    calls++;
    return firmware_result;
}
'''
    args = "0, 0, 0" if header == NEW_HEADER else "0, 0"
    suffix = """
int run_channel(int firmware_error) {
    int result;
    firmware_result = firmware_error;
    calls = 0;
    result = brcmf_cfg80211_nexmon_set_channel(""" + args + """);
    return calls == 1 ? result : -998;
}
"""
    lib = compile_host_fixture(tmp_path, prefix + source + suffix)
    lib.run_channel.argtypes = [ctypes.c_int]
    lib.run_channel.restype = ctypes.c_int
    for error in (0, -5, -16, -95):
        assert lib.run_channel(error) == (error if patched else 0)
