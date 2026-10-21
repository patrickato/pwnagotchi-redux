# One-shot prepared artifact audit on an approved Redux Pi 4

**Status:** Explicit operator authorization granted October 21, 2026 for
**one** bounded, read-only verification of **at most one** prepared
`.hc22000` output. The independent permission to share **only sanitized
categorical hardware feedback** with Codex was approved at the same time.
These two permissions do not authorize writing to the Pi, transmitting RF,
interfering with radios, accessing raw PCAP, brute-forcing passwords,
restarting services, reflashing the card, or recurring artifact audits.

## Minimal hardware procedure

The laptop-side launcher `scripts/redux-one-artifact-field-audit.py` reuses
the same previously approved owner-private SSH host/key identity stored in
WSL's field bridge policy. It **does not** borrow the hourly metadata
observer's attempt count. It enforces SSH BatchMode, strict known-host
checking, one connection attempt, no port or agent forwarding, no pseudo-TTY,
no sudo, and a local 70-second deadline. It consumes a private,
owner-only `consumed.json` marker **before** SSH: failure or timeout does
not automatically retry. Repetition requires separate fresh authorization
and deliberate operator review; do not delete that marker to bypass scope.

The fixed remote entry point is `python3 -I`. Reviewed Python audit
source is sent on **stdin** and executed in Pi memory, without creating
remote scripts or installing packages. It is sourced from a clean Git
workbench checkout and compared byte-for-byte to the committed file.
The launcher never outputs SSH credentials or target identifiers.

The remote preflight must independently identify the current
`/captures` as a mounted **ext4**, read-write, **REDUXCAP-labelled**
block device. A missing or incorrect capture mount stops **before
opening jobs.db**. Remote operation uses only the existing non-root
`pi` SSH identity. On a genuine REDUXCAP filesystem, it opens
`/captures/jobs.db` via read-only SQLite and verifies that the ready
population is no greater than **one**. More than one ready row stops
before hashing any prepared output. For precisely one ready record it
checks the canonical SHA-256-named regular file under
`/captures/ready`, with the existing auditor's inode/mtime/ctime
continuity checks, 33 MiB single-file cap, and bounded query budget.

**No raw capture or prepared hash material is sent to the laptop**.
Only predefined codes and bounded counters are printed:
`verified_bytes`, `attention`, or `unknown`, and one approved reason.
A byte-level SHA match is evidence of **file integrity**, not
handshake authenticity, a successful RF receive path, a usable
credential hash, or release acceptance. All of those remain UNKNOWN.

If files are inaccessible without sudo, the mount cannot be verified,
or any scope/hash/identity guard fails, stop. Do not broaden the privilege
scope or choose different paths. The original field metadata observer
remains on its frozen hourly schedule and approval.

## Operator's explicitly approved one-time execution

After the current GitHub workbench CI is green, **one** WSL command can
activate model-bound filtered categories and perform the single audit.
The operator's affirmative consent is represented by the two separate
explicit flags:

```bash
cd ~/redux-workbench-src &&
git pull --ff-only &&
python3 -m pytest -q tests/test_codex_feedback_service.py tests/test_one_artifact_field_audit.py &&
python3 scripts/configure-redux-codex-feedback.py --enable --approve-sanitized-model-data &&
python3 scripts/redux-one-artifact-field-audit.py --audit-once --approve-one-ready-artifact-read-only
```

The first service option configures **future** source-only Codex missions
to receive the bounded, schema-validated feedback from an already-saved
Pi metadata report (when one is fresh). It does not run SSH or enable RF.
The second option runs **exactly one** physical integrity check. The
command may return a nonzero code for UNKNOWN/ATTENTION; that is a valid
stop condition, not a reason to add sudo or repeat the audit. Only the
sanitized one-shot JSON output should be relayed to ChatGPT.

For off-line contract disclosure:

```bash
python3 scripts/redux-one-artifact-field-audit.py --plan
python3 scripts/configure-redux-codex-feedback.py --plan
```

Turning filtered Codex feedback off later is always allowed and does not
affect field observations:

```bash
python3 scripts/configure-redux-codex-feedback.py --disable
```

GitHub CI and synthetic tests validate logic and response shape, not
the permissions of the Pi's deployed capture filesystem or actual
byte integrity. **Do not claim a hardware integrity PASS until a real
one-shot report returns `verified_bytes` and is independently reviewed.**


## October 21 first real run — ATTENTION before any hash

The operator ran the single authorized attempt from WSL and reported that
both targeted sets of tests passed (26 tests), then the Codex feedback toggle
returned `sanitized_model_feedback=on`. The Pi audit returned:

```json
{"status":"attention","reason":"artifact_integrity_problem",
 "checked":0,"ready_rows":0,"physical_acceptance":"UNKNOWN",
 "handshake_authenticity":"UNKNOWN"}
```

**Interpretation:** The first protocol obscured a failure *before* any file
was verified. With zero checked files, the result is **not proof that the
ledger really has zero ready entries** and is **not proof of corruption**.
The result's zeros are also default counters when early preflight or SQLite
fails. It is unsafe to treat this as a failed hash.

Source review found a likely permissions blocker. The installed
`systemd/redux-capture-ingest.service` uses `User=root`; the ingestion
worker sets `/captures/ready` to mode **0700** and
`/captures/jobs.db` to mode **0600**. The one-shot audit purposely uses
the unprivileged `pi` identity with **no sudo**. Unless those files are
owned by `pi` or have independent read grants, it cannot inspect them.
The field metadata reader does not require opening those protected files,
so its prior success does not prove artifact audit access.

The audit source now explicitly tests for read/search permissions after
REDUXCAP verification, returning **UNKNOWN /
`read_permission_unavailable`** when needed. It also maps unexamined
schema/output-directory errors to specific UNKNOWN reasons rather than
misleading generic artifact-integrity ATTENTION. These are **source-only**
repairs; they have not been rerun against the Pi. The original one-time
attempt marker remains consumed. Do **not** delete it, repeat the previous
SSH audit, change directory permissions or add sudo under that authorization.

Before any further real prepared-file inspection, independently review a
new **separately authorized** minimal privileged *read-only* audit profile
with fixed code, verified Pi identity, bounded file reads, no capture
export, no automatic retries and no persistent Pi changes. Do not grant
unrestricted model/root shell access. The separate hourly metadata observer
continues unaffected and can still supply aggregate ledger-status categories
to the now-consented Codex source-only lane.
