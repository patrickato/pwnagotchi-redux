# Unified local qualification

Run from an Ubuntu/WSL checkout on `human/redux-workbench`:

```bash
python3 scripts/dev-preflight.py
python3 scripts/redux-qualify.py
```

The default qualifier performs no SSH. It detects dependencies using the current
Python interpreter, tests executable tools, then runs the complete pytest suite,
the existing `image/ci.sh` runner, Python compilation, tracked shell syntax and
`git diff --check`. Each regression gate runs even if an earlier gate fails.
Missing required tools block regressions explicitly. The image runner continues
to own its test list; there is no second copied image test list in the qualifier.
Output has concise PASS/FAIL/UNKNOWN verdicts, exit codes, test totals and a
bounded local failure tail. A successful offline run is software evidence only.
`--preflight-only` detects tools without running regressions and records those
regressions as UNKNOWN. It cannot silently qualify untested source.

The scripts never install dependencies or run sudo. For a new development
machine, create a project-local environment after approving downloads:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,crypto]'
.venv/bin/python scripts/redux-qualify.py
```

Ubuntu host packages needed are `python3`, `python3-venv`, `build-essential`,
`binutils`, `shellcheck`, `git` and `bash`; the operator can install these with
their normal approved package-manager workflow. Python requirements include
pytest, dbus-next, Pillow and cryptography (the repository's dev/crypto extras).
Node.js is optional for static Python contracts, but required to execute the
headless browser behavior and JavaScript syntax tests. Its absence is UNKNOWN;
a found but broken runtime is FAIL. No global Python environment is modified.

Pytest creates an owner-writable disposable Python cache and passes it to child
processes. The image runner creates its own disposable cache if none is set.
Qualification also isolates compilation caches. Old root-owned `__pycache__`
directories in the checkout do not need deletion or chmod for these checks.

## Optional read-only field observations

```bash
python3 scripts/redux-qualify.py --field --host <trusted-private-IPv4> --key ~/.ssh/redux_field_ed25519
```

This runs the same offline gates, then exactly one bounded status probe through
`field-status.py`. `--country US` may record the operator's expected physical
country but never sets the radio country; regulatory state remains UNKNOWN when
not measured. Defaults match the existing field-status target/key; explicit
options support another already provisioned trusted machine.

The SSH connection is batch/public-key-only with `StrictHostKeyChecking=yes`,
explicit identity, disabled password/interactive authentication and forwarding,
a connection deadline, liveness limits and a 20-second process deadline. It
runs standard-library Python with bytecode writing disabled. It reads only the
Pi's local status API with proxies disabled and a three-second/one-MiB response
limit. No remote sudo, installation, configuration, host-key trust change,
service/radio command, capture read or persistent Pi write is performed.

Only allowlisted enums, timestamps, counters and bounded numeric measurements
leave the Pi. Full status JSON, reasons, paths, SSIDs, MACs, credentials and
capture material are withheld. Source timestamps are checked independently;
a cached `fresh: true` cannot authorize an old measurement. Missing battery
and power-source measurements stay null. Degraded/action Doctor findings fail
qualification; a report's existence is distinct from its health.

`field-acceptance.status_verdicts()` reuses the existing acceptance policy for
this sanitized snapshot. Runtime readiness and packet link type are assessed
only with a fresh checkpoint. Systemd, ext4, regulatory, rfkill, monitor mode,
RF reception and other unmeasured facts remain UNKNOWN. Counters and link type
do not prove decoded frames or handshake conversion. The legacy
`field-acceptance.py` full-report command remains available separately; its
sudo/full diagnostic output path is **never called** by this workflow.

Exit codes: 0 all requested checks pass; 1 at least one observed failure;
2 requested field transport/arguments unavailable; 3 unknowns with no failure.
A field transport error returns 2 while retaining the local gate results.
Preflight-only and old images with absent telemetry cannot yield an all-green
qualification. For standalone status without regressions, `field-status.py`
remains supported and uses the same sanitizer and health policy.

## Software and physical evidence

The browser distinguishes configured capture paths from verified files. A path
is labeled `Configured capture path (existence unverified)`; no stat or RF claim
is inferred from engine startup. Resource freshness and provenance are displayed
separately from runtime heartbeat and visual sample freshness. A stalled visual
revision is marked stale even while checkpoints keep arriving; stale map/airspace
samples are not redrawn as new observations. Unknown Doctor areas stay visible.

Resource sampling and Governor cooldown survive engine connection replacement
through one shared `ResourceMonitor`. The runtime samples even while no radio
is usable; neither monitoring nor Doctor changes capture scheduling or executes
radio actions. Firmware masks are schema-checked and battery-source booleans
must be real booleans. This is source-tested behavior, not a Pi qualification.
Real Pi sensor/firmware permissions, screen readability/touch and end-to-end RF
capture/conversion remain separate hardware gates. No deployment is included.

## Verified source run — 2026-10-18

On the integrated workbench based on `8ce3c6a`, the final offline
`python3 scripts/redux-qualify.py` run returned **0**: 16 PASS, 0 FAIL,
0 UNKNOWN. The environment supplied standalone Node.js 22.22.1 under `/tmp`
through PATH; no global Node/Python installation or Pi access was performed.

- Complete `python3 -m pytest -q -o addopts=`: **2,670 passed**, exit 0.
  The `addopts` override avoids doubling the repository's default `-q`, so
  totals remain visible. The one warning is the host PyGI deprecation of
  `GLib.unix_signal_add_full`, not a Redux test failure.
- `bash image/ci.sh`: **734 passed**, exit 0, including shellcheck.
- Targeted telemetry, Governor, Doctor, Augur, field-status, qualification and
  headless dashboard contracts: **128 passed**, exit 0.
- Compilation of redux/scripts/boot/image, tracked shell syntax and
  `git diff --check`: exit 0.

An earlier invocation inside the restricted tool sandbox:

```bash
python3 -m pytest -q tests/test_field_status.py tests/test_field_acceptance.py tests/test_resource_telemetry.py tests/test_live_runtime.py tests/test_web.py --tb=short
```

hit `PermissionError: [Errno 1] Operation not permitted` in local socket tests:
`test_local_doctor_dashboard_starts_even_when_capture_radio_is_missing` and
seven web tests (`test_runtime_doctor_report_is_transmitted_unchanged_through_local_api`,
`test_handler_serves_status_and_page`, `test_serve_loop_bounded_updates_snapshot`,
`test_api_requires_token_when_set`, `test_non_ascii_token_401s_not_crashes`,
`test_page_shows_login_until_token_then_dashboard`, `test_open_handler_needs_no_token`).
The final local run allowed test sockets and passed all of them. These were
sandbox restrictions, not unresolved software regressions. No field run,
physical measurement, installation, image build or RF qualification is claimed.

Sprint 02 adds the separate [offline release candidate, RAUC readiness and
recovery gate](RELEASE_QUALIFICATION.md). The default preflight reports its host
parser requirements, and image CI runs both policy and real-artifact tests.
Release qualification does not imply Pi boot, installation or automatic rollback.
