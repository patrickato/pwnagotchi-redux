# Pure target selection: usable without root, radio hardware or uname mocks.
nexmon_driver_for() {
    local kernel=$1 source_dir=$2 module_dir=$3 driver
    if [[ ! $kernel =~ ^([0-9]+)\.([0-9]+)\..*-(v8|2712)$ ]]; then
        echo "Unexpected non-arm64 Pi4/Pi5 target kernel: $kernel" >&2
        return 1
    fi
    driver="$source_dir/patches/driver/brcmfmac_${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.y-nexmon"
    if [[ ! -d $driver || ! -d $module_dir/build ]]; then
        echo "No nexmon driver or headers for target $kernel" >&2
        return 1
    fi
    printf '%s\n' "$driver"
}

# dpkg wildcard results also include known-but-uninstalled package names.
nexmon_installed_kernel_packages() {
    awk -F '\t' '$2 == "installed" { print $1 }'
}
