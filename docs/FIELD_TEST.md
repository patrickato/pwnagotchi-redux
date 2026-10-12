# Field testing without typing a stream of Pi commands

For the owner's **separate disposable Redux SD card**, not the working
Jayofelony Pwnagotchi. This profile is for initial hardware diagnosis, not
production.

## Build on the Ubuntu WSL laptop — one paste

```bash
cd ~/redux-workbench-src && git pull --ff-only && bash scripts/build-field-image.sh
```

The helper creates or reuses a **private, unencrypted ed25519 SSH key** in
`~/.ssh/redux_field_ed25519`, validates the paired public key, chooses a
new build directory, and builds a standalone arm64 image. It prints the image
location and creates the `~/redux-field-latest` symlink for Windows browsing.
It never changes the already-flashed Pi, your original Pwnagotchi SD card,
GitHub `main`, or previous image builds.

Flash the new `.img.xz` from
`\\wsl.localhost\Ubuntu\home\dad\redux-field-latest\pi-gen\deploy`
to the **spare** SD using Raspberry Pi Imager. Flashing erases the selected SD.

## Use the laptop instead of the mini keyboard

Connect the Pi's Ethernet to the **same trusted LAN/router as the laptop**.
SSH on this explicitly provisioned field image is **public-key-only**. The
reusable `pi`/`redux` password is for the physical console only and is not
accepted over SSH. The field profile installs and enables OpenSSH and Avahi;
the default image still has SSH off unless a key is deliberately supplied.

From Ubuntu:

```sh
ssh -i ~/.ssh/redux_field_ed25519 pi@redux.local
```

If `redux.local` cannot resolve (for example, due to WSL mDNS), use the Pi's
Ethernet IP from your router's connected-device list instead. This does not
require typing diagnostic commands on the handheld keyboard. The user can
paste any commands on the laptop.

## Zero-command first-boot report

A systemd timer starts after two minutes and refreshes every fifteen minutes.
It saves **one** private, bounded text report under
`/captures/diagnostics/latest.txt` (mode 0600), on the separate writable
REDUXCAP ext4 partition. A failed/missing capture mount prevents writing onto
the read-only/overlay root. This tool only reads radio status; it does not
switch interfaces, start captures, run attacks, or transmit wireless frames.

The report includes service states, Bettercap's bounded stderr/stdout tail,
the latest live state, RF kill and regulatory status, and available interface
information. It redacts common credentials and MAC addresses, but review
before sharing because error messages may contain network labels. It never
includes PCAPs, caplet contents or raw password files.

Once connected to the Pi by SSH, for a concise screen:

```sh
r
```

For the longer diagnostic report:

```sh
sudo redux-report --full
```

To print the automatically saved first-boot report:

```sh
sudo cat /captures/diagnostics/latest.txt
```

An operator can also capture the output directly on the laptop with
`ssh -i ~/.ssh/redux_field_ed25519 pi@redux.local 'sudo redux-report --full'`,
provided the Pi and laptop can reach each other. The command never includes
the private SSH key contents.

## Physical issue discovered 2026-10-11

Pi 4 boots the development ARM64 image, root OverlayFS and REDUXCAP are
available, and `redux.service` works. A `redux-live.service` crash was
traced to touching `/captures/redux`, a separately owned directory; moving
the radio supervisor's state to `/captures/live` made its systemd service
active on the test card. **Bettercap then exited unexpectedly**: no packet
capture is verified. A proper engine-log diagnosis is still needed and is
one of the reasons the automatic report was added. Do not confuse an active
supervisor service with a functioning capture engine.

The GPIO SPI 3.5-inch TFT is **not** installed/configured as part of this lite
diagnostic profile, and physical display acceptance remains a separate gate.

## Pi 4 field evidence: Bettercap aliases + Wi-Fi soft block (2026-10-11)

The first laptop-side SSH report on `192.168.12.115` confirmed the Redux
field image runs all three systemd services, mounts REDUXCAP as ext4 and
serves the dashboard, **but radio capture remains degraded**. Bettercap
repeatedly reports `open /root/bettercap.aliases: permission denied` and
the Wi-Fi phy is soft blocked with a world regulatory domain (`country 00`).
This **does not yet prove** whether one or both issues cause Bettercap to
exit. The radio was in monitor mode, but that alone is not functioning capture.

Bettercap loads `~/bettercap.aliases` using `HOME`. Our
`redux-live.service` correctly forbids access to `/root` through
`ProtectHome=true` and deliberately grants only networking capabilities.
The permanent fix sets `HOME=/captures/live` for Bettercap's child process,
as well as for the live service; it does not weaken the service sandbox,
grant file-ownership capabilities, or modify the other service's
`/captures/redux` directory. This will be baked into the **next** image,
but is not present in the already-flashed card.

To test on the **currently running field card**, with a correct operator-
chosen two-letter regulatory domain and Ethernet still connected, run this
**single command on the Ubuntu laptop** (example **US only if the Pi is
physically operating in the United States**):

```bash
cd ~/redux-workbench-src && git pull --ff-only && ssh -i ~/.ssh/redux_field_ed25519 pi@192.168.12.115 'sudo -n bash -s -- US' < scripts/field-live-fix.sh
```

It stops the live service, creates a temporary `/run/systemd/system` drop-in
for HOME, reloads systemd, applies the given country with `iw reg set`,
unblocks software rfkill only after the regulatory-setting command succeeds,
restarts the live supervisor and prints a bounded report. This code neither
updates the SD's immutable root nor changes another card. The settings
**do not survive reboot**. If radio country cannot be set, the script does
not unblock it. No claim of working capture is made until a later report
establishes a healthy live engine and packet collection is physically tested.

The full report should be reviewed before sharing outside a private support
chat: it redacts MAC addresses and known credential patterns, but log lines
may contain names of nearby wireless networks.

## Bettercap opens interactive quit prompt on every retry (2026-10-12)

The Pi 4 runtime now reaches `starting_engine`, with `wlan0` in monitor
mode and Wi-Fi unblocked in the actual US regulatory domain. But the
Bettercap log displays `Are you sure you want to quit this session? y/n`.

Review of Bettercap's upstream `main.go` confirmed that after applying a
caplet, Bettercap **always enters its interactive `ReadLine` loop**. When its
standard input immediately returns EOF, the main loop invokes an interactive
quit confirmation. Redux supplied `stdin=subprocess.DEVNULL`, forcing that
EOF on every engine launch. `-silent` and `-no-history` do not change
this behavior. The permanent source correction supplies `subprocess.PIPE`
and keeps the private input pipe open for the child's lifetime, closing
the parent descriptor only **after** the child is reaped. No keystrokes,
passwords, or interactive commands are sent to Bettercap.

On the **existing booted** field card only, test the correction from the
ASUS Ubuntu terminal without rebuilding or reflashing:

```bash
cd ~/redux-workbench-src && git pull --ff-only && ssh -i ~/.ssh/redux_field_ed25519 pi@192.168.12.115 'sudo -n bash -s' < scripts/field-stdin-fix.sh
```

The script verifies the exact expected runtime source, stops the single radio
service, patches its source on the **temporary OverlayFS upper layer**,
compiles it, restarts only that service, and prints a fresh bounded report
after 16 seconds. Regulatory country is **not altered**. Ethernet/SSH stays
connected. The earlier runtime HOME override remains effective for this
boot. A later reboot discards these live rootfs modifications; the permanent
changes are committed on the workbench branch.

The report must show `live_state: running` before considering the engine
healthy. Even then, actual passive radio reception and closed file handoff
need a separate physical test on the user's own access point.
