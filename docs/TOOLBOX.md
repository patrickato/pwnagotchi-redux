# Toolbox — the easy "add a tool" path (`redux toolbox`)

The image ships lean. When you later want a capability that needs an outside
program — decode a new band, add a TX backend, crack a new format — this is the
*"A and B are here, wire in C easily"* path: a curated catalog that, for each
tool, says what it's for, which redux feature it lights up, and the **exact**
install command. Adding software is one copy-paste, not a research project.

```
redux toolbox list                 # everything, with what each enables
redux toolbox list --category tx   # just the TX backends
redux toolbox show hackrf          # full detail + install command
redux toolbox install gpsd         # prints the command (…--run to execute)
```

```
$ redux toolbox show hackrf
hackrf — HackRF tools (hackrf_transfer) — TX/RX 1 MHz-6 GHz
  enables:  redux subghz replay (hackrf backend)
  needs:    HackRF One
  note:     your Portapack bundle — this is the TX backend
  install:  sudo apt-get install -y hackrf
```

## Curated to the safe side

The catalog carries standard SDR / Wi-Fi / GPS / IR / network tools — the things
a real tech actually uses. It deliberately carries **nothing whose only purpose
is to harm**: if a tool can't sit on the OK side of that line, it isn't in the
catalog, full stop.

Categories: `sdr` (rtl-sdr, rtl-433, dump1090, multimon-ng, acarsdec), `gps`
(gpsd), `wifi`/`crack` (aircrack-ng, hashcat, hcxtools, reaver, bully, kismet),
`ir` (lirc), `tx` (hackrf, rpitx), `recon` (nmap, tcpdump, tshark), and
`companion` desktop GUIs to run on your laptop (SDR++, URH).

Notes are honest about hardware and placement — e.g. the GPIO tools (`lirc`,
`rpitx`) remind you the 3.5" TFT uses header pins 1–24, so wire to 25–40; the
`companion` tools are marked as laptop GUIs, not device installs.

`install` prints by default (adding system packages is your call and usually
needs root); `--run` executes on the device. `redux/toolbox.py` — `CATALOG`,
`list_tools`/`find`/`get`/`install_commands`.
