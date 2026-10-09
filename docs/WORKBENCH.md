# Redux Workbench — Independent Development Line

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
