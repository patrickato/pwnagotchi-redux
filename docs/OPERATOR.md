# Autonomous glass-box kill-chain operator (`redux/operator/`)

## Why

The frontier right now is LLM-orchestrated attack chains that pivot on their own.
redux takes the opposite, honest line: a **deterministic** operator that sequences
recon → capture → crack → network-pivot (scan → cred-test → loot) where every step
is explainable and gated — **no model improvising about who to hit.** It's the
conductor the offensive suite was missing, and it's your "chain recon→capture→
crack→pivot" obsession wired into one operator.

## The three gates (every firing step)

1. **Posture** — offense must be enabled. A detection-only persona runs recon only.
2. **Scope** — the target must be authorized in the central Scope (aiming).
3. **Capability** — the capability the step needs must actually be present (e.g. a
   capture engine for the capture step). No pretending.

If any gate fails, that target's branch stops there with the reason, and downstream
steps are shown as contingent — you can't crack what you couldn't capture.

## plan() vs run()

- `plan(targets)` is a **dry run**: it returns a `Step` per phase/target with a
  reason, showing exactly what it would do and why each blocked branch is blocked.
  Nothing executes.
- `run(targets, executors, recon=…)` executes allowed steps via **injected
  executors** (`executors[phase](target) -> (ok, result)`), so it's testable with
  no radio and no tools. It advances a target's chain only while the previous step
  actually succeeded, stops on a failed or missing executor, and records every
  executed, *targeted* step as an action.

## Feeds the engagement report

`run()` returns a `log` of `EngagementAction`s (passive recon excluded — it isn't
aimed at a scoped target, so authorization-checking it would be a false flag). Pass
that straight to `redux.report.build_report`: an autonomous campaign produces a
chain-of-authorization report, and because the operator gates out-of-scope targets,
that report comes back **CLEAN**.

## CLI / Augur

- `redux campaign plan --persona red --scope-file … plan --targets …` — the gated plan.
- `redux campaign … demo --targets …` — runs with fake executors and renders the report.
- `Augur.operator()` binds the operator to the live Scope + posture +
  capability graph (so the capture-engine gate reflects the real device);
  `campaign_plan()` / `run_campaign()` wrap it.

## Honesty

Deterministic and explainable by construction; a blocked or failed step stops the
branch and says why; passive recon is never authorization-flagged; nothing is
invented. The real executors (capture_plan, crack, netrecon) are injected on-device
— the operator itself never needs hardware to plan or to be tested.
