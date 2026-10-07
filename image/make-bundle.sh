#!/bin/bash
# Sign an exported A/B image; private keys remain exclusively on the release host.
set -euo pipefail
[[ $# == 5 && $EUID == 0 ]] || { echo 'Usage (root): image/make-bundle.sh IMAGE OUTPUT.raucb CERT KEY VERSION' >&2; exit 2; }
image=$(realpath "$1")
output=$(realpath -m "$2")
cert=$(realpath "$3")
key=$(realpath "$4")
version=$5
[[ ! -e $output && $version =~ ^[A-Za-z0-9._+-]+$ ]] || { echo 'Choose a new output and a simple release version.' >&2; exit 1; }
for tool in rauc mksquashfs losetup mount tar; do command -v "$tool" >/dev/null; done
scratch=$(mktemp -d)
loop=''
cleanup() {
    mountpoint -q "$scratch/boot" && umount "$scratch/boot"
    mountpoint -q "$scratch/root" && umount "$scratch/root"
    [[ -z $loop ]] || losetup -d "$loop"
    # Scratch is the exact directory returned by mktemp, never an operator path.
    rm -rf -- "$scratch"
}
trap cleanup EXIT
loop=$(losetup --show --find --read-only --partscan "$image")
mkdir "$scratch/root" "$scratch/boot" "$scratch/bundle"
[[ $(blkid -s PARTLABEL -o value "${loop}p4") == REDUXROOTA ]] || { echo 'Expected the A/B GPT profile.' >&2; exit 1; }
mount -o ro,noload "${loop}p4" "$scratch/root"
mount -o ro "${loop}p2" "$scratch/boot"
tar --numeric-owner --xattrs --acls --one-file-system --exclude=./lost+found --exclude='./captures/*' --exclude='./boot/firmware/*' --exclude='./boot/redux-control/*' -C "$scratch/root" -cf "$scratch/bundle/rootfs.tar" .
tar -C "$scratch/boot" -cf "$scratch/bundle/boot.tar" .
printf '#!/bin/sh\nexec /usr/local/bin/redux-ota hook "$@"\n' > "$scratch/bundle/hook.sh"
chmod 0755 "$scratch/bundle/hook.sh"
cat > "$scratch/bundle/manifest.raucm" <<EOF
[update]
compatible=pwnagotchi-redux-pi4-pi5-arm64-ab-v1
version=$version
[bundle]
format=verity
[hooks]
filename=hook.sh
[image.boot]
filename=boot.tar
hooks=post-install
[image.rootfs]
filename=rootfs.tar
EOF
rauc --cert "$cert" --key "$key" bundle "$scratch/bundle" "$output"
rauc --keyring "$cert" info "$output"
echo "Signed bundle: $output"
