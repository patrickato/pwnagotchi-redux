#!/bin/bash
# Build an arm64/all APT repository layout from reviewed .deb artifacts.
set -euo pipefail
[[ $# -ge 2 && $# -le 3 ]] || { echo 'Usage: repository.sh DEB_DIR NEW_REPO_DIR [GPG_SIGNING_KEY_ID]' >&2; exit 2; }
input=$(realpath "$1")
output=$(realpath -m "$2")
[[ ! -e $output ]] || { echo 'Use a new repository output directory.' >&2; exit 1; }
for tool in apt-ftparchive dpkg-deb gzip; do command -v "$tool" >/dev/null; done
mkdir -p "$output/pool/main" "$output/dists/redux/main/binary-arm64"
count=0
for deb in "$input"/*.deb; do
    [[ -f $deb ]] || continue
    architecture=$(dpkg-deb -f "$deb" Architecture)
    [[ $architecture == arm64 || $architecture == all ]] || { echo "Rejecting incompatible package architecture: $architecture" >&2; exit 1; }
    cp "$deb" "$output/pool/main/"
    count=$((count + 1))
done
[[ $count -gt 0 ]] || { echo 'No .deb artifacts found.' >&2; exit 1; }
cd "$output"
apt-ftparchive packages pool > dists/redux/main/binary-arm64/Packages
gzip -n -c dists/redux/main/binary-arm64/Packages > dists/redux/main/binary-arm64/Packages.gz
apt-ftparchive -o APT::FTPArchive::Release::Origin=pwnagotchi-redux \
    -o APT::FTPArchive::Release::Suite=redux -o APT::FTPArchive::Release::Codename=redux \
    -o APT::FTPArchive::Release::Architectures=arm64 -o APT::FTPArchive::Release::Components=main \
    release dists/redux > dists/redux/Release
if [[ $# == 3 ]]; then
    gpg --batch --yes --local-user "$3" --clearsign --output dists/redux/InRelease dists/redux/Release
    gpg --batch --yes --local-user "$3" --detach-sign --output dists/redux/Release.gpg dists/redux/Release
    echo "Signed repository: $output; publish only after verifying its signing identity."
else
    echo "Unsigned staging layout: $output; APT must not trust/publish this until Release is signed."
fi
