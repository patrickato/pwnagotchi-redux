# Experience layer — learning from what actually worked (`redux learn`)

The device now keeps a real memory of outcomes and can tell you — honestly —
what has worked and what to try next. This is the "smarts," built to the
project's standard: **real data, glass-box, exploration-preserving, and never
over-claiming.**

## Why it's honest, not hype

The Brain is deliberately *not* a channel-yield reinforcement learner — we
proved in sim that a learning policy doesn't reliably beat greedy+random at
channel selection, so we never sell "more handshakes." The experience layer
keeps that discipline:

- It records **real outcomes**, one per attempt — never fabricated.
- Every statistic carries its **sample size** (`6/8 captures`, not a bare
  `75%`), and small samples are flagged (`very low confidence`).
- Ranking is **exploration-aware** (a UCB bonus), so an under-sampled or untried
  option is *lifted*, never starved — the opposite of greedy lock-in.
- An untried option reports the **prior** (0.50), not a made-up 0 or 100%.

## One honest outcome per attempt

The trap: logging only successes makes every rate 100%; logging an attempt *and*
a success double-counts. So the `CaptureRecorder` **correlates** them — each
lab-mode offense attempt against a BSSID is held pending with its context; a
handshake/PMKID for that BSSID within a window resolves it to a success,
otherwise it resolves to a miss. Every attempt produces exactly **one** row, so
`6 captures / 8 attempts on channel 6` is a real rate.

This runs only in **LAB mode** (where offense transmits); FIELD records nothing
because it fires nothing.

## CLI

```
redux learn summary                              # totals, success rate, outcomes
redux learn stats --dim channel|band|hour|vendor|kind
redux learn rank  --dim channel --candidates 1,6,11
redux learn reset
```

```
$ redux learn rank --dim channel --candidates 1,6,11
  channel=11: untried — prior 0.50, worth a look (explore)
  channel=6: 6/8 captures (75%), moderate confidence; score 1.38
  channel=1: 1/9 captures (11%), moderate confidence; score 0.83
```

The untried channel sorts first (explore it), then the productive one, then the
poor one — and you can see exactly why.

## Wired into the Brain

The Brain takes an optional experience store and enriches its orchestration
reasons with honest history — e.g. *"productive spot, keep hunting (history:
6/8 captures at this hour)"*. It's **advisory only**: it never changes which
intent is chosen (the proven greedy behavior is untouched), it just makes the
reason richer. The live runtime wires the store at
`/captures/live/experience.jsonl` automatically.

## Dimensions

`channel`, `band`, `hour` (of day), `vendor` (populated once OUI enrichment is
merged), and `kind`. Add context freely when recording; unknown dimensions are
simply ignored in aggregation.
