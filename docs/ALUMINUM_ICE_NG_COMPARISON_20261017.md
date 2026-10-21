# Aluminum-ice Pwnagotchi and Pwnagotchi-NG — Redux comparison
_Date: 2026-10-17. Source-only research; no hardware change or code import._

## Scope and evidence

Two publicly visible efforts must not be conflated:

1. **[aluminum-ice/pwnagotchi](https://github.com/aluminum-ice/pwnagotchi)** is
   the older GPL-3.0 Pwnagotchi fork. Its latest publicly listed binary release
   is **1.8.5, February 2024**. Its *master* branch was updated in May 2026
   with a [modernisation design](https://github.com/aluminum-ice/pwnagotchi/blob/master/docs/design.md):
   removed obsolete pwngrid dependency, dead UI/voice code, plugin-worker
   shutdown diagnostics, and a staged plan to move from 32-bit Bullseye through
   Bookworm to 64-bit. The Bookworm 64-bit/PyTorch phase is **planned**, not
   a demonstrated released image on this repository.
2. Reddit [October 14 Pwnagotchi-NG teaser](https://www.reddit.com/r/pwnagotchi/comments/1x64s1e/pwnagotching_teaser/)
   and [October 17 device-management announcement](https://www.reddit.com/r/pwnagotchi/comments/1x815go/pwnagotching_device_management/)
   describe a new ground-up 64-bit Bookworm image: reproducible Packer/Ansible
   builder; Bluetooth PAN phone management and bounded pairing; separate USB
   capture radio and built-in management Wi-Fi; reconnection worker;
   on-device user-selectable learning methods; planned e-ink companion UI.
   The author reports 10x legacy captures, no blind errors and stable Bluetooth.
   **These are self-reported claims, not independently measured results.**
   An inspectable source tree, reproducible image artifact and benchmark
   protocol for this particular NG implementation were not located in this
   investigation. Do not assume the old fork's code is the new NG code or
   assume the teaser's author is authenticated by GitHub repository metadata.

Our [Redux workbench](https://github.com/patrickato/pwnagotchi-redux/tree/human/redux-workbench)
is an actual self-standing 64-bit Bookworm pi-gen image on Pi4, with automated
image contracts, checksums, signed build-provenance rules, Docker-free native
components, Bettercap service orchestration, dedicated REDUXCAP storage,
glass-box Doctor, 480x320 framebuffer UI code and pure multi-radio role
planner. **Physical native RF and physical TFT validation are not yet passed**.
A native monitor VIF was measured as ARPHRD 803 but was not activated
because NetworkManager ownership could not be affirmatively established.
The signed running image's primary wlan0 remains ARPHRD 1, firmware0.

## Comparison with verified/claimed status

| Layer | New NG report | Redux source/field evidence | Assessment |
|---|---|---|---|
| OS | Bookworm Lite arm64 via Packer/Ansible (reported) | Bookworm arm64 via pi-gen, Pi4 boot verified | Two viable build systems; no strong reason to migrate |
| Capture radio | Dedicated USB monitor adapter; onboard for uplink (reported) | Radio role-selection and hotplug planning exist; USB capture not physically qualified | **High-value design and MVP fallback** |
| Real RF | ~10x old Pwnagotchi capture rate and no blinds (claimed) | Pi4 radiotap VIF created but not activated; no decoded standalone captures | NG claims lead, not a demonstrated head-to-head win |
| Connectivity | Bluetooth PAN, onboarding window, reconnect daemon, optional Wi-Fi station (reported) | No equivalent working standalone phone-onboarding service yet | **High-value UX and security improvement** |
| Brain | Multiple selectable algorithms (reported) | Auditable advisory glass-box brain and radio role planner; not an ML benchmark | Candidate for optional policy selectors and A/B metrics |
| Device management | Phone-managed configuration and whitelist sideload (reported) | Scope commands, web/TFT views and packs; end-user management controls remain incomplete | High-value UI backlog |
| Extensibility | Old fork has thread-pool callbacks and community plugin catalog | Redux typed packs, manifests, dependency resolver; live plugin compatibility incomplete | Compare lifecycle/executor patterns only |
| LCD | E-ink companion planned | 480x320 browser-free TFT code + touch tests; physical panel unqualified | Prioritize user-owned 3.5in TFT, not e-ink by default |
| Debug/release | NG logs/CI not public at review | Extensive CI, runtime Doctor, field gate logs, recovery, signed builds | Redux stronger on **publicly verifiable** testing |

## Concrete improvement candidates (priority order)

**A — P0: real dual-radio USB-capture qualification.** NG's separation of
capture from connectivity is the strongest practical inspiration.
Redux's `redux/radio/orchestrator.py` already independently assigns CAPTURE
and UPLINK roles, but the project must qualify that against real supported
USB monitor hardware and enforce role ownership before changing interfaces.
Propose a hardware-free source inspection and simulator gate first; only then
request one bounded external-radio field acceptance. This can unblock
standalone capture/PCAP testing *without* guessing at onboard Nexmon mode.
A USB-capable capture path is an *alternative*, not a replacement for planned
Nexmon support.

**B — P1: phone-first management plane.** Define a BlueZ-based Bluetooth PAN
manager with *time-limited*, owner-triggered discoverability, no embedded
default PIN, authenticated provisioning, reconnect backoff, and explicit
scope between management interfaces and radio capture. Test pairing state
machines, timeouts, power-loss recovery, interface exclusion and privacy
without physical Bluetooth initially. Require owner approval for actual
Pi service/package/network installation.

**C — P1: user-selectable, explainable policies.** Expose rule-based baseline
and optional experimental strategies behind the same typed decision API.
Record exact parameters, channel coverage, energy and legitimate capture
outcomes per observation window. Blind claims such as '10x' must be evaluated
with equal environment, firmware, interface hardware, allowed channels and
session duration. Never trade rf uptime for an unproven ML policy.

**D — P1: plugin/pack event isolation.** The old public
[`pwnagotchi/plugins/__init__.py`](https://github.com/aluminum-ice/pwnagotchi/blob/master/pwnagotchi/plugins/__init__.py)
uses a 10-worker `ThreadPoolExecutor` and callback locks, gracefully
handling executor shutdown during mode changes. Redux should test whether
equivalent bounded dispatch, cancellation and per-pack failure isolation
are already present before adding implementation. Do not copy an unlimited
queue or entire compatibility layer.

**E — P2: Bettercap resilience and radio lifecycle lessons.** The older
[`pwnagotchi/bettercap.py`](https://github.com/aluminum-ice/pwnagotchi/blob/master/pwnagotchi/bettercap.py)
uses reconnect/backoff for websocket handling. The older
[`builder/data/usr/bin/pwnlib`](https://github.com/aluminum-ice/pwnagotchi/blob/master/builder/data/usr/bin/pwnlib)
documents a specific wlan0/mon0 order to avoid historical driver EBUSY -16.
Treat those only as diagnostic **historical hypotheses** for different
kernels; do not run or port its global rfkill, driver reload, ifconfig,
network interface or legacy firmware commands to the 6.12.109 Redux Pi.
Audit existing Redux reconnect and retry tests instead.

**F — separate P0 driver reference.** An independent project,
[`flintdevices/nexmon-install`](https://github.com/flintdevices/nexmon-install),
documents *physically tested* native Wi-Fi receive on Pi4 Bookworm kernel
6.12.75 with Kali `brcmfmac-nexmon-dkms 6.12.2`; it attributes earlier
sustained-capture hangs to a self-ported module. This is **not aluminum-ice's
project** and not validated on Redux's kernel **6.12.109** and signed image.
It warrants source-only driver ABI/provenance review before ever proposing
a driver swap; no field deployment on current approval.

## What not to copy or claim

- Do not replace our tested pi-gen image with 2024 32-bit Bullseye packages
  or unverified 2026 NG binaries.
- Do not imply the new NG source has already been reviewed, or that its
  self-reported 10x throughput is an independent measurement.
- Do not merge GPLv3 code without retaining original authorship and copyright
  notices and checking included third-party dependency licenses. Redux is
  also GPLv3; copying might be legally possible, but attribution and
  derivative obligations must be handled explicitly.
- Do not disable NetworkManager ownership gates or modify the live Pi.
- Do not sidetrack touchscreen acceptance with support for e-ink sizes the
  user does not currently own.

## Suggested next roadmap gates

1. Source-only review of dual-radio USB capture, management/capture isolation,
   Bettercap reconnect/handoff semantics, and available adapter drivers.
2. Hardware-free, fail-closed role-ownership tests and acceptance checklist;
   separately approved field proof on USB adapter.
3. Source-only Bluetooth PAN onboarding/policy state machine and its tests.
4. Real 802.11 reception, nonempty closed capture, ingestion, end-to-end
   verification, Pi5 and physical TFT acceptance.
5. Revisit actual NG source, benchmark evidence or upstream collaboration
   *when published*; update this comparison based on concrete commits.

Conclusion: useful *architectural* learnings are available today, but do not
replace one unverified driver path with another unknown image. Prioritize
USB capture and phone management; keep Redux's measured telemetry and
open, auditable test/recovery approach.
