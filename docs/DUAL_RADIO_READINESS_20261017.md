# Dual-radio readiness and onboard diagnosis — 2026-10-17

**Read-only measurements; no Pi mutations, source deployment, service restart,
NM/radio/channel change, receive trial, RF transmission or image build.**
Only the workbench diagnostic source/tests/contracts changed. UI/TFT code,
documentation and tests belong to the parallel track and were not edited.

## Measured checkpoint

Strict previously verified ED25519 Ethernet SSH succeeded. The consolidated
collector was streamed into Python process memory (`-B`), without installation,
receipt writes, captures or bytecode. Checkpoint around **19:15 UTC**; source
bundle SHA256 `52b3ef06b2cdb228a67610632cc693d9fb264647f93f177ceaea73d55d9762b6`.
The reporting-only API field placement and USB channel observation were refined
after that checkpoint; those changes do not retroactively add measurements.

| Gate | Actual observation |
|---|---|
| Transport/platform | PASS strict verified SSH, stable eth0 source/return route, Pi4/5 model and ARM64 |
| Country/storage/services | PASS existing US, captures ext4/free-space gates, Redux/live/ingest timer active |
| Driver corroboration | PASS installed brcmfmac vermagic and loaded/installed srcversion agreement; not a hash of running kernel memory |
| Config file | `preferred_iface` empty (auto-selection); file mtime AND ctime predate supervisor start |
| Supervisor | Root executable, PID/start generation, module/config arguments and redux-live cgroup independently corroborated |
| Loaded config | UNKNOWN direct in-memory preference: installed runtime has no private configuration attestation; old file age alone cannot prove loaded bytes |
| Effective onboard placement | **FAIL: Bettercap currently uses the Alfa, not wlan0** |
| Bettercap provenance | Root executable/cgroup/start identity, fresh checkpoint/argv interface agreement, owned AF_PACKET inode bound to the USB index; zero onboard/wildcard bindings |
| API/liveness | Unauthenticated loopback GET401, fresh running checkpoint, failure_streak0; no credentials or response body read |
| Alfa | One matching 0e8d:7612 USB device and mt76x2u netdev, monitor/UP/ARPHRD803, zero IP addresses |
| Alfa NM policy | Stable NM owner/device: Managed=true, Autoconnect=true, state20 (unavailable); not an established IP connection or exclusive test ownership |
| Onboard | SDIO brcmfmac, monitor-labelled/ARPHRD1, firmware monitor getter0; NM managed=true/autoconnect=true/state20 |
| Existing pipeline | events_seen328, handed_off0; active/incoming/ready file counts and bytes all0, no truncation |
| Decoded reception | UNKNOWN on BOTH radios; no receive trial or capture payload inspection |

This supersedes the earlier no-USB checkpoint. The old qualification plan's
assumption that Bettercap remains onboard is **not currently satisfied**. Do not
set the Alfa unmanaged or DOWN while it is the running capture owner's radio.
Monitor-labelled ARPHRD803 and an open packet socket do not prove decoded frames.
Neither event counters nor HTTP401 establish real wireless reception.

## Why brcmfmac can be monitor-labelled but ARPHRD1

Reviewed the exact pinned Nexmon driver source, commit
`1654e1857766df92086dbfbed5ffd288efc9bd8c`. Fresh public-source retrieval matched
the cached review bytes:

- `cfg80211.c` SHA256 `cf8b94a8b536a2c72e8934475f4042ff06c2bda71a5ae0cf3c50d1138d3f0f8a`;
- `core.c` SHA256 `35f597ee5d8000d09b1307f59ab33acfcff1c6bd5ce347ea14c333674d078a20`.

The source paths are
[change/add callbacks](https://github.com/jayofelony/nexmon/blob/1654e1857766df92086dbfbed5ffd288efc9bd8c/patches/driver/brcmfmac_6.12.y-nexmon/cfg80211.c)
and [native monitor netdev operations](https://github.com/jayofelony/nexmon/blob/1654e1857766df92086dbfbed5ffd288efc9bd8c/patches/driver/brcmfmac_6.12.y-nexmon/core.c).

`brcmf_cfg80211_change_iface` accepts MONITOR under NEXMON_MON_IF, sets infra,
and updates `ieee80211_ptr->iftype`. That path does not replace the primary
netdev's Ethernet ARPHRD or install the monitor netdev operations. By contrast,
`brcmf_mon_add_vif` calls `brcmf_net_mon_attach`, then explicitly marks the new
netdev radiotap/monitor; its `brcmf_net_mon_open` chooses firmware monitor1/2
from native ARPHRD and sends SET_MONITOR. Its stop callback disables monitor.
The image's separate VIF-lifetime cleanup fix does not turn the primary
change-type path into the native monitor attach path.

This explains the measured primary label/ARPHRD1/firmware0 mismatch without
claiming an absent firmware patch or a new driver regression. Advertisement,
iftype label, Linux link framing, firmware mode, capture-owner placement and
actual decoded frames are six different facts. Currently Bettercap is not on
the primary at all. Re-pinning it cannot repair native framing; no driver change,
VIF test, monitor toggle or rebuild is justified/authorized by this diagnosis.

## Implemented read-only preflight

```text
python3 scripts/pi-acceptance.py --host <verified-Pi-IP> --dual-radio
```

The opt-in `redux.radio.readiness` provider adds bounded root-owned/no-follow
config/checkpoint reads, independent process-generation/cgroup/argument checks,
packet-socket inode bindings, real USB ancestry, and repeated NM property reads
through a pinned unique D-Bus owner. Missing enumeration stays UNKNOWN, never
unmanaged. It uses the actual NM1.42 Device.Autoconnect property, documented
[here](https://networkmanager.dev/docs/api/1.42.4/gdbus-org.freedesktop.NetworkManager.Device.html).
It does not assume GENERAL.AUTOCONNECT exists in nmcli device-show output.

The provider now emits a glass-box `readiness` assessment and the consolidated
acceptance command includes `dual_radio_readiness` in its normal gates/exit
status. A blocked configuration can no longer print detailed failures while
the CLI exits successfully because unrelated base gates passed. The assessment
requires coherent onboard capture assignment/binding, native onboard framing,
one isolated Alfa identity and affirmative unmanaged/autoconnect-disabled USB
policy. Passing means only `READY_FOR_SEPARATE_QUALIFICATION`; decoded reception
and physical qualification remain `UNKNOWN` and require their own approved
hardware gate.

No setters, packet sockets, discovery scan, capture bytes, credentials or API
body are read. The only firmware command is absolute-path
`/usr/local/bin/nexutil -I wlan0 -m` (getter). PATH deliberately excludes
/usr/local/bin in the bounded command transport, so an unqualified nexutil
lookup is not reliable. Commands are limited to two seconds/128KiB, share the
outer acceptance deadline, and a readiness observation over ten seconds is
rejected. Radio/config/process/route replacement discards partial positive
results; individual unavailable firmware/NM properties stay unknown. Existing
capture inspection reads filenames/stat metadata privately and emits only
bounded counts/bytes, with explicit truncation.

New source-only `configuration_identity` checkpoint data records actual loaded
LiveConfig booleans tied to supervisor PID/start ticks/boot. Collectors verify
these against independent process facts before reporting loaded policy. It is a
trusted-root runtime self-report, not cryptographic attestation or a promise
against a privileged adversary. **It is written only to the private live.json,
not the public HTTP snapshot.** Old checkpoints yield UNKNOWN. Nothing was
deployed; the current Pi will not acquire this feature through a local commit.
All existing radio selection and USB lease/rearm interlocks remain unchanged.

## Proposed operations — NOT authorized or executed

### A. Controlled onboard configuration pin and role handoff

This is the smallest next maintenance decision. It would interrupt the current
USB capture owner and put Bettercap on a known non-native onboard path. Operator
approval must explicitly accept that tradeoff; configuration pinning is not an
RF fix. If retaining the current USB owner is preferred, stop here and keep the
passive Alfa qualifier blocked.

Proposed exact scope for **one separately approved operation**:

1. Console available. Repeat strict Ethernet/image/driver/US/storage/ownership
   checks. Privately save exact original config bytes, inode/owner/mode/digest,
   selected USB identity and original interface/NM states. Never publish the
   private config or digest. Refuse a changed baseline or another owner.
2. `systemctl stop redux-live.service` once. Verify the original supervisor and
   tracked Bettercap child exited, USB packet socket is gone and existing files
   were closed/handed off through the existing runtime. Do not kill/retry if
   they remain. No manual capture deletion or conversion of an active file.
3. With compare-before-write against the saved bytes/digest and no-follow regular
   file/parent checks, atomically replace only this existing TOML value:

   ```diff
   -preferred_iface = ""
   +preferred_iface = "wlan0"
   ```

   Preserve every other byte/comment/setting, original root owner and0600 mode.
   Parse the candidate TOML offline first; require the resulting tree differs
   only at live.preferred_iface. Save the original in an approved private backup,
   fsync candidate/file directory; this is a CONFIGURATION WRITE needing approval.
   No connected-capture override, USB lease enablement, country or network change.
4. After at least two seconds (the qualifier's conservative pre-start file-age
   check), `systemctl start redux-live.service` once. Approval must cover the
   existing passive engine startup/channel handling on wlan0; a promise of no
   channel activity cannot be combined with restarting the capture engine.
5. Independently verify new supervisor/child generations, actual `-iface wlan0`,
   socket-inode binding to the original onboard ifindex, fresh runtime, unchanged
   config bytes, original Ethernet/US/physical identities and unchanged NM flags.
   The source selector already refuses USB fallback for a missing/unsafe explicit
   preference; regression coverage preserves that behavior. Report direct loaded
   attestation UNKNOWN on the old image even if operational assignment verifies.
   Firmware0/ARPHRD1 remains a separate failed RF gate.

Planned rollback, **included only if specifically approved with A**: stop only
that newly started live instance if necessary, verify its child/socket release,
compare the current config with the expected candidate, atomically restore the
saved original bytes/ownership/mode, then start only the originally active live
service once. Verify expected new process generations, original automatic USB
assignment, original NM/radio/Ethernet/US state and capture preservation.
If another writer changed config, the child cannot reap, interface state changed
unexpectedly or auto-selection differs, refuse overwrites/extra radio setters
and report rollback unverified. No third start, driver reload or guessed repair.
PID, log/event counts and capture filenames necessarily change during maintenance;
they cannot honestly be claimed restored to their old values.

The new diagnostic attestation is NOT a prerequisite secretly deployed by A.
Installing a reviewed compatible attestation backport/source payload needs its
own explicit deployment approval and offline compatibility review. Do not copy
latest live_runtime.py alone onto the old image: its other dependencies changed.

### B. Alfa isolation preparation — separate from A and from reception

Only after A (or an independently verified equivalent) leaves the current
Bettercap on wlan0, verify no remaining USB owner/socket/binding/IP/route, stable
Alfa USB/netdev generation, Ethernet/US and current monitor/UP/frequency/NM flags.
The measured current monitor/UP, Managed=true, Autoconnect=true baseline is
**outside** the qualifier's already-isolated profile; do not invoke it yet.

A separately approved preparation would, once per needed command, use:

```text
nmcli --wait 5 device set <pinned-USB-netdev> autoconnect no
nmcli --wait 5 device set <pinned-USB-netdev> managed no
```

Verify each actual Device.Autoconnect/Managed/State readback. If the resulting
same adapter is still monitor/UP, only the explicitly approved, identity-gated
`ip link set dev <USB> down` and `iw dev <USB> set type managed` may follow.
Require the qualifier's complete NM no/state10, managed/DOWN/ARPHRD1/no-frequency
baseline, no supplicant/capture owner, and unchanged onboard runtime. No VIF,
channel setter, receive socket, global NM restart or persistent profile/udev edit.
A differing/unknown state stops; no automatic retries.

**Rollback boundary:** the current original policy has autoconnect=true. It is
not possible to promise passive-only behavior while restoring that policy under
NM. Original monitor flags and fixed channel are also not yet qualified for exact
rollback. Therefore B is not yet an unconditional ready-to-execute plan. Before
approving it, choose explicitly between adopting a new safely isolated baseline
or a separately reviewed original-policy return that may resume normal NM radio
activity. A return would require pinned identity, no foreign owner, restore exact
recorded USB monitor/channel/UP while still unmanaged/autoconnect-disabled, then
managed=yes and finally original autoconnect=yes with readback after each step;
any uncertainty stops. Do not silently restore auto-connect, invent a channel or
claim exact restoration of unmeasured monitor flags. These return operations and
possible normal NM RF behavior need explicit scope beyond a passive trial.

### C. Passive reception (later, separately approved)

Only after a reviewed B baseline exists may the existing bounded AWUS036ACM
runner be proposed. It restores that pre-isolated baseline and explicitly does
not restore external original NM ownership. Its existing strict gates remain
unchanged. No receive, hotplug/handoff, driver or image approval is inferred.
