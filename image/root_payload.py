"""Read-only assembled-root contract for the Redux pi-gen export boundary.

Checks files actually staged into the mounted rootfs, never the repository.
This cannot prove physical boot, firmware correctness, radio or display.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import os
from pathlib import Path
import stat
import tomllib
try:
    from image import unit_readiness
except ModuleNotFoundError:  # direct image/root_payload.py invocation
    import unit_readiness


_MUST_EXIST = (
    "usr/local/bin/redux-display-diag",
    "usr/local/bin/redux-kiosk",
    "opt/redux/redux/display/acceptance.py",
    "opt/redux/redux/display/kiosk.py",
    "opt/redux/redux/display/local_http.py",
    "opt/redux/redux/packs/locations.py",
    "opt/redux/redux/packs/control.py",
    "opt/redux/redux/packs/manager.py",
    "opt/redux/redux/packs/manifest.py",
    "opt/redux/redux/packs/status.py",
    "opt/redux/redux/packs/registry.py",
    "opt/redux/redux/packs/runtime.py",
    "opt/redux/redux/packs/native_observer.py",
    "usr/share/redux/packs/resource-summary/pack.toml",
    "opt/redux/redux/packs/__init__.py",
    "usr/share/redux/packs/field-diagnostics/pack.toml",
    "opt/redux/redux/core/boot.py",
    "opt/redux/redux/core/live_runtime.py",
    "opt/redux/redux/core/runtime_identity.py",
    "opt/redux/redux/core/telemetry.py",
    "opt/redux/redux/core/governor.py",
    "opt/redux/redux/core/doctor.py",
    "opt/redux/redux/core/live_health.py",
    "opt/redux/redux/core/processing_evidence.py",
    "opt/redux/redux/display/ui_state.py",
    "opt/redux/redux/web/status_page.py",
    "opt/redux/redux/radio/readiness.py",
    "opt/redux/redux/radio/live_lease.py",
    "opt/redux/redux/radio/local_evidence.py",
    "opt/redux/redux/radio/display_inventory.py",
    "opt/redux/redux/radio/frame_headers.py",
    "opt/redux/redux/radio/passive_qualification.py",
    "opt/redux/redux/radio/awus036acm.py",
    "opt/redux/redux/acceptance.py",
    "opt/redux/redux/connectivity/pan_preflight.py",
    "opt/redux/redux/radio/split_integration.py",
    "opt/redux/redux/radio/dual_radio.py",
    "opt/redux/redux/connectivity/bluez_pan.py",
    "opt/redux/redux/connectivity/bluez_executor.py",
    "opt/redux/redux/connectivity/bluez_bus.py",
    "opt/redux/redux/connectivity/phone.py",
    "opt/redux/redux/core/regulatory.py",
    "opt/redux/redux/crack/ingest.py",
    "opt/redux/redux/storage_mount.py",
    "opt/redux/redux/boot_readiness.py",
    "usr/local/bin/redux",
    "usr/local/bin/redux-boot-check",
    "usr/local/bin/redux-fbui",
    "opt/redux/redux/display/fbui.py",
    "opt/redux/redux/display/touch_nav.py",
    "opt/redux/redux/display/calibration.py",
    "opt/redux/redux/display/calibration_file.py",
    "opt/redux/redux/display/calibrated_nav.py",
    "opt/redux/redux/display/gesture_nav.py",
    "opt/redux/redux/display/capture_panel.py",
    "opt/redux/redux/display/radio_panel.py",
    "opt/redux/redux/display/acceptance_snapshot.py",
    "opt/redux/redux/display/telemetry_history.py",
    "opt/redux/redux/display/trend_chart.py",
    "etc/systemd/system/redux-fbui.service",
    "usr/share/redux/fbui.example.env",
    "usr/local/bin/redux-live-diag",
    "usr/share/redux/sources.sh",
    "usr/share/redux/redux-revision",
    "usr/share/redux/packages.tsv",
    "etc/systemd/system/redux-capture-ingest.service",
)
_UNITS = {
    "redux.service": ("multi-user.target.wants", "Type=notify", "RequiresMountsFor=/captures",
                      "ReadWritePaths=/captures", "Environment=REDUX_STATE_DIR=/captures/boot"),
    "redux-regdom.service": ("multi-user.target.wants", "Type=oneshot",
                              "ConditionPathExists=/etc/redux/regulatory-country",
                              "Before=redux-live.service",
                              "CapabilityBoundingSet=CAP_NET_ADMIN"),
    "redux-live.service": ("multi-user.target.wants", "Type=simple",
                           "Wants=redux-regdom.service captures.mount",
                           "After=local-fs.target redux.service redux-regdom.service captures.mount",
                           # Soft mount dependency, NOT RequiresMountsFor: the diagnostics
                           # supervisor must still start (and serve a storage-paused Doctor)
                           # when REDUXCAP is absent, rather than fail its mount dependency.
                           "ReadWritePaths=/captures",
                           "Environment=PYTHONPATH=/opt/redux",
                           "Environment=HOME=/captures/live", "ProtectHome=true"),
    "redux-capture-ingest.timer": ("timers.target.wants", "Unit=redux-capture-ingest.service"),
}

# These are the effective installed policies, including image overlay staging.
_UNITS['redux.service'] += ('After=local-fs.target', 'User=redux', 'Group=redux',
    'NoNewPrivileges=true', 'ProtectSystem=strict', 'ProtectHome=true',
    'CapabilityBoundingSet=', 'Restart=on-failure', 'RestartSec=5',
    'ExecStartPre=+/usr/bin/install -d -o redux -g redux -m 0700 /captures/boot',
    'ExecStart=/usr/bin/python3 -m redux.core.boot')
_UNITS['redux-live.service'] += ('User=root', 'Group=root', 'NoNewPrivileges=true',
    'ProtectSystem=strict', 'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW',
    'AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW', 'KillMode=control-group',
    'Restart=on-failure', 'RestartSec=5',
    'ExecStart=/usr/bin/python3 -m redux.core.live_runtime --config /etc/redux/live.toml')
_UNITS['redux-capture-ingest.timer'] += ('OnBootSec=45s', 'OnUnitInactiveSec=20s',
    'AccuracySec=5s', 'Persistent=true')
_WORKER = ('After=local-fs.target', 'RequiresMountsFor=/captures', 'Type=oneshot',
    'User=root', 'UMask=0077', 'Environment=PYTHONPATH=/opt/redux',
    'NoNewPrivileges=true', 'ProtectHome=true', 'ProtectSystem=strict',
    'ReadWritePaths=/captures', 'CapabilityBoundingSet=', 'AmbientCapabilities=',
    'RestrictSUIDSGID=true',
    'ExecStart=/usr/bin/python3 -m redux.crack.ingest --config /etc/redux/pipeline.toml --once')

_AB_UNITS = {
    "redux-ota-health.service": (
        "multi-user.target.wants", "After=redux.service", "Requires=redux.service",
        "RequiresMountsFor=/captures /boot/firmware /boot/redux-control",
        "ExecStart=/usr/local/libexec/redux-rauc-health",
    ),
    "redux-ota-deadline.timer": (
        "timers.target.wants", "OnBootSec=120",
        "Unit=redux-ota-deadline.service",
    ),
}

_AB_REQUIRED_UNITS = {
    "redux-ota-deadline.service": (
        "Type=oneshot", "ExecStart=/usr/local/libexec/redux-rauc-deadline",
        "Restart=on-failure", "RestartSec=30", "StartLimitIntervalSec=0",
        "TimeoutStartSec=30",
    ),
}


def regular(root, path):
    file = root / path
    if file.is_symlink() or not file.is_file():
        raise ValueError(f"missing or linked image payload: {path}")
    st = file.stat()
    if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
        raise ValueError(f"empty/nonregular image payload: {path}")
    return file


def text(root, path, limit=32768):
    file = regular(root, path)
    if file.stat().st_size > limit:
        raise ValueError(f"oversized image configuration: {path}")
    return file.read_text(encoding="utf-8")


def _enabled_unit_contract(root, units):
    services = root / "etc/systemd/system"
    checked = []
    for unit, (want, *required) in units.items():
        raw = text(root, "etc/systemd/system/" + unit)
        for directive in required:
            if directive not in raw.splitlines():
                raise ValueError(f"image unit {unit} missing {directive}")
        enable = services / want / unit
        if not enable.is_symlink():
            raise ValueError(f"image unit not enabled: {unit}")
        target = enable.readlink()
        # systemctl enable from a chroot (or systemctl --root) normally writes
        # /etc/systemd/system/<unit> as an ABSOLUTE guest-root symlink.
        # Host Path.resolve() would incorrectly follow it into the build
        # host's /etc, rejecting the image that systemctl correctly enabled.
        # Treat a guest-absolute link as an image-root path and only accept
        # the exact installed unit; keep relative links strictly confined.
        expected_guest = Path("/etc/systemd/system") / unit
        expected_host = (services / unit).resolve(strict=False)
        if target.is_absolute():
            correct = target == expected_guest
        else:
            correct = (enable.parent / target).resolve(strict=False) == expected_host
        if not correct:
            raise ValueError(f"image enable link points outside expected unit: {unit}")
        checked.append(unit)
    return checked


def unit_contract(root, additional=None):
    services = root / "etc/systemd/system"
    checked = _enabled_unit_contract(root, _UNITS)
    required = {unit: policy[1:] for unit, policy in _UNITS.items()}
    required['redux-capture-ingest.service'] = _WORKER
    required['redux-fbui.service'] = ('After=redux-live.service', 'Wants=redux-live.service',
        'RequiresMountsFor=/captures', 'ConditionPathExists=/captures/config/fbui.env',
        'NoNewPrivileges=true', 'ProtectSystem=strict', 'CapabilityBoundingSet=', 'AmbientCapabilities=')
    required.update(additional or {})
    # The native TFT runtime must be present but never selected or started
    # without a measured panel and persistent operator-supplied config.
    fbui = text(root, "etc/systemd/system/redux-fbui.service")
    for directive in (
            "ConditionPathExists=/captures/config/fbui.env",
            "EnvironmentFile=/captures/config/fbui.env",
            "ExecStartPre=/usr/local/bin/redux-fbui --check --framebuffer ${REDUX_FRAMEBUFFER}",
            "RequiresMountsFor=/captures"):
        if directive not in fbui.splitlines():
            raise ValueError(f"unsafe native TFT unit: missing {directive}")
    if ((root / "captures/config/fbui.env").exists()
            or (root / "captures/config/fbui.env").is_symlink()):
        raise ValueError("image must not preconfigure the operator's TFT")
    for want in ("multi-user.target.wants", "graphical.target.wants",
                 "default.target.wants"):
        entry = services / want / "redux-fbui.service"
        if entry.exists() or entry.is_symlink():
            raise ValueError("native TFT service must not autostart")
    if "REDUX_FRAMEBUFFER=/dev/fbN" not in text(
            root, "usr/share/redux/fbui.example.env"):
        raise ValueError("native TFT template must require actual fb identity")
    for disabled in ("bettercap.service", "resize2fs_once.service"):
        mask = services / disabled
        if not mask.is_symlink() or mask.readlink() != Path("/dev/null"):
            raise ValueError(f"unsafe systemd mask for {disabled}")
    if (root / "etc/init.d/resize2fs_once").exists():
        raise ValueError("legacy root resize init script remains")
    unit_readiness.verify(root, required, text)
    return checked


def capture_contract(root, profile, *, expected_country=None):
    live = tomllib.loads(text(root, "etc/redux/live.toml"))
    pipeline = tomllib.loads(text(root, "etc/redux/pipeline.toml"))
    live_config = live.get("live")
    pipeline_config = pipeline.get("pipeline")
    if not isinstance(live_config, dict) or not isinstance(pipeline_config, dict):
        raise ValueError("missing image live/pipeline configuration")
    # The root-owned radio supervisor must not write into the separate
    # unprivileged redux.service state namespace.
    expected_live = {"state_dir": "/captures/live",
                     "capture_dir": "/captures/incoming",
                     "active_dir": "/captures/active",
                     "pipeline_database": "/captures/jobs.db"}
    expected_pipeline = {"inputs": ["/captures/incoming"],
                         "output_dir": "/captures/ready",
                         "database": "/captures/jobs.db"}
    for key, val in expected_live.items():
        if live_config.get(key) != val:
            raise ValueError(f"image live config {key} must use REDUXCAP")
    for key, val in expected_pipeline.items():
        if pipeline_config.get(key) != val:
            raise ValueError(f"image pipeline config {key} must use REDUXCAP")
    if live_config.get("require_regulatory_country") is not True:
        raise ValueError("image must require an operator-configured Wi-Fi country")
    if live_config.get("regulatory_country_file") != "/etc/redux/regulatory-country":
        raise ValueError("image regulatory country path must be baked and system-owned")
    country_file = root / "etc/redux/regulatory-country"
    if country_file.exists() or country_file.is_symlink():
        if regular(root, "etc/redux/regulatory-country").stat().st_mode & 0o022:
            raise ValueError("image regulatory country is writable by group or others")
        country = text(root, "etc/redux/regulatory-country", limit=8).strip()
        if not (len(country) == 2 and country.isascii() and country.isupper()
                and country.isalpha() and country not in ("ZZ", "AA")):
            raise ValueError("image contains invalid regulatory country")
    if expected_country is not None:
        if country_file.is_symlink() or not country_file.is_file():
            raise ValueError("expected regulatory country missing from assembled image")
        if country != expected_country:
            raise ValueError(
                f"assembled image regulatory country mismatch: expected {expected_country}"
            )
    if live_config.get("allow_connected_capture") is not False:
        raise ValueError("image must preserve passive-by-default uplink policy")
    if live_config.get("require_usb_lease") is not False or live.get("phone", {}).get("enabled") is not False:
        raise ValueError("image must keep unqualified USB lease and Bluetooth PAN paths disabled")
    if pipeline.get("audit", {}).get("enabled") is not False:
        raise ValueError("image must ship with capture audit disabled")
    for name in ("live.toml", "pipeline.toml"):
        if regular(root, "etc/redux/" + name).stat().st_mode & 0o077:
            raise ValueError(f"image config permissions are not private: {name}")
    fstabs = [line.split() for line in text(root, "etc/fstab").splitlines()
              if line.strip() and not line.lstrip().startswith("#")]
    captured = [line for line in fstabs if len(line) >= 3 and line[1] == "/captures"]
    if profile == "lite":
        if len(captured) != 1 or captured[0][:3] != ["LABEL=REDUXCAP", "/captures", "ext4"]:
            raise ValueError("Lite fstab lacks a unique REDUXCAP mount")
    elif captured:
        raise ValueError("A/B fstab must defer capture mount to its slot generator")
    if "tmpfs:recurse=0" not in text(root, "etc/overlayroot.conf"):
        raise ValueError("image overlayroot is not configured for RAM upper")
    if not (root / "captures").is_dir() or (root / "captures").is_symlink():
        raise ValueError("image lacks captures mountpoint")
    return {"incoming": "/captures/incoming", "database": "/captures/jobs.db"}


def package_inventory(root):
    raw = text(root, "usr/share/redux/packages.tsv", limit=4*1024*1024)
    entries = {}
    for line in raw.splitlines():
        fields = line.split('\t')
        if len(fields) != 2 or not re.fullmatch(r'[a-z0-9][a-z0-9+.:_-]*', fields[0]) or not re.fullmatch(r'[A-Za-z0-9.+:~_-]+', fields[1]) or fields[0] in entries:
            raise ValueError("invalid installed package inventory")
        entries[fields[0]] = fields[1]
    if not entries:
        raise ValueError("empty installed package inventory")
    required = {'python3', 'python3-pil', 'python3-dbus-next', 'hcxtools', 'bettercap', 'iw'}
    if not required <= {name.split(':')[0] for name in entries}:
        raise ValueError('installed runtime dependency inventory incomplete')
    return {'count': len(entries), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}


def verify(root, *, profile="lite", other_root=None, expected_country=None):
    if expected_country is not None:
        if (not isinstance(expected_country, str) or len(expected_country) != 2
                or not expected_country.isascii() or not expected_country.isupper()
                or not expected_country.isalpha() or expected_country in ("ZZ", "AA")):
            raise ValueError("invalid expected regulatory country")
    if profile not in ("lite", "ab"):
        raise ValueError("unknown Redux root profile")
    primary = Path(root)
    roots = [primary]
    if profile == "ab":
        if other_root is None or Path(other_root) == primary:
            raise ValueError("A/B validation needs separate mounted root slots")
        roots.append(Path(other_root))
    elif other_root is not None:
        raise ValueError("Lite export must not accept a second root slot")
    inventories = []
    for index, candidate in enumerate(roots):
        if not candidate.is_dir() or candidate.is_symlink():
            raise ValueError("export root is missing or linked")
        for filename in _MUST_EXIST:
            regular(candidate, filename)
        inventories.append(package_inventory(candidate))
        for name in ('field-diagnostics', 'resource-summary'):
            catalog_path = f"usr/share/redux/packs/{name}/pack.toml"
            reviewed = Path(__file__).parent / f"stage-redux/60-packs/files/catalog/{name}/pack.toml"
            if text(candidate, catalog_path) != reviewed.read_text():
                raise ValueError("installed native pack catalog differs from reviewed payload")
        for filename in ('registry.py', 'runtime.py', 'native_observer.py'):
            installed = regular(candidate, f"opt/redux/redux/packs/{filename}")
            reviewed = Path(__file__).resolve().parents[1] / 'redux/packs' / filename
            if installed.stat().st_size > 65536 or installed.read_bytes() != reviewed.read_bytes():
                raise ValueError("installed native implementation differs from reviewed registry")
        ab_units = {**{unit: rules[1:] for unit, rules in _AB_UNITS.items()}, **_AB_REQUIRED_UNITS} if profile=='ab' else None
        unit_contract(candidate, ab_units)
        capture_contract(candidate, profile, expected_country=expected_country)
        if profile == "ab":
            regular(candidate, "etc/rauc/keyring.pem")
            if not (candidate / "etc/rauc/system.conf").is_symlink():
                raise ValueError("A/B slot lacks generated RAUC config link")
            _enabled_unit_contract(candidate, _AB_UNITS)
            for unit, required in _AB_REQUIRED_UNITS.items():
                raw = text(candidate, "etc/systemd/system/" + unit).splitlines()
                for directive in required:
                    if directive not in raw:
                        raise ValueError(f"image unit {unit} missing {directive}")
        else:
            mask = candidate / "etc/systemd/system/rauc.service"
            if not mask.is_symlink() or mask.readlink() != Path("/dev/null"):
                raise ValueError("Lite profile must mask unused RAUC unit")
    return {"schema": 1, "profile": profile, "slots_checked": len(roots),
            "runtime_files_checked": len(_MUST_EXIST) * len(roots),
            "systemd_units": sorted(set(_UNITS) | (set(_AB_UNITS) if profile == "ab" else set())),
            "root_payload_verified": True,
            "package_inventories": inventories,
            "regulatory_country_verified": expected_country is not None,
            "physical_boot_verified": False,
            "note": "Mounted image rootfs contract only; not proof of boot or peripherals"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("lite", "ab"), required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--other-root", type=Path)
    parser.add_argument("--expected-country")
    args = parser.parse_args(argv)
    try:
        report = verify(args.root, profile=args.profile, other_root=args.other_root,
                        expected_country=args.expected_country)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
