#!/usr/bin/env bash
# One laptop-side action for a disposable field image: key + fresh build.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
[[ $EUID != 0 ]] || { echo 'Run as your normal Ubuntu user, not root.' >&2; exit 1; }
for tool in ssh-keygen sudo date; do
    command -v "$tool" >/dev/null || { echo "Missing $tool" >&2; exit 1; }
done
mkdir -p "$HOME/.ssh"
chmod 0700 "$HOME/.ssh"
key="$HOME/.ssh/redux_field_ed25519"
if [[ ! -e "$key" && ! -e "$key.pub" ]]; then
    ssh-keygen -q -t ed25519 -N '' -C 'redux-field-only' -f "$key"
fi
[[ -f "$key" && -f "$key.pub" && ! -L "$key" && ! -L "$key.pub" ]] || {
    echo 'Field key pair incomplete/unsafe; refusing to build.' >&2; exit 1;
}
chmod 0600 "$key"
workspace="$HOME/redux-field-$(date +%Y%m%d-%H%M%S)"
[[ ! -e "$workspace" ]] || { echo "Build folder exists: $workspace" >&2; exit 1; }
printf '\nBuilding disposable field image (Pi login: pi / redux; SSH: public key only).\n'
printf 'Build directory: %s\n' "$workspace"
# This user's physically US-based disposable Pi field test previously
# selected US explicitly. Other operators must override for their location.
country=${REDUX_REGULATORY_COUNTRY:-US}
[[ $country =~ ^[A-Z]{2}$ && $country != ZZ && $country != AA ]] || {
    echo 'Invalid operator regulatory country; select your real two-letter country.' >&2
    exit 1
}
printf 'This field image will configure regulatory country: %s (override REDUX_REGULATORY_COUNTRY).\n' "$country"
printf 'After flashing, connect the Pi Ethernet port to your trusted LAN.\n\n'
# Do not publish a field build from uncommitted or mismatched source.
[[ -z $(git status --porcelain --untracked-files=normal) ]] || {
    echo 'Field build requires a clean committed workbench checkout.' >&2
    exit 1
}
revision=$(git rev-parse HEAD)
sudo env REDUX_DEV_LOGIN=1 REDUX_OPERATOR_PUBKEY_FILE="$key.pub" \
  REDUX_REGULATORY_COUNTRY="$country" REDUX_BUILD_DIR="$workspace" "$ROOT/build.sh"
# Before updating "latest" or advertising the disk image, re-check exactly
# the downloadable artifact, its manifest, and the mounted-root export gate.
python3 "$ROOT/scripts/qualify-field-image.py" --workspace "$workspace" \
  --revision "$revision" --country "$country" --profile lite
if [[ -L "$HOME/redux-field-latest" ]]; then
    rm "$HOME/redux-field-latest"
fi
if [[ ! -e "$HOME/redux-field-latest" ]]; then
    ln -s "$workspace" "$HOME/redux-field-latest"
fi
printf '\nSUCCESS. New image: %s/pi-gen/deploy\n' "$workspace"
printf 'Windows: \\\\wsl.localhost\\Ubuntu\\home\\%s\\redux-field-latest\\pi-gen\\deploy\n' "$USER"
printf 'From your Ubuntu laptop (Pi on Ethernet):\n'
printf 'ssh -i ~/.ssh/redux_field_ed25519 pi@redux.local\n'
printf 'Then: sudo redux-report --full\n'
