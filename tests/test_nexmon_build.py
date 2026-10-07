"""No-hardware compatibility checks for observed cfg80211 callback APIs."""
import importlib.util
from pathlib import Path

import pytest

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
