# TFT UI — getting to everything from the 3.5" screen

The native framebuffer UI (`redux/display/fbui.py`) is a read-only, cool-running
status display refreshed on a ~2s cadence (and the framebuffer is only written
when the image actually changes). This expands it so the whole toolkit is
reachable and legible on the panel, without changing that cadence or adding
per-frame cost.

## Pages

The page registry (`ui_state.PAGES`) is an unbounded list; the footer tab bar
slides a 5-slot window over it, so new pages are reachable by swiping. Added:

- **menu** — a "map of everything": a 2-column grid of capability groups
  (STATUS / RECON / CRACK / SDR·RF / OPS / SYSTEM) listing each tool. Actions run
  via CLI/web; this makes them all discoverable from the screen. Static draw.
- **power** — the power-tier safety view: in LAB it shows the strong tools
  unlocked (each needs a confirm); in FIELD it shows them hidden, and the
  FORBIDDEN items (jamming, beacon-spam) in red. Pure from `power_tier` + mode.
- **signals** — the Signal Dex count (total + by medium). Its data is sampled
  **only while the page is on screen, at most every 15s**, so the catalog read
  never touches idle heat/CPU; absent a sample it shows a clean hint.

## Heat / resource discipline

- No new per-frame work: `menu`/`power` are cheap text+rect draws from data
  already in hand; the existing render cadence and change-only framebuffer write
  are untouched.
- The one data-backed page (`signals`) samples lazily and slowly, guarded by
  try/except, and fails safe to a hint — it can't peg CPU/RAM or spin the DB.

## Still a bench item

Final visual tuning and interactive tile-tap navigation are best iterated on the
physical panel. The render functions are pure and were eyeballed by rendering to
PNG, but the real 480×320 panel is the judge.
