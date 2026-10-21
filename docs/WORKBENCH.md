# Redux Workbench — Independent Development Line

Sprint 06 first-boot and hardware evidence tooling is documented in
[HARDWARE_ACCEPTANCE_KIT.md](HARDWARE_ACCEPTANCE_KIT.md). Offline source/image
qualification and externally attested physical observations remain separate.

This branch is the project's **active, AI-assisted engineering workspace**.

## Why this workspace exists
The owner wants substantial, iterative development with freedom to add,
reorganize, replace, remove and refine modules without treating every round
of work as a single accumulating pull request against the original project.

## Snapshot and boundaries
- **Working branch:** `human/redux-workbench`
- **Seed:** `human/redux-capture-pipeline-integration` at the point this
  workbench was created, including the previously completed capture and
  standalone-image infrastructure.
- **Original protected branch:** `main` in `patrickato/pwnagotchi-redux`.
- **Historical integration branch:** retained unchanged as a recovery snapshot.
- Work on this branch **directly**, with descriptive commits and tests.
- No default merges or PRs back into `main`. The owner explicitly controls
  whether any components are later promoted to the original project.
- Preserve working builds and historical commit access; significant rewrites
  should be independently testable and documented.

## Current readiness
- Source code and automated CI coverage exist for many subsystems.
- Full standalone ARM64 image build and all hardware gates are **not**
  considered completed merely because source CI passes.
- `docs/PROJECT_SCORECARD.md` tracks approximate full-project progress.

## Engineering practices
- Develop in meaningful feature sets and verify them with relevant regression
  tests, full CI where available, and explicit Pi 4/5 hardware gates.
- Real observations only: never pretend replayed events or synthetic tests
  are measured device data.
- Capture and security-sensitive operations remain scope-authorized with a
  passive default; no unsolicited RF transmission.
- No automated raw-capture deletion. Retention requires explicit operator
  invocation and verified records.
- This working branch can become substantially different from `main`.
  That divergence is intentional.

This workbench is an independent **branch**, not a new GitHub repository.
If full repository separation is wanted later, move this branch into a
new repository while keeping the original project unmodified.

## Resource telemetry

The source-only read-only collector now feeds Augur/Governor and exposes shared
freshness/provenance to Doctor and the live dashboard. Missing/stale sensors
remain unknown; battery/power-source measurements are unavailable. See
[HARDWARE_TELEMETRY.md](HARDWARE_TELEMETRY.md) for sources, status fields and
hardware qualification limits. No Pi deployment or RF qualification is implied.

## Unified qualification

`python3 scripts/redux-qualify.py` runs local development preflight and the
existing regression/image contracts with isolated Python caches. `--field`
adds only an unprivileged, sanitized, read-only status probe; unavailable
physical facts remain UNKNOWN. See [REDUX_QUALIFICATION.md](REDUX_QUALIFICATION.md).

## Release, update and recovery framework

`python3 scripts/redux-release.py` provides offline candidate qualification,
RAUC snapshot readiness and recovery planning. It performs no SSH, mounts,
update installation or boot-state writes. See
[RELEASE_QUALIFICATION.md](RELEASE_QUALIFICATION.md) for exact evidence boundaries,
host tools, exit codes and remaining Pi 4/5 rollback acceptance gates.

## Shared UI evidence and navigation

The read-only System workspace now shares a bounded evidence contract between
HTTP and native framebuffer renderers. It exposes independent sensor, visual and
heartbeat freshness, provenance and unknown coverage. Native navigation uses an
extensible sliding footer with 44-pixel targets. See [UI_OBSERVABILITY.md](UI_OBSERVABILITY.md)
for controls, software acceptance and remaining physical display/touch gates.

## Integrated beta handoff

Sprint 04 integrates bounded diagnostic failures/recovery, shared worker heartbeat
validation, canonical checkpoint/UI state and qualified touch reconnection.
`redux-qualify.py --beta` produces an executable acceptance matrix while keeping
physical RF/display and actual update/rollback UNKNOWN. Read
[BETA_HANDOFF.md](BETA_HANDOFF.md) before continuing development or proposing any
physical acceptance work. Offline qualification does not establish production readiness.

## Release candidate factory

`redux-release.py factory` now orchestrates clean-source image assembly through the
existing builder, native manifests/checksums and actual offline image/bundle gates.
Preflight, build, verify and inspect share one entrypoint, with new private attempts
and no overwrite of old outputs. External A/B signing credentials are mandatory;
no sudo, deployment or physical rollback claim is introduced. See
[RELEASE_FACTORY.md](RELEASE_FACTORY.md) for commands, outputs and honest limits.
