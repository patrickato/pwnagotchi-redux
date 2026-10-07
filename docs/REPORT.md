# Engagement report — the chain-of-authorization deliverable

## Why

It's the feature that makes a serious person trust the box. Everything redux
already records — the central Scope (what you were authorized to hit), the
glass-box reason on every action, the ATT&CK/PTES tags, the Dex findings, and a
purple Range coverage score — collapses into one report a pro can hand a client.

## What's built (`redux/report/`)

- `EngagementAction` — one recorded action (ts, action, target, reason, result).
- `build_report(...)` — aggregates actions + the live Scope into a structured
  report dict; `render_markdown(report)` renders the deliverable.
- `Beastcore.engagement_report(...)` — builds it from the box's live Scope and
  folds in the Dex summary as findings.
- CLI: `redux report demo` (a sample, including one deliberately out-of-scope
  action so you can see the flag), `redux report build --actions acts.json`
  (`--out file.md`, `--sanitize`).

## The integrity behaviour (why it's trustworthy)

The report **re-checks every recorded action against the Scope** and flags any that
wasn't authorized as ⚠ UNAUTHORIZED, with the header verdict going **FLAGGED**. A
report that would expose its own out-of-scope action is one you can trust; one that
quietly omits it is worthless. It aggregates real data only — it never invents an
action or a result, and N/A-style unknowns stay unknown.

Example (`redux report demo`): three in-scope actions pass; a stray `deauth`
against an unarmed AP shows ⚠ UNAUTHORIZED and flips integrity to FLAGGED.

## Sections

Authorization (the armed Scope, per job, with expiry) · Activity (every action
with its time, ATT&CK technique, PTES phase, target, authorization check, and
result) · ATT&CK techniques exercised · Findings (Dex summary) · Detection coverage
(purple Range, with gaps named).

## Sharing safely

`--sanitize` runs identifiers through the same ghost pseudonymizer as
replay/ghost: BSSIDs and SSIDs become stable pseudonyms (OUIs preserved), CIDRs
stay (networks aren't personally identifying), so a report can be shared or
submitted without leaking real targets.
