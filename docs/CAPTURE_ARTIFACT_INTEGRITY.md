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
