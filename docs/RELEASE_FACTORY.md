# Host release candidate factory

`python3 scripts/redux-release.py factory` wraps the existing `build.sh`, image
manifest publisher, offline payload verifiers and RAUC bundle builder. It never
invokes sudo, installs packages, flashes storage, connects to a Pi, changes Wi-Fi,
or changes host boot configuration. Only a caller already running on a qualified
privileged build host can start image assembly. Verification/inspection need no
root and use regular image files, debugfs and mtools without mounting them.

## One supported workflow

Start from a clean, committed source checkout and record the full revision:

```sh
git status --short --branch
git pull --ff-only
git rev-parse HEAD
mkdir -p build
python3 scripts/redux-release.py --json factory preflight \
  --revision FULL_40_DIGIT_SHA --profile lite --country US \
  --workspace "$PWD/build/rc-work-001" --out "$PWD/build/rc-lite-001"
```

Country is explicitly selected by the operator for the intended device location;
it is not inferred. `US` is an example, not a universal default. Preflight writes
nothing and does not fetch sources. JSON includes source/tree identity, upstream
pins, configuration digest, source epoch, selected inputs, tool versions and the
existing builder's detailed host checks. Work/output paths must have existing
parents, be disjoint and new, have no whitespace or symlink ancestors, and avoid
boot, system, device, captures and removable-media trees. Paths inside the source
checkout must be Git-ignored build outputs. Prior attempts are never reused.

Once the host prerequisites and privilege are deliberately provided, use the same
arguments with `factory build`. It performs the whole sequence, not a prepare-only
run:

```sh
python3 scripts/redux-release.py factory build \
  --revision FULL_40_DIGIT_SHA --profile lite --country US \
  --workspace "$PWD/build/rc-work-001" --out "$PWD/build/rc-lite-001"
```

No escalation is attempted. The current ordinary WSL user cannot run the root-only
pi-gen assembly; opening a privileged host session is a separate operator action.
The factory does not call the legacy field-image wrapper, provision SSH keys,
select a display overlay, enable a desktop, or enable a development console login.

The factory creates a private detached source copy at the exact selected revision.
The existing builder stages source, fetches pinned pi-gen/Nexmon inputs, uses
Bookworm APT, assembles the image and publishes its native manifest/checksum.
Ambient `REDUX_*`, shell-startup and Git configuration environment overrides are
not passed into the builder. Controlled build inputs are recorded explicitly.
The original checkout and isolated source must still have the same clean revision
before publication. Concurrent source changes fail the attempt instead of being
silently included. No branch in the original checkout is switched or rewritten.

The actual completed `.img.xz` must pass decompression, partition/profile, labels,
manifest/digest, installed runtime, boot, package inventory and update-policy gates.
The selected captures capacity must equal the measured partition size. Factory
captures sizes are 64–32768 MiB; the existing total inspection limit is 64 GiB.
The private workspace is also the scratch parent for full payload qualification,
so a small `/tmp` does not have to hold the decompressed build.

## A/B signing and trust

A/B uses the same transaction with `--profile ab` and all of:

```text
--cert /external/release-signing-certificate.pem
--key /external/private-release-signing-key.pem
--keyring /external/trusted-update-ca.pem
--version release-version
```

These files must be external to source, workspace and output. The signing key must
have private permissions and match the certificate. This file-based workflow
supports noninteractive unencrypted PEM keys; encrypted/HSM/PKCS#11 signing is not
implemented here. No production key is generated or stored in the repository,
private workspace source copy, image, public candidate or receipt. Public certs
and keyrings are bounded snapshots checked for private material and validity.
Never put production keys in fixture directories. Signing input changes during
assembly are rejected.

The factory decompresses the measured A/B image into its private workspace and
calls the existing root-only `image/make-bundle.sh` on that regular file. The helper
uses read-only loop mounts of this newly built image, then signs a standard RAUC
verity bundle. It accepts an explicit trust keyring (the old five-argument form
remains supported) and bounds SquashFS compression to one worker. The full offline
candidate gate independently checks the signature, complete verity payload,
manifest/build compatibility, archive safety and image/bundle runtime binding.
An absent, incomplete, wrongly signed or unsupported bundle cannot complete an
A/B factory transaction. This establishes cryptographic software evidence, not
physical A/B installation or rollback acceptance.

## Outputs, verification and inspection

Successful output directories contain exactly:

- the original completed `*.img.xz` plus its native `.release.json` and `.sha256`;
- A/B only: `update.raucb` and the public `keyring.pem`;
- `factory.json`: final no-replace completion receipt, per-file sizes/hashes,
  exact build inputs/provenance, tool requirements/versions, embedded package
  inventory hashes/counts and all actual-artifact verification results.

The image retains `/usr/share/redux/packages.tsv` with actual installed package
versions. Validation rejects malformed, duplicate or empty inventories and requires
current telemetry, Doctor, worker-evidence, web and canonical UI dependencies.
The receipt is not a signature. Self-generated checksums establish consistency,
not independent image authenticity. A/B `update_bundle_authenticated=true` refers
only to the explicitly trusted update signature; `release_authenticated=false`
remains explicit for the unsigned image transport.

```sh
python3 scripts/redux-release.py --json factory inspect --candidate build/rc-lite-001
python3 scripts/redux-release.py --json factory verify --candidate build/rc-lite-001
```

`inspect` checks the receipt/inventory hashes and labels validation as recorded;
it does not rerun embedded payload or signature checks. `verify` reruns the existing
actual-artifact gate. A/B re-verification requires an independently supplied
`--keyring /external/trusted-update-ca.pem`; the adjacent keyring is not automatically
trusted. Supply `--trusted-sha256` only when obtained through an independent trusted
channel, never merely by copying the checksum from the same output directory.
`--scratch-dir /existing/private/ext4-folder` selects temporary inspection storage
for `factory verify` and the existing `candidate` command.

The underlying gate remains directly available:

```sh
python3 scripts/redux-release.py --json candidate \
  --image build/rc-lite-001/EXACT_IMAGE.img.xz \
  --revision FULL_40_DIGIT_SHA --profile lite --country US
```

Exit codes: preflight PASS is 0; failures are 1; completed build/inspection and
otherwise valid verification normally return 3 because physical acceptance and/or
independent image authenticity are UNKNOWN. `factory_completed` means a completed
software transaction, never production readiness. `offline_qualified` is the
underlying gate's stricter result and remains false without independent image
trust. Physical boot/update/recovery always remains UNKNOWN in this workflow.

## Failure handling and reproducibility limits

Each new workspace is 0700, with private bounded stage logs and `attempt.json`.
Build commands have a six-hour deadline and 64 MiB combined log budget. Cancellation
terminates only that stage's process group, first with TERM then bounded KILL.
An interrupted or failed build retains its workspace and any partial output;
no old candidate is removed, overwritten or repointed. Only the final atomically
published `factory.json` marks completion. If final private-journal persistence
fails after that receipt is committed, the result explicitly reports a warning.
If disk failure prevents any journal write, absence of a completed receipt still
prevents acceptance. Never publish the whole workspace: builder logs and the
build-local random console credential file are private.

After failure, inspect the recorded stage and private logs. A killed privileged
builder may leave its own loop mounts/processes; do not blindly unmount devices or
resume stale work. Have the authorized host operator inspect and clean only the
specific build resources, then choose fresh workspace/output paths. The factory
never performs broad cleanup or alters host boot configuration.

This is a source-pinned, repeatable workflow, not a bit-for-bit build guarantee:
APT metadata/packages come from live repositories, and the existing builder creates
random build-local account credentials. Actual installed versions and their digest
are retained in the image/evidence, and `SOURCE_DATE_EPOCH` is recorded/passed, but
an archival APT snapshot and deterministic account provisioning would be needed
for a stronger reproducibility claim. Do not label synthetic test images or
ephemeral test-key bundles as distributable production releases.

Continue with [BETA_HANDOFF.md](BETA_HANDOFF.md) and [RELEASE_QUALIFICATION.md](RELEASE_QUALIFICATION.md).
Source CI, a complete genuine image, a trusted signed update and physical Pi 4/5
RF/display/boot/failed-trial/rollback/recovery acceptance are separate gates.
