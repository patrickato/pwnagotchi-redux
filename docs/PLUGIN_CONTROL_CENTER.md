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

## Installed image integration (Sprint 09)

New images install the reviewed first-party catalog at `/usr/share/redux/packs`.
The local CLI, web provider and framebuffer status share that default; explicit
`--dir`/`--state` and `REDUX_PACKS_DIR`/`REDUX_PACKS_STATE` remain available for
host development. An explicitly selected development catalog keeps the legacy
catalog-local state default unless a state override is supplied.

On the installed system, no TOML editing or environment setup is needed:

```
redux packs list
redux packs enable field-diagnostics
redux packs enable field-diagnostics --confirm REVIEWED_PREVIEW_REVISION
```

The writable default is `/captures/config/packs.json`. Preview/inventory never
create it. A confirmed local request creates private directories and atomically
persists private state only after the existing writable captures mount and
REDUXCAP block identity are verified. Mount-generation changes stop requests;
directory-relative writes cannot fall through a replaced path onto root storage.
Permission failures are reported; the CLI never invokes sudo. On a system where
only root can administer this state, use an explicitly approved local privilege
path. Web/TFT remain inspection-only and do not create directories or files.

The catalog's `field-diagnostics` entry describes real base-image commands,
`redux-boot-check` and `redux-display-diag`. They are manually invoked tools,
not automatically loaded plugins. Changing requested state does not run or
schedule these commands, change a radio, or restart a service. Active runtime
state remains unverified. No third-party plugin is bundled. More native packs
can be added as reviewed manifests; legacy adapters retain static inspection and
all existing dependency, configuration and side-effect authorization gates.

Inventory refreshes are shared and limited to once per ten seconds. At most four
50-entry pages are cached. The live status/TFT contract carries only twelve
compact requested-state summaries, without option schemas; full schemas remain
on the paginated authenticated inventory endpoint. This avoids breaking the
framebuffer's 64-KiB response budget with large catalog options. A failing plugin
producer leaves runtime/Doctor/worker/telemetry status available. Canonical System
details and a supplemental Doctor-page notice identify inventory failures without
rewriting Doctor's producer assessment or implying healthy hardware.

The image stage records its installed package inventory after all stages' runtime
dependencies have been installed. Export verification now requires the plugin
modules, reviewed catalog, diagnostic launchers and native display imports, plus
Python, Pillow, D-Bus transport, Bettercap, iw and hcxtools package records. The
existing root validator and extraction path remain the single image contract.
Historical Sprint 07's WAL connector test failure was corrected by `b6ed0aa`;
it is not a remaining plugin defect.

Installed-root tests execute the real source CLI from extracted-style temporary
roots and run the real image stage shell scripts with chroot commands recorded,
never executed. They cover cold boot defaults, missing/corrupt state, failed and
recovered persistence, mount loss, authenticated API and framebuffer parity,
missing optional producers, bounded schemas and preservation across immutable
catalog replacement. This is software evidence, not a real boot or TFT/RF test.
No Pi connection or deployment is part of this integration.

Sprint 09 delivery validation, after preserving the integrity owner's commits
through `e59fc9b`: 3,202 Python tests and 1,107 image tests passed, with zero
failures/skips (one existing PyGI deprecation warning). The focused installed-root,
export/factory, browser and framebuffer selection passed 375 tests. All 24 unified
qualification checks passed, including compilation and shell syntax. Both staged
and unstaged whitespace checks passed. Nineteen new installed-integration tests
exercise the real copied sources and stage scripts without privileged execution.
Physical boot, captures preservation, actual plugin activation and TFT/RF operation
remain separate hardware gates; no real image or deployment is implied.

## Trusted native runtime (Sprint 10)

The installed catalog now includes `resource-summary`, a read-only native plugin.
It consumes the supervisor's **existing** `ResourceMonitor` observations over
`Signal.RESOURCE_OBSERVATION`. Its identifiable output contains executed
observation count, original measurement time, freshness and available CPU
temperature/load and RAM utilization. It never reads hardware, queries a ledger,
changes a radio, or treats missing battery/power readings as measurements.
`field-diagnostics` remains a manual command/tool pack; it has no daemon activation.

The shipped `redux.packs.registry` is the trust boundary, backed by the verified
image/source revision. It pins both the reviewed manifest bytes and implementation
SHA-256, and uses a fixed first-party import. An arbitrary manifest/module name,
legacy adapter, imported Python file or self-provided digest cannot authorize
execution. This is **not a sandbox**. Adding a native implementation requires a
source review, registry entry and updated image/lifecycle tests. Currently only
`resource-summary` is supported, with no configuration options or elevated/hardware
requirements. Legacy settings remain preserved by the manager but are not passed
to this implementation.

### Local activation workflow

On a future approved installed-system session:

1. Inspect `redux packs list`. `native.eligible` describes registry qualification,
   independently of requested and actual active state.
2. Run `redux packs enable resource-summary`, review the returned preview, then
   repeat with `--confirm REVISION`. Existing atomic state/backup and mount guards
   apply. This command alone does not start anything.
3. The reviewed live owner must be deliberately enabled with
   `[live] enable_native_plugins = true` in the live service configuration.
   The shipped default is **false**. Applying that configuration to a Pi and
   restarting its service require separate physical authorization; no CLI control
   or web request silently restarts services.
4. Inspect `redux packs runtime` (optional `--url http://127.0.0.1:PORT/api/status`)
   for current live-process evidence. This bounded, proxy-free, no-redirect reader
   uses the existing loopback status endpoint. The authenticated browser also
   exposes evidence at `/api/packs` and `/api/status`; remote writes remain absent.
5. Disable using the existing preview/confirmation CLI workflow. The enabled
   owner reconciles catalog/request changes at most every ten seconds, stopping
   prior instances in reverse dependency order before replacing them. No disk
   writes occur during native execution. The inventory itself has a ten-second
   inspection cache; its sample age remains visible.

The live checkpoint reads the worker's cached inventory without filesystem scans.
The worker publishes starting, confirmed active, degraded, stopped or unavailable
states. Successful `start()` completion is necessary for active evidence; the
request revision is checked again after startup. Evidence carries an unpredictable
current owner generation, exact requested-state digest, manifest and reviewed
implementation identity. Only an in-process owner supplier is accepted by the
inventory integration: saved checkpoints are never loaded as active proof. A new
process starts without active evidence. CLI `packs list` remains offline manifest
inspection; `packs runtime` explicitly asks the running owner. Requested-state
changes may take up to the bounded reconciliation/inspection interval to appear.

The radio loop only replaces one pending telemetry projection. Manifest IO and
plugin callbacks execute on a daemon worker; repeated start does not add duplicate
subscriptions. Shutdown detaches subscriptions immediately and joins for at most
250 ms. Callback failures are isolated and redacted, with no automatic same-request
retry after a plugin failure; a changed request revision or fresh owner is needed.
Python cannot forcibly interrupt a hung trusted callback. A callback exceeding one
second loses fresh active evidence and is quarantined after returning, and a worker without a completed heartbeat for
five seconds is stale. Shutdown of a hung callback is reported as degraded, not
successful. Other plugins share this worker; that bounded isolation does not make
untrusted code safe. Sensor freshness remains separate from worker freshness.

Doctor adds a native-plugin finding only when this owner is explicitly enabled.
Unknown/stale evidence remains UNKNOWN; failed requested native activation is
DEGRADED. Tool packs are excluded from daemon expectations. The native System page
and authenticated web inspection consume the same compact inventory. A missing
REDUXCAP prevents the installed supervisor starting (existing storage safety);
a later mount loss stops native instances and never creates fallback state.

Host tests execute the shipped implementation in source and copied installed-root
fixtures, assert output and cleanup, and exercise loopback HTTP, missing radio,
corrupt state, startup revision races, failure/timeout isolation and restart
freshness. These are software results, **not physical Pi acceptance**. No Pi
startup, actual sensor behavior, framebuffer/touch operation or radio behavior is
certified by these tests. No real image was assembled during this sprint.
