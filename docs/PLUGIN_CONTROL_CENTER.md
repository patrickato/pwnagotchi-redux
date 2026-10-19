# Plugin Control Center

The existing PackManager owns discovery, dependency resolution and requested
configuration. It never installs, imports, activates or restarts plugin code.
The Python PluginHost remains a trusted-code execution interface, not a sandbox.

Use `python3 -m redux packs --dir CATALOG --state STATE list` for redacted
inventory. On an image, keep the reviewed catalog in immutable `/opt/redux/packs`
and create the state parent on the writable captures partition, for example
`/captures/config/packs.json`. No image service is enabled by this workflow.

To request a change:

```
python3 -m redux packs --dir CATALOG --state STATE enable NAME
python3 -m redux packs --dir CATALOG --state STATE enable NAME --confirm REVISION
```

The first command is a preview. The confirmation binds current state, complete
manifest bytes and requested changes. `disable` cascades to dependents;
`configure NAME --options '{"interval":30}'` validates typed options.
`rollback NAME` previews the preceding atomic state snapshot, then requires its
own confirmation. Enable requests declaring side effects additionally require
`--authorize-side-effects` on both commands. This authorizes only the request;
actual activation is UNKNOWN and remains pending a separately approved pathway.
Never put secrets in command history; use a protected local invocation instead.

State is mode 0600, atomically replaced under an advisory writer lock, with an
atomic `.previous` snapshot. Interrupted pre-replacement writes preserve the
old state; startup ignores orphan temporary files. Invalid state is preserved
and reported, never silently replaced with empty state. Unknown legacy state
and settings survive edits but are neither executed nor included in inventory.
After filesystem errors inspect state and backup before requesting another edit.

Manifests accept conflicts, requirements, side_effects and typed `options` with
boolean, string, number, enum and path schemas. Paths remain below
`/captures/plugins`. Unqualified platform/runtime `requirements` prevent enable
requests. Native manifests are unverified runtime descriptions. Legacy Python
needs explicit `adapter="pwnagotchi-shim-v1"`, relative `entrypoint` and exact
`sha256`. Static AST checks reject unsupported hooks/signatures and known agent/UI
API mismatches. This limited contract cannot prove arbitrary Python safe or
working; imports and physical operation are never compatibility tests.

Set `REDUX_PACKS_DIR` and optionally `REDUX_PACKS_STATE` in the existing web
process environment to expose `/plugins` and `/api/packs`, with existing token
protection. Inspection is cached ten seconds, reports age/provenance and isolates
inventory failure from runtime status. Remote writes are unavailable. The
canonical status adds `plugins`; framebuffer System details summarize requested
state and explicitly unknown activation. No separate framebuffer scanner runs.
Discovery has a 250 ms budget and reports incomplete catalogs rather than
allowing changes against partial evidence. Individual inputs/state are bounded
to 64 KiB. Inventory pages contain up to 50 entries and expose total/next offset;
large catalogs can be inspected through `PackManager.inventory(offset, limit)`.

Software tests cover deterministic lifecycle, interrupted storage, stale
previews, malformed manifests, secrets, static adapters and authenticated web
inspection. Physical touchscreen, plugin runtime activation, platform support
and RF behavior remain separate acceptance gates. No third-party code is bundled.

For additional catalog pages, use `list --offset 50`, then the reported
`next_offset`; web inspection provides a Next plugins link. Standalone legacy
`.py` files without an adaptation manifest are reported as incompatible and are
never imported. A malformed plugin cannot suppress inspection of valid siblings.

The v1 static adapter requires an explicit `from redux.compat import Plugin`
base class. Its import allowlist is intentionally narrow (`redux.compat`,
`logging`, `math`, `time`); other imports require further adaptation rather than
an optimistic compatibility label. `adapted` describes this static contract,
not successful initialization, safe execution, supported hardware or activation.

## Sprint 07 handoff evidence

The final offline run executed 3,133 Python tests: 3,132 passed and one failed;
none skipped. Image CI passed 1,049 tests. The focused plugin/CLI/shim/canonical
UI selection passed 145 tests, including 45 new plugin-control cases.
Compilation, shell syntax and whitespace checks passed. Unified qualification
therefore exits 1, rather than masking the remaining failure.

The failure reproduces independently with:

```
python3 -m pytest -q tests/test_capture_artifact_audit.py::test_wal_writer_cannot_split_ready_count_from_rows
```

That capture-integrity-owned test defines a SQLite wrapper whose first parameter
is named `uri`, then receives both a positional database URI and `uri=True`.
It raises `TypeError: ... got multiple values for argument 'uri'` before auditing.
The owner should correct that wrapper and rerun full qualification. Sprint 07
preserves those independently maintained files without modifying them.

Next engineer: start with `python3 scripts/redux-qualify.py`, inspect a reviewed
local catalog with `packs ... list`, then exercise preview/confirmation using
only disposable writable state. Configure the web inventory environment only
through a separately reviewed deployment. Runtime activation remains absent:
do not infer activation from persisted requests or add automatic imports/restarts.
Physical touch/display behavior and actual Pi plugin operation still need a
separately authorized hardware session. This sprint made no Pi connection.
