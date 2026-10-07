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

nexmon_build_module() {
    local kernel=$1 module_dir=$2 driver=$3 vermagic
    # Both kernels use the same external source directory. Kbuild can otherwise
    # reuse .o/.mod.c compiled for 2712 when the next target is v8.
    make -C "$module_dir/build" M="$driver" ARCH=arm64 clean || return
    make -C "$module_dir/build" M="$driver" ARCH=arm64 -j2 modules || return
    vermagic=$(modinfo -F vermagic "$driver/brcmfmac.ko") || return
    [[ $vermagic == "$kernel "* ]] || {
        echo "Driver kernel mismatch: expected $kernel, got $vermagic; refusing installation." >&2
        return 1
    }
}
