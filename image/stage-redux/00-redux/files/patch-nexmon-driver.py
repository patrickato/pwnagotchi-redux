#!/usr/bin/env python3
"""Adapt nexmon's monitor callback to the installed kernel's actual API."""
import argparse
from pathlib import Path
import re


def parameter_types(parameters):
    types = []
    for parameter in parameters.split(","):
        match = re.fullmatch(r"\s*struct\s+(\w+)\s*\*\s*\w+\s*", parameter)
        if not match:
            raise ValueError("unsupported monitor callback parameter declaration")
        types.append(match.group(1))
    return types


def adapt_driver(header, source):
    declaration = re.search(r"\(\s*\*\s*set_monitor_channel\s*\)\s*\(([^)]*)\)", header)
    definition = re.search(
        r"\bbrcmf_cfg80211_nexmon_set_channel\s*\(([^)]*)\)\s*\{", source
    )
    if not declaration or not definition:
        raise ValueError("monitor callback declaration or nexmon definition missing")
    expected = parameter_types(declaration.group(1))
    actual = parameter_types(definition.group(1))
    old = ["wiphy", "cfg80211_chan_def"]
    new = ["wiphy", "net_device", "cfg80211_chan_def"]
    if expected not in (old, new) or actual not in (old, new):
        raise ValueError("unsupported monitor callback API; refusing an unverified driver adjustment")
    if actual == expected:
        return source, f"monitor callback unchanged: matches installed {len(expected)}-argument kernel API"
    if actual != old or expected != new:
        raise ValueError("cannot safely downgrade a nexmon callback that may use its net_device argument")
    first, last = definition.group(1).split(",")
    replacement = (
        "brcmf_cfg80211_nexmon_set_channel(" + first.strip()
        + ", struct net_device *redux_dev, " + last.strip()
        + ") {\n\t(void)redux_dev; /* Preserve nexmon's existing PHY-wide channel control. */"
    )
    source = source[:definition.start()] + replacement + source[definition.end():]
    return source, "monitor callback adapted: installed kernel adds net_device; existing PHY-wide channel control preserved"


def adapt_sdio_id(header, source):
    old = "SDIO_DEVICE_ID_BROADCOM_CYPRESS_43752"
    new = "SDIO_DEVICE_ID_BROADCOM_43752"
    alias = f"#ifndef {old}\n#define {old} {new}\n#endif"
    if old not in source or re.search(r"^\s*#define\s+" + old + r"\b", header, re.M):
        return source, "SDIO ID unchanged: target headers provide the expected identifier"
    if not re.search(r"^\s*#define\s+" + new + r"\b", header, re.M):
        raise ValueError("target headers provide neither supported 43752 device identifier")
    if alias in source:
        return source, "SDIO ID unchanged: renamed identifier already has its compatibility alias"
    anchor = "#include <linux/mmc/sdio_ids.h>"
    if anchor not in source:
        indirect = "#include <linux/mmc/sdio_func.h>"
        if source.count(indirect) != 1:
            raise ValueError("cannot locate a unique SDIO include for compatibility alias")
        source = source.replace(indirect, indirect + "\n" + anchor, 1)
    if source.count(anchor) != 1:
        raise ValueError("cannot locate a unique SDIO IDs include for compatibility alias")
    # Same alias as the pinned maintained nexmon 6.18 driver; no guessed ID.
    return source.replace(anchor, anchor + "\n\n" + alias, 1), "SDIO ID adapted: use renamed header identifier via nexmon 6.18 compatibility alias"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("header", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sdio-header", type=Path)
    args = parser.parse_args()
    try:
        original = args.source.read_text()
        updated, reason = adapt_driver(args.header.read_text(), original)
        if updated != original:
            args.source.write_text(updated)
        reasons = [reason]
        if args.sdio_header:
            sdio_header = args.sdio_header.read_text()
            for name in ("sdio.c", "bcmsdh.c"):
                source_path = args.source.parent / name
                original = source_path.read_text()
                updated, reason = adapt_sdio_id(sdio_header, original)
                if updated != original:
                    source_path.write_text(updated)
                reasons.append(f"{name}: {reason}")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print("; ".join(reasons))


if __name__ == "__main__":
    main()
