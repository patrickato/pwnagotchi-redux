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
