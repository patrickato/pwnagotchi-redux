# Prepared capture artifact integrity audit

Redux ingestion records successful conversions in `/captures/jobs.db`.
The dashboard's count of ready rows is not proof that the corresponding
`.hc22000` files are still intact. The offline read-only checker provides
a separate **integrity** signal without opening or exporting any raw PCAP.

Run against a disposable fixture or an offline mounted capture partition:

```bash
python3 scripts/capture-artifact-audit.py --db /path/to/jobs.db --output /path/to/ready
```

It checks each `ready` row against its canonical SHA-256-named prepared file,
checks regular-file type and a 33 MiB ceiling (matching the ingest worker), pins files through
`O_NOFOLLOW`, verifies digest against ledger, checks for concurrent changes,
and refuses a symlinked ledger or output directory. It imposes a 10,000
ready-row bound and 512 MiB total file-hashing budget per invocation. If the
budget is exhausted, it returns attention with the explicit
`audit_byte_budget_exceeded` finding; partial verification never passes. Its JSON output contains only aggregate
counts and predefined error codes; no file paths, SSIDs, MACs, handshake
records, credentials or captured packet bytes appear.

Exit 0 means that at least one recorded ready artifact was structurally intact at
the time of the read and no failures were found. Exit 1 means unavailable
or failed evidence. Exit 3 and result UNKNOWN mean an empty ledger: no
prepared artifacts have yet been verified.

Run the offline tests:

```bash
python3 -m pytest -q tests/test_capture_artifact_audit.py
```

This audit is **not** part of the live runtime, does not scan wireless traffic,
and must not be mistaken for cryptographic verification that a handshake is
genuine. It is an additional forensics/integrity gate suitable for eventual
integration into release and support diagnostics. No Pi deployment is implied.

## Cross-component size contract

The auditor enforces the same 33 MiB prepared-artifact ceiling as the
ingestion worker. It must not reject a valid prepared artifact solely
because the auditor and worker disagree about output size. Tests enforce
that correspondence and reject 33 MiB + 1 byte before hashing.

## Lightweight integrity receipt contract

The audit CLI now emits a schema-1 JSON receipt with `completed_utc`,
`result`, `checked`, `ready_rows`, and aggregate issue counts. The
reusable `redux.crack.integrity_receipt.classify_receipt()` validates that
receipt without reading the capture partition or rehashing artifacts.

A receiver must check the timestamp and report shape before presenting any
result. A receipt older than one hour is UNKNOWN, not a present-tense PASS.
PASS requires at least one verified ready artifact, every ready row checked,
and zero integrity errors. Malformed schemas, unknown issue codes, impossible
counts, and future timestamps are UNKNOWN. Known integrity discrepancies
become ATTENTION.

This module is a reusable *read-side contract*, not a new background job or
dashboard claim. Scheduling, secure publication, and Doctor/UI integration
remain separate work; until a producer supplies a validated receipt, the
interface must display integrity as unknown.

## Cached read-side integration

`redux.crack.integrity_receipt.read_cached_receipt(path)` reads an existing
JSON receipt without opening the capture ledger or prepared files. It refuses
symlinks and nonregular sources, limits the read to 8 KiB, rejects duplicate
JSON keys and malformed/nonfinite values, and reuses the freshness and
counter-consistency classifier. Missing or unsafe sources report UNKNOWN.

No background scheduler, file publisher, or Doctor/dashboard wiring is
assumed from this addition. Those require a separate controlled integration
of an atomic receipt producer, with the current audit remaining explicitly
read-only. In particular, a recent receipt is evidence of its audit time,
not proof that captured data can never change afterward.

## Explicit, atomic receipt publication

`redux.crack.integrity_receipt.publish_receipt(destination, audit_result)`
provides opt-in private cache publication for an already computed report.
It validates the compact schema, writes via a same-directory 0600 temporary
file, fsyncs it, and atomically replaces the destination. A symlinked
destination or parent is refused. The caller must only use a trusted local
receipt directory; this is not a safe writer for directories controlled by
untrusted concurrent users.

The function is not invoked automatically by web requests or status refreshes.
Until an operator-selected audit job actually publishes a receipt, Doctor
must report artifact integrity as UNKNOWN. Publication tests and a physical
installation/permissions review are required before scheduling this on the Pi.

## Runtime Doctor wiring (opt-in audit, automatic reading)

The offline auditor remains read-only unless the caller explicitly passes
`--receipt`. A trusted operator may choose the writable capture partition's
diagnostics directory for a cached receipt:

```bash
python3 scripts/capture-artifact-audit.py \
  --db /captures/jobs.db --output /captures/ready \
  --receipt /captures/diagnostics/artifact-integrity.json
```

This runs the bounded SHA-256 audit once and atomically publishes only its
private summary. The directory must already exist and be controlled by the
trusted operator; the tool does not create it or run with escalated privileges.
Normal runs without `--receipt` remain non-mutating.

The live runtime now reads only that fixed 8 KiB receipt path, under the
existing capture partition's diagnostics directory, during its existing
status checkpoint. The canonical status JSON exposes
`artifact_integrity`; live Doctor adds `capture artifact integrity`
with status OK for recent verified artifacts, ACTION when a recent audit
observed discrepancies, or UNKNOWN for a missing, stale or invalid receipt.

**Important limitations:** no audit is scheduled automatically, dashboard
refreshes never run the audit, and no receipt is shipped as evidence in an
unverified image. The presence of valid prepared data does not prove an
authentic wireless handshake. Receipt freshness defaults to one hour and
only covers the audit's measured moment. Neither RF acceptance nor physical
deployment is implied.

## Cache filesystem safety (hardened)

Both the receipt publisher and the cached reader now pin the actual
diagnostics directory with a Linux directory file descriptor. They refuse
symlinked final directory components and directories writable by other
users or groups. They use `O_NOFOLLOW` for receipt access. The publisher
creates a 0600 uniquely named file, fsyncs its contents, atomically replaces
the destination using directory-relative rename, and fsyncs the directory.
The reader checks the same opened file's identity, size, mtime, and ctime
before/after a bounded read to reject in-place changes. The caller remains
responsible for protecting ancestor directories.

The baked `redux-live.service` and first-boot report service both run as
root with `ReadWritePaths=/captures`. The cache remains **opt-in**, and
these permissions do not authorize automatically enabling a new audit timer
on a deployed image.

No audit is performed in the HTTP handler. New isolated fixture checks cover
private publication, atomic replacement, symlinked targets/directories,
shared-directory refusal, oversized receipts and torn in-place reads.
A complete checkout regression run is required before declaring this
new code software-qualified.

## Untrusted timestamp and malformed JSON boundaries

Receipt time validation bounds UTC epochs before floating-point conversion.
Enormous integers (including JSON integer values larger than a machine float),
nonfinite numbers, future times, and stale observations cannot crash the
collector or be interpreted as a recent PASS. The loader also refuses
truncated/nonregular files and inconsistent issue totals; failure to validate
a replacement receipt leaves the previously published file unchanged.

The classifier timestamp hardening and its edge-case unit tests were added
after the Sprint 05 full-regression baseline. Local replay of the equivalent
pure classifier logic passed 11 boundary cases; exact repository tests still
require the next integrated checkout qualification.

## REDUXCAP mount prerequisite for explicit audit CLI

When `--db`, `--output`, or `--receipt` names a path beneath
`/captures`, the CLI requires the shared Redux
`storage_mount.capture_mount_snapshot()` to identify a genuine writable
ext4 REDUXCAP mount. The identity is checked again after the audit and before
publishing the private receipt. Unavailable mount evidence returns exit 2 with
`capture_mount_unverified`; changed mount identity returns exit 2 with
`capture_mount_changed`. No receipt is published in either case.

Offline tests using explicit temporary paths do not need a physical mount.
This guards against accidentally reading or publishing under the root
filesystem when /captures is missing. It does not bypass authorization for
operating on real captures or make a genuine Pi acceptance claim.
