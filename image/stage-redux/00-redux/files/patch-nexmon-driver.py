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


def propagate_monitor_error(source):
    """Do not report a successful channel change when firmware rejected it.

    The pinned Nexmon callback logs its firmware error but returns zero.
    Restrict this adjustment to that known callback and reject unfamiliar
    bodies rather than guessing which return value is safe to substitute.
    """
    pattern = r"\bbrcmf_cfg80211_nexmon_set_channel\s*\([^)]*\)\s*\{(.*?)^\}"
    callbacks = list(re.finditer(pattern, source, re.S | re.M))
    if len(callbacks) != 1:
        raise ValueError("cannot locate a unique nexmon channel callback body")
    callback = callbacks[0]
    body = callback.group(1)
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
    if (len(re.findall(r"\bs32\s+err\s*=\s*0\s*;", code)) != 1
            or len(re.findall(
                r'\berr\s*=\s*brcmf_fil_iovar_int_set\(ifp,\s*"chanspec",\s*chanspec\)\s*;',
                code)) != 1):
        raise ValueError("unsupported nexmon channel error handling")
    terminal = re.search(r"\breturn\s+(0|err)\s*;\s*$", body)
    if terminal is None:
        raise ValueError("unsupported nexmon channel callback return")
    if terminal.group(1) == "err":
        # The pinned 6.18 callback already propagates errors and has an
        # early unsupported-band rejection. Preserve that implementation.
        returns = re.findall(r"\breturn\s+([^;]+);", code)
        if any(value.strip() not in ("err", "-EINVAL") for value in returns):
            raise ValueError("unsupported nexmon channel error handling")
        return source, "channel callback unchanged: firmware errors already propagated"
    if len(re.findall(r"\breturn\b", code)) != 1:
        raise ValueError("unsupported nexmon channel error handling")
    updated = body[:terminal.start(1)] + "err" + body[terminal.end(1):]
    source = source[:callback.start(1)] + updated + source[callback.end(1):]
    return source, "channel callback corrected: propagate firmware error instead of reporting success"


def adapt_monitor_cleanup(source):
    """Release the monitor's cfg80211 VIF when its netdev is destroyed.

    Match the normal brcmf_net_attach lifecycle: install the destructor
    only after registration succeeds, so failed registration still uses
    brcmf_mon_add_vif's existing failure cleanup.
    """
    pattern = r"\bbrcmf_net_mon_attach\s*\(struct brcmf_if \*ifp\)\s*\{(.*?)^\}"
    matches = list(re.finditer(pattern, source, re.S | re.M))
    if len(matches) != 1:
        raise ValueError("cannot locate a unique nexmon monitor attach body")
    match = matches[0]
    body = match.group(1)
    code = re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S)
    expected = "ndev->priv_destructor = brcmf_cfg80211_free_netdev;"
    if "priv_destructor" in code:
        if expected not in code:
            raise ValueError("unsupported nexmon monitor destructor")
        return source, "monitor cleanup unchanged: cfg80211 destructor already installed"
    if (code.count("ndev->netdev_ops = &brcmf_netdev_ops_mon;") != 1
            or code.count("err = cfg80211_register_netdevice(ndev);") != 1
            or len(re.findall(r"\breturn\b", code)) != 1):
        raise ValueError("unsupported nexmon monitor registration lifecycle")
    terminal = re.search(r"(?m)^([ \t]*)return err;\s*$", body)
    if terminal is None:
        raise ValueError("unsupported nexmon monitor attach return")
    indent = terminal.group(1)
    addition = (indent + "if (!err)\n" + indent + "\t" + expected + "\n\n")
    updated = body[:terminal.start()] + addition + body[terminal.start():]
    return (source[:match.start(1)] + updated + source[match.end(1):],
            "monitor cleanup corrected: release cfg80211 VIF after successful netdev registration")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("header", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sdio-header", type=Path)
    parser.add_argument("--core-source", type=Path)
    args = parser.parse_args()
    try:
        original = args.source.read_text()
        updated, reason = adapt_driver(args.header.read_text(), original)
        updated, channel_reason = propagate_monitor_error(updated)
        if updated != original:
            args.source.write_text(updated)
        reasons = [reason, channel_reason]
        if args.core_source:
            original = args.core_source.read_text()
            updated, reason = adapt_monitor_cleanup(original)
            if updated != original:
                args.core_source.write_text(updated)
            reasons.append(reason)
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
