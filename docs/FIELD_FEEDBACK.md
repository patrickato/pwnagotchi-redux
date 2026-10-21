# Redux field feedback — private saved observations to source engineering

**Status:** Source code committed; isolated Linux fixture tests run; **not yet
enabled for Codex ingestion**. The approved hourly field bridge runs
independently and has not been changed by this feature.

## The minimum necessary feedback

The first hourly read-only Pi collector stores private 0600 reports at
`~/.local/state/redux-field-bridge/session-<random>.json`. These contain
more information than an AI needs to prioritize source-level repairs.
Never copy raw reports or SSH key material into GitHub, chats, issues, CI
artifacts or LLM prompts.

`scripts/redux-field-feedback.py` instead selects the newest owner-only,
regular 0600 report under a verified owner-only 0700 directory. It calls the
**existing offline** `redux-acceptance.py --json session-inspect` against
those *already-saved bytes*. It does not contact the Pi, scan wireless
traffic, read captures, invoke sudo, or change services.

`redux.field_feedback.facts` then converts that verified inspection
projection to a bounded, fixed-category JSON record. It exposes only:

- Radio ordinal, onboard/USB transport, monitor/managed label, numeric
  ARPHRD link type, measured NetworkManager boolean/state and bounded
  potential-owner count.
- Enumerated TFT readiness and predefined display blockers.
- Worker availability/freshness *at the observation time*, cumulative
  processing ledger status counts and most recent completed scan outcomes
  (when counter totals validate).
- A coarse capture-storage free-space band, predefined live-state category,
  and fixed engineering priority signals.

No MAC addresses, SSIDs, private IPs, interface names, paths, filenames,
serials, raw caplets, packet data or credentials are included. Free-form
strings are not copied. Storage capacity is bucketed rather than exact.

The result always says `physical_acceptance: UNKNOWN` and
`release_qualification: UNKNOWN`. A 1/4 ready/invalid ledger is not a
handshake verification; USB radiotap framing is not decoded reception; an
old snapshot must not become a current Doctor PASS.

## Source-only test coverage

Synthetic fixture tests in `tests/test_field_feedback.py` and
`tests/test_feedback_cli.py` cover malformed projections, sneaked free-text
identifiers, inconsistent ledger counts, symlinked/untrusted report paths,
bounded subprocess invocation and missing evidence. The first isolated
Linux replay passed **16 tests**; that is not a substitute for the full
repository's CI or real Pi testing.

For later locally approved use, this command is **offline** and produces
only sanitized JSON when an existing private report is present:

```sh
python3 scripts/redux-field-feedback.py --latest-private
```

The user need not run it to keep the installed observer working. The new
code is available on the GitHub workbench but pulling the WSL checkout will
change its pinned revision and may pause the currently approved observer.
Treat that as a deliberate upgrade requiring the original approval policy
to be reviewed/renewed; do not bypass source-revision pinning.

## Codex ingestion is separately optional

The source-only Codex wrapper now has an **explicit opt-in** flag
`--with-sanitized-field-feedback`, separate from `--enable-agent`. It
accepts a successful fixed-format projection via the local offline
converter and appends it to one Codex task as *untrusted categorical evidence,
not instructions*. The AI still has **no SSH/RF/service/card authority**.
The existing user timer does not pass this new flag and does not transmit
these observations to Codex merely because the bridge is running.

Once the owner separately enables that feedback option, Codex's cloud model
will necessarily receive **only the filtered hardware categories** in its
task prompt. Do not interpret the earlier read-only collector approval as
blanket permission to upload raw Pi telemetry. Do not commit actual
observations to the shared GitHub branch, even when the repository is private.

## Next integration gates

1. Verify at least one real hourly observer result on WSL (the timer is
   installed but current execution is not independently visible from chat).
2. Run targeted feedback tests and full WSL/CI tests. Review that the
   converter never returns free-form user/device data.
3. After consent and a safe planned local runner upgrade, connect the
   sanitized prompt feed into the existing Codex timer. Keep the owner-only
   SSH key inaccessible to the Codex workspace-write subprocess.
4. Build a future private evidence-return channel for the chat-side lead
   with a separate explicit publication policy; do not invent a direct
   ChatGPT-to-LAN connection or silently push telemetry to public GitHub.

No additional physical authorization, capture-content access, service
restart, RF operation or image deployment is conferred by this source work.

## October 21: unattended collection liveness and freshness gate

The owner successfully migrated the scheduled read-only Pi observer into a
private **frozen, exact-revision worktree**. The existing approval, hourly
timer, and remaining-attempt count were preserved. One earlier timer trigger
was visible, but no `last-status.json` was printed, so an actual successful
SSH-backed collection remains **unverified**. A systemd timer firing is not
the same as verified collection.

The mutable workbench now ships an **offline field doctor**, invoked through:

```sh
bash scripts/install-redux-field-bridge.sh --status
```

It inspects only local systemd and owner-private policy/status files; it does
not SSH, initiate another collection, read captures or return private hosts,
keys or raw session records. It explicitly distinguishes an expected hourly
service/timer, approved session counts and expiry, a previous saved
collection attempt, and the local systemd result code. A missing last result
is `never_recorded`, **not** success or implicit failure of RF reception.
After the frozen migration, normal source pulls no longer change the
approved observer's code.

Even when an existing `session-<random>.json` report is available, the
private feedback converter refuses records older than **two hours**, or
more than five minutes in the future. This prevents stale physical metadata
from silently driving new Codex work. It never overwrites, removes or
rotates private hardware evidence. The ongoing observer scope is unchanged:
hourly, at most 40 attempts, valid for 14 days, read-only only.

Before opting into model ingestion, confirm one actual `observed` local
status and recent private report using the offline doctor. The existing
Codex timer does **not** upload Pi observations by default.
