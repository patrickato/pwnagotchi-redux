# Approved 02080f0 native-monitor attempt — 2026-10-17

**Stopped before activation; fully verified restoration. Approval consumed.**
The operator confirmed local console recovery availability and approved one
complete supervised experiment with reviewed rollback. Workbench pulled to
`b208834`; updated AGENTS.md read. Runner bytes exactly matched `02080f0`:
SHA256 `a2ce87f5777d27edfb8710a691ee503808b6de1297554afcf4f8cce1016f4e70`.
All 69 runner regressions passed. Only the frozen reviewed runner was streamed
over strict SSH, restricted to the previously console-verified ED25519 key.
No files were installed on the Pi. Private sanitized logs remain in the
ignored local `build/native-vif-approved-02080f0` directory.

| Phase | Result | Evidence |
|---|---|---|
| BEFORE | PASS | Fresh read-only and execution preflight: verified Ethernet return route/identity, expected Pi 4 signed image/kernel/module, US, unblocked radio, healthy dedicated storage, unbound supplicant; only wlan0 ifindex3 on phy0, monitor/UP, ARPHRD1, firmware0, NM yes/state20, fresh active Redux |
| ISOLATION | PASS | Only redux-live stopped; Bettercap absent; wlan0 runtime NM no/state10 with expected managed/DOWN, unchanged primary identity/type and firmware0; independent safety checks passed |
| CREATE identity/link | PASS | Exactly one rxv5e16a901 created; ifindex5, correct PHY, monitor mode, actual ARPHRD **803 (radiotap)**; firmware monitor **0** before activation |
| CREATE ownership | UNKNOWN; gate FAIL | The sole failed predicate was **temporary_nm_unmanaged**: temp_nm_managed=null. The interface was absent from NM discovery at observation time; absence is not affirmative unmanaged ownership |
| ACTIVATION / RECEPTION | NOT RUN / UNKNOWN | No VIF-up command, firmware2 activation or receive socket sample. Decoded reception counts unavailable, not a measured zero |
| AFTER | PASS | Identified temporary VIF removed; original-only wlan0 ifindex3/phy0 restored monitor/UP/type1/firmware0, NM yes/state20, empty supplicant bindings; fresh active Redux and original degraded Doctor health; Ethernet/US/storage safety verified |

Exact post-create allowlisted snapshot:

```json
{"stage":"created","failed_checks":["temporary_nm_unmanaged"],"observed":{"safe":true,"managed":false,"nm_state":10,"primary_mode":"managed","primary_up":false,"primary_type":1,"live":"inactive","capture_process":false,"firmware":0,"temp_index":5,"temp_type":803,"temp_monitor":true,"temp_nm_managed":null,"temp_present":true,"temp_phy_matches":true}}
```

The runner reported `test.stopped="temporary interface isolation not confirmed"`,
`cleanup=[]`, `restored=true`, and exit status **2**. Authorized cleanup
removed only the positively identified trial VIF, restored original wlan0
monitor then UP while NM-unmanaged, restored NM ownership, then started only
redux-live behind the reviewed safety gates. No retries or expanded recovery.

Independent read-only summary at **2026-10-17T14:32:25Z** confirmed all three
Redux units active, Bettercap HTTP401, global/configured US, unblocked Wi-Fi,
dedicated ext4 capture storage, checkpoint fresh at 1.8 seconds, runtime
running with zero failure streak/current errors, and NM yes/state20.
Active/incoming inventories, processing artifacts and handoffs remain empty.
Original wlan0 is still ARPHRD1 despite its monitor label. Runtime events
are not decoded RF evidence. No hardware capture, handoff or conversion
acceptance is claimed.

No driver reload, global manager restart, persistent change, reboot, reflash,
image build, raw capture artifact or active RF test was performed. Original
Jayofelony card untouched. This authorization is consumed.

Next safe source investigation: determine how to obtain affirmative bounded
ownership evidence for a newly created monitor netdev that NM does not
enumerate. Do not equate discovery absence with managed=false, weaken the
gate, or claim NM actually owned the VIF. Any revised hardware operation
requires independent review and new scoped approval.
