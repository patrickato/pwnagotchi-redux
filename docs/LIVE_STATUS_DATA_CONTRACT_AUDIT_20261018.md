# Redux live status: producer-to-consumer contract audit (2026-10-18)

Scope: audited `human/redux-workbench` production source, with no field-device mutation. This is independent of the concurrent hardware telemetry implementation.

## Confirmed sources and consumers

| Producer | Published field | Consumer | Contract / gap |
| --- | --- | --- | --- |
| `live_runtime._checkpoint` | `runtime.updated_utc` | Web `tick()` | Heartbeat is compared with previous version; 12-second frozen-checkpoint warning. |
| `live_runtime._checkpoint` | `runtime.visual_sampled_utc`, `visual_revision` | Web `paint()` | Visual render gating uses version; independently enforce visual freshness, since runtime heartbeat alone does not prove visual data refreshed. |
| `Augur.status` | `governor.mode`, `interval_scale` | Web/TFT status | Absent Governor decision currently appears as `mode: full`, despite reason `FULL — no readings yet`. Preserve UNKNOWN provenance rather than interpret FULL as evidence of healthy sensors. Concurrent Codex task owns this. |
| `Augur._doctor_inputs` | `governor=self._gov` | `Doctor.probe_thermal_power` | `_gov` starts None; `observe_resources` exists, but a production caller was not found. Concurrent Codex task owns wiring. |
| `live_runtime._checkpoint` | `runtime.capture_file` | Web `renderRuntimePanels` | Configured filename is rendered as `Active file`. Path is assigned during engine launch, before a handshake creates a PCAP. Rename displayed state to `Configured capture path` unless file existence/open/write is separately verified. Never infer written bytes from a planned path. |
| `live_runtime._checkpoint` | `runtime.health`, `health_unknown_areas` | Dashboard and local checkpoint | Derive from the same Doctor snapshot; unknown count must equal coverage.not_assessed length, and health must match doctor.overall. |
| `read_summary(jobs.db)` | `capture_processing` | Doctor and `renderPipeline` | Worker heartbeat and successful conversion are distinct. A recent scan with duplicate outcomes is evidence of liveness, not new handshake ingestion. |
| `_handoff` | `runtime.handed_off` | Capture runtime panel | Count means successful nonempty closed PCAP handoffs, not engine rotations. A five-minute rotation with no capture file is not necessarily loss. |
| Radio inventory worker | `radio_inventory` | Fourth radio UI | Samples independently from engine heartbeat and expires after 20 seconds; preserve per-source age/provenance. |

## Concrete field observation

On the authorized test Pi, Bettercap logged full WPA2 handshake events for `dad24`, ingestion ledger reported 2 jobs (one invalid, one ready) and the ready file contained one structurally valid 22000 record. Those are distinct claims; do not portray cryptographic verification as established. Current `active` directory was empty while `capture_file` held a prospective path. Rotation configured at 300 seconds, matching ~303-second engine cycles. Doctor ATTENTION is caused by unconfigured location and target scope, while thermal/power was UNKNOWN from missing Governor readings. Empty authorized scope must remain an enforced safety boundary.

## Integration requirements

1. Never silently coerce null, missing, stale, invalid or unmeasured data to zero or FULL/OK.
2. Give each sensor/UI field a producer, timestamp, unit, validity, freshness limit, owner and precise semantic definition.
3. Distinguish an unavailable optional subsystem from a failed required one; never weaken the armed-scope gate to make the dashboard green.
4. Status snapshots must remain atomic and free of independently mixed revisions where possible.
5. Keep passive radio ownership and five-minute Bettercap rotation untouched while validating dashboard contracts.
6. Validate snapshot contracts offline, using sanitized saved status JSON and no RF/device operations.
7. Regression cases: stale heartbeat despite HTTP success; visual refresh stalled while heartbeat moves; Doctor severity/count disagreement; capture path configured but file absent; duplicate-only ingest scan; missing sensor values; reboot/engine rotation.

## Work separation

Concurrent Codex owns thermal collector/Governor/Doctor integration. This audit is source-and-evidence review for later contract enforcement and UI labeling. Do not assume that an audit entry is an implemented fix; require tests and CI before closing a gate.
