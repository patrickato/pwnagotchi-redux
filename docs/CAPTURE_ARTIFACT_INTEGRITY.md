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

## Directory-replacement and nested-JSON regressions

The prepared-artifact auditor opens the output directory once with
`O_DIRECTORY | O_NOFOLLOW` and pins its descriptor throughout the hash scan.
Each prepared artifact is opened by basename relative to that descriptor. A
directory replaced by a symlink after the audit starts cannot redirect reads
to a different prepared file tree. Offline regression fixtures simulate this
race and check that the pinned original artifact is the one verified.

Cached receipt readers treat an excessively recursive JSON parser exception as
UNKNOWN rather than allowing a malformed receipt to interrupt live checkpoint
production. JSON runtimes may accept different maximum nesting depths; the
contract is fail-closed UNKNOWN for non-object or unparseable receipts.

The pinned-directory implementation was exercised in five isolated Linux
filesystem fixtures. These fixtures are useful local behavior checks but do not
replace the full repository test suite or physical Pi validation.

## Stable SQLite observation and final directory identity

Integrity evidence now holds an explicit read-only SQLite transaction for
the schema check, ready-row count and ready-row selection. Without this,
a concurrent ingest worker changing statuses between two SELECT statements
could produce contradictory audit receipts. The source also refuses a
count-versus-row mismatch instead of marking unverified rows as PASS.

The prepared-artifact directory is read through its pinned directory
descriptor, and after hashing the audit rechecks the canonical directory
name's inode. A symlink swap cannot redirect what is read, **and** losing
the original public directory name now causes
`output_directory_changed` (ATTENTION) rather than a misleading PASS.
The timestamped receipt classifier recognizes this discrepancy.
The audit does not lock or interrupt the processing worker.

Offline WAL concurrency replay verifies that a writer can update the
database while the read transaction continues to see one consistent
ready-row snapshot. Repository regression tests cover both the WAL
interleaving and directory replacement, but the full integrated suite
must be rerun after these commits.

## Audit receipt classification and content-change hardening

The saved receipt validator now distinguishes **per-artifact** issues from
**audit-global** issues. A legitimate audit can record a missing prepared
file and then detect that its output directory was replaced; both must be
reported as ATTENTION, not suppressed as an impossible issue total.
Global issue counters must be exactly one each; row-level issue totals
cannot exceed checked rows. An over-limit SQLite ready-row population
greater than 10,000 also remains ATTENTION even though no rows were hashed.
An unauthenticated or stale receipt still cannot prove healthy captures.

While hashing prepared artifacts, the auditor now compares ctime as well
as mtime, inode, device and size. Changing a file's contents and restoring
its mtime is detected as a concurrent content mutation rather than a PASS.
A stale receipt never proves present-tense artifact integrity.

The updated receipt classifier and auditor were copied byte-for-byte from
their committed Git blobs into an isolated Linux test directory; the source
blob hashes were checked against GitHub and 23 targeted tests passed.
This validates the two audited modules, not the full Redux repository or
its physically mounted REDUXCAP partition.

## Ledger identity across concurrent processing

The auditor now pins the original regular `jobs.db` inode while reading a
transactional snapshot and hashing prepared artifacts. After the scan, it
checks that the ledger's public path is still the same regular inode. If the
ledger was removed, replaced or redirected by a symlink, the audit emits
`ledger_changed` as ATTENTION. The saved receipt classifier recognizes this
global issue. Normal writes to the same SQLite inode in WAL mode remain
permitted: no ingestion worker is stopped and no new lock is taken.

This is a consistency check of the public ledger path at the audit boundary,
not an anti-tampering guarantee against privileged writers and not physical
proof of the prepared files on a Pi. Tests cover a ledger-path replacement,
WAL updates on a stable inode and actionable receipt classification.

## Private receipt provenance and permissions

The cached receipt is an internal diagnostic assertion, **not** an externally
authenticated attestation. As an additional local trust boundary, the publisher
and reader require the diagnostics directory to be owned by their own effective
Unix user and not be group- or world-writable. Readers also require the receipt
file to have no group/other access bits and to be owned by that directory's
owner. Atomic publication creates mode `0600` receipts.

The baked live runtime currently runs as root: a usable
`/captures/diagnostics` directory therefore needs to be root-owned with
private permissions, created only through an explicitly approved installation
step. A receipt created by an unprivileged account in that account's own
directory is deliberately UNKNOWN to the root-run live Doctor. Do not change
service ownership, recursively chmod the capture partition, or enable an audit
timer merely to make the cache appear healthy.

Unit tests now cover readable but nonprivate receipts, foreign-owned receipts
and foreign-owned cache directories. The exact receipt implementation matching
Git blob `1f5d1e537ac5728786647a71c692494da034e0a9` passed 32 isolated
Linux tests, including read/write provenance and WAL/directory-swap behavior.
This is not a full source-tree regression or device qualification.

## Zero-scan operator inspection

The audit command now supports reading the **existing** cached verdict
without opening `jobs.db`, reading prepared artifacts, or running SHA-256:

```sh
python3 scripts/capture-artifact-audit.py \
  --inspect-receipt /captures/diagnostics/artifact-integrity.json
```

This prints only bounded and sanitized keys:
`status`, `reason`, `checked`, `ready_rows` and `age_seconds`.
Exit **0** means a recent verified ready-artifact audit, exit **1** means
the cached audit reported an actionable discrepancy, and exit **3** means
unavailable, invalid, stale or otherwise unverifiable evidence. The
inspection option cannot be combined with `--receipt` publication.
It never triggers an audit or refreshes an old cache.

For a receipt beneath `/captures`, inspection checks the shared
REDUXCAP mount identity before and after the read. Missing or changed
mount evidence reports UNKNOWN even when a JSON file appears readable.
For isolated test receipts outside that mount, normal private-file and
ownership protections still apply. A cached receipt describes the last
audit moment; it is not proof of currently receiving Wi-Fi frames.

## Prepared filename identity during digest verification

SHA-256 verification keeps an artifact's file descriptor open for the entire
bounded read, but a correct hash of that inode alone does not prove the
canonical prepared filename still points to it. After hashing, the auditor
rechecks the basename relative to the **same pinned output directory**, with
`follow_symlinks=False`, comparing regular-file type, device/inode, size,
mtime and ctime to the already opened descriptor. A missing/replaced filename,
symlink substitution, or concurrent metadata mutation produces
`artifact_changed_during_audit` (ATTENTION), even if a replacement has
identical contents and an identical digest.

This is a bounded, best-effort consistency check of one audit instant; it is
not a filesystem lock or an anti-tampering guarantee against subsequent
privileged writes. Two source regression tests cover same-content inode
replacement and symlink substitution during hashing. Four isolated Linux
filesystem identity checks passed, including a no-change control; complete
checkout qualification against these commits remains outstanding.

## Advertised path identity during final verification

Some operator-supplied output directories may be reached through a symlinked
ancestor (for example, an alias parent in a fixture). A fixed canonical path
alone is not evidence that the **operator-supplied** path still leads to the
same prepared-artifact directory at audit completion. Final verification now
compares both canonical and advertised path identities with the directory
descriptor used for hashing. If the alias/ancestor is redirected while the
audit runs, the verdict is ATTENTION (`output_directory_changed`) rather
than PASS, even though the originally opened records hashed successfully.

An offline regression fixture replaces only the parent alias after the output
directory is opened, preserving the original directory and changing its
advertised location. This is a best-effort race detector, not a permanent
filesystem lock; full combined Redux regression against these latest changes
is still required.

## Bounded SQLite read phase

The auditor limits the read-only SQLite phase to a **10-second monotonic VM
deadline**, checked every 1,000 SQLite VM operations. The ordinary SQLite
one-second busy timeout still applies to database locks. Each SQLite string
or BLOB cell the audit may materialize is constrained by
`SQLITE_LIMIT_LENGTH` to 8 KiB before the ready-row SELECT is performed.
This prevents an abnormal ledger row with a multi-megabyte `output_path`
from unexpectedly allocating that entire value in the diagnostic process.
Schema checks, COUNT and ready-row selection still share one read-only
transaction, and an interrupted/oversized SQLite operation produces
`audit_unavailable` (ATTENTION), never an integrity PASS.

These bounds apply to **SQLite query execution**, not the SHA-256 file
phase. File hashing retains separate 33 MiB per-artifact and 512 MiB
aggregate byte limits; operating system storage stalls cannot be assigned
a guaranteed wall-clock deadline by those byte bounds alone.

The three new deterministic unit tests cover oversized ledger values, forced
SQLite VM interruption and ordinary passing records. On the local Linux
SQLite runtime, these specific low-level behaviors reproduced as
`DataError: string or blob too big`, `OperationalError: interrupted`,
and successful retrieval of an ordinary ready row. Full checkout
qualification remains necessary after these commits.

## Autonomous bring-up: fixed-scope offline plan (October 21)

The current disposable Pi 4's *historical* read-only session reported five
processing-ledger rows (one marked ready, four invalid). The pipeline's last
reported scan counted five duplicates. None of those counts independently
verify the ready file's bytes or prove a genuine wireless handshake.

Before any new physical access, review the auditor's immutable source scope:

```sh
python3 scripts/capture-artifact-audit.py --plan
```

The offline plan returns exit **3** intentionally and contains only fixed
JSON: maximum 10,000 ready rows, at most 33 MiB per prepared artifact,
512 MiB aggregate hashing, an 8 KiB SQLite cell bound and a 10-second query
deadline. It does not touch `/captures`, open SQLite, hash files, perform
SSH, or write a receipt. It refuses mixing `--plan` with receipt access or
publication. New source-only tests enforce these properties.

The actual audit requires separate operator authorization covering access
to the live capture ledger/prepared files or a verified offline copy.
Run it only on the disposable Redux card after independently verifying the
storage mount and target. Prefer *no* `--receipt` for the first investigation.
Even a passing SHA-256 audit proves file integrity at audit time, not packet
origin, valid RF monitor framing, successful over-the-air reception, or
handshake authenticity. The old Oct 20 field report is already stale; do not
convert its historical counts into present-time PASS.
