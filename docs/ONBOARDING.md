# First run — the field-operator path

From a freshly flashed card to an armed, authorized device. This is the path for the
operator (not the dev quickstart in the README). Every firing-capable function stays
behind the central Scope — this guide is how you set that up, once.

## One command to lay it down

```
export AUGUR_PASSPHRASE='a strong passphrase'     # at-rest encryption + swarm keystore
redux init --dir /etc/redux --node-id augur-01
```

`redux init` is idempotent (it won't clobber existing files without `--force`) and:

- writes `/etc/redux/config.toml` from the template, with your `node_id` filled in;
- mints a sealed **swarm keystore** at `/etc/redux/swarm.keys` (only if the `crypto`
  extra is installed **and** `AUGUR_PASSPHRASE` is set — otherwise it honestly tells
  you how to create it later, rather than leaving an unencrypted key);
- prints the checklist below.

## Then, the four steps it prints

1. **Edit `config.toml`** — fill every `>>> USER INPUT REQUIRED <<<` field. On a
   `localhost` dashboard there are none; for an off-box bind set `[web] token`.
2. **Set `AUGUR_PASSPHRASE`** in the device's environment so the vault and keystore
   open on boot. Never put the passphrase in the config — the config names only the
   *env var* (`[vault] passphrase_env`).
3. **Arm your lab** — nothing is firing-capable until the Scope has targets:
   ```
   redux scope --file /etc/redux/scope.json arm-lab --cidr 10.0.0.0/24
   ```
   The Scope is the authorization list — your own networks/devices, your lab, gear
   you own, engagements you're contracted for, ranges, CTFs, consenting peers. It
   starts empty on purpose.
4. **Validate** — `redux config check --config /etc/redux/config.toml` lists any
   problems (empty = good).

## Bringing a second device onto the swarm

On the first device: `redux mesh key export --store /etc/redux/swarm.keys` prints an
`AKEY1` token (show it as a QR, or send it over the LoRa lane to a node you trust).
On the new device, after its own `redux init`: write the token to a file and
`redux mesh key import --store /etc/redux/swarm.keys --token-file tok.txt`. Rotate per
job with `redux mesh key rotate` — recent keys stay valid through a grace window.

## What still needs the hardware

Flashing the image, monitor-mode capture, the radios, the TFT, GPS, and the on-boot
service that reads `config.toml` into the long-running processes are the on-Pi steps
— see `docs/HARDWARE_VALIDATION.md`.
