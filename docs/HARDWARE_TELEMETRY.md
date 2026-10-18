# Read-only resource telemetry

The workbench owns one `ResourceCollector` per Augur. `Augur.pump()` samples
at most once every two monotonic seconds and passes the new reading through
`observe_resources()`. It has no worker thread, file output, external Python
dependencies or shutdown hooks. Status, Doctor, POST and web requests inspect
the same cached observation; they never initiate another hardware read.
The live checkpoint refreshes telemetry age independently of the heavier
visual cache. New Augur instances start without a CPU utilization baseline.

| Measurement | Source and interpretation |
| --- | --- |
| CPU temperature | Pi 4/5 device-tree model plus `thermal_zone0` with type `cpu-thermal`, millidegrees C |
| CPU utilization | Two advancing aggregate `/proc/stat` samples; guest counters excluded from the sum because already included in user/nice; idle includes iowait |
| RAM utilization | `/proc/meminfo`: `(MemTotal - MemAvailable) / MemTotal`; no invented free-memory fallback |
| CPU frequency | `cpufreq/policy0/scaling_cur_freq`, kHz converted to MHz; kernel-reported policy frequency, not an independent clock measurement |
| Firmware limits | Pi-only `/usr/bin/vcgencmd get_throttled`, read-only, 0.5-second timeout; missing executable, permissions or malformed output remain unavailable |
| Battery and power source | Unavailable: JSON null, including `on_battery`; there is no battery, current, voltage or wattage collector |

Firmware bit names follow the [Raspberry Pi documentation](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled).
Bits 0–3 describe active undervoltage, frequency capping, throttling and the soft
temperature limit. Bits 16–19 record historical occurrences. The raw mask and
individual flags are exposed; historical flags alone are not active faults.
A zero firmware mask does not measure battery charge or establish mains power.
No package installation or extra permissions are performed to obtain flags.

`Augur.status()` and `/api/status` expose `telemetry`: `measured_at` (Unix
seconds), `age_seconds`, `fresh`, `max_age_seconds`, `values`, `flags` and
per-measurement `provenance`. Before sampling it is null. Values without a
valid measurement are null and have no source attribution. A missing/invalid
new sensor read replaces the old value rather than silently retaining it.
Snapshots expire after ten seconds using both wall and monotonic time;
future timestamps and clock rollback cannot pass freshness. Raw measured
values remain visible when stale, explicitly labeled as stale in the browser.
CPU's initial sample, stalled/reset counters, absent sensors, malformed values,
nonfinite/out-of-range numbers and unsupported development hosts cannot
produce invented measurements. Linux host RAM/load/frequency can still be
reported, but arbitrary host thermal zones are not labeled as Pi CPU sensors.

Governor thresholds, escalation, recovery hold and interval scales are
unchanged. Invalid caller-supplied readings are now discarded before both
comparison and coverage accounting. Firmware flags inform Doctor, without
adding a Governor mode or any capture/radio/lease/rotation/handoff action.
Live capture scheduling is unchanged.

Doctor rejects stale telemetry and requires temperature plus firmware-limit
coverage before reporting FULL-mode measurements within limits. Active firmware
limits produce a degraded finding; an existing survival finding retains its
higher severity. Missing sensors never prove health. Findings retain the
snapshot and explicitly state that battery and power source are unmeasured.
The legacy standalone `DoctorInputs(governor=...)` interface still accepts
independently supplied sensor coverage; Augur always supplies timestamped
telemetry after an observation. Callers of `observe_resources()` must supply
real observations and use their measurement time when passing `now`.

Validation uses deterministic synthetic procfs/sysfs/firmware fixtures, plus
Augur, live checkpoint and dashboard integration tests. These checks are
source evidence only. No Pi access, deployment, image build or physical sensor
qualification was performed. Pi 4/5 sensor paths, firmware utility access under
the installed service sandbox, real temperatures and physical power faults
remain hardware acceptance gates for a separately approved installation.

Offline verification on the workbench: 2,613 pytest cases passed, 2 skipped;
all 734 `image/ci.sh` image-contract cases passed; Python compile, tracked shell
syntax and `git diff --check` passed. The host used its existing temporary TCC
and shellcheck toolchain, with local test socket permission and an external
pycache directory. These are hardware-free checks, not Pi measurements.

The unified qualification integration now keeps the collector and Governor in a
single `ResourceMonitor` shared across runtime/Augur connections. Sampling also
runs with no capture engine, and monotonic time protects production cooldowns
from wall-clock steps. Snapshot optional fields and Doctor containers are
validated before use. Browser resource and visual freshness are independently
checked; see [REDUX_QUALIFICATION.md](REDUX_QUALIFICATION.md).
