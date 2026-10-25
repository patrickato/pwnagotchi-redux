# gpsd support (`redux gps`)

`gps.py` reads NMEA straight off a serial device; **gpsd** is the daemon most
Linux setups actually run — it owns the GPS (USB/serial/Bluetooth) and every
client reads position from it over TCP (localhost:2947) as JSON. Supporting gpsd
means Augur gets a fix however the GPS is attached, and shares it nicely with
anything else on the box.

```
redux gps                       # read the current fix from gpsd
redux gps --host 10.0.0.5 --port 2947
```

Wire it into the live runtime by setting an env var (gpsd takes precedence over
the serial `$REDUX_GPS` path):

```
REDUX_GPSD=1            # localhost:2947
REDUX_GPSD=host:port    # a remote gpsd
```

Honest by construction: only a real fix is trusted — a TPV report with `mode < 2`
(no fix) or out-of-range/garbage coordinates is skipped, never guessed; gpsd
being down fails safe to "no position". `redux/geo/gpsd.py` — `parse_tpv`,
`GpsdClient`, `gpsd_position_provider` (drop-in for Augur's position slot). The
line source is injectable, so it's fully tested without a socket.
