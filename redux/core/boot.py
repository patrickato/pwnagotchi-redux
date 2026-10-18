"""Passive image bootstrap with durable restart records and systemd heartbeats."""
from __future__ import annotations

import logging
import json
import os
from pathlib import Path
import signal
import socket
import tempfile
import uuid
from datetime import datetime, timezone
from threading import Event

from redux.storage_mount import capture_mount_snapshot


BOOT_ID = Path("/proc/sys/kernel/random/boot_id")


def boot_identity(path: Path = BOOT_ID) -> str:
    """Return the kernel boot identity, refusing absent or malformed evidence."""
    value = path.read_text(encoding="ascii").strip()
    try:
        valid = str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("kernel boot identity is malformed")
    return value.lower()


def atomic_checkpoint(path: Path, record: dict) -> None:
    """Replace a complete record, syncing both data and its directory entry."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("checkpoint path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".boot-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(record, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def previous_checkpoint(path: Path):
    if not path.exists():
        return "missing", "no previous checkpoint; starting a passive bootstrap"
    if path.is_symlink():
        raise ValueError("checkpoint path must not be a symlink")
    try:
        if path.stat().st_size > 65536:
            raise ValueError("checkpoint too large")
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1 or type(data.get("clean_shutdown")) is not bool:
            raise ValueError("unsupported checkpoint schema")
    except (ValueError, OSError) as error:
        return "invalid", f"prior checkpoint cannot be trusted ({error}); no actions replayed"
    if data["clean_shutdown"]:
        return "clean", "prior shutdown checkpoint was committed; no actions replayed"
    return "unclean", "prior process did not commit shutdown; resume passive bootstrap without RF replay"


def notify(message, address=None):
    address = address if address is not None else os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if not address.startswith(("/", "@")):
        raise ValueError("invalid systemd notification socket")
    if address.startswith("@"):
        address = "\0" + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(address)
        channel.sendall(message.encode())
    return True


def watchdog_interval(environment, pid):
    if "WATCHDOG_USEC" not in environment:
        return 1.0
    if int(environment.get("WATCHDOG_PID", pid)) != pid:
        raise ValueError("watchdog requested for a different process")
    microseconds = int(environment["WATCHDOG_USEC"])
    if microseconds <= 0:
        raise ValueError("watchdog timeout must be positive")
    return min(1.0, microseconds / 3_000_000)


def validate_state_mount(state: Path, probe=capture_mount_snapshot) -> None:
    """Require persistent bootstrap state to reside on verified REDUXCAP."""
    state = Path(state)
    if state != Path("/captures") and Path("/captures") not in state.parents:
        raise ValueError("bootstrap state must be stored under /captures")
    if probe() is None:
        raise ValueError("writable REDUXCAP mount identity is unavailable")


def run(stop: Event, logger: logging.Logger, checkpoint=None, notifier=None, interval=1.0,
        *, pid=None, boot_id=None) -> None:
    """Stay alive until shutdown without inventing radio or capture state."""
    logger.info(
        "redux image bootstrap active; this entrypoint does not start live integration; "
        "no radio operations are started"
    )
    if interval <= 0:
        raise ValueError("heartbeat interval must be positive")
    record = None
    if checkpoint is not None:
        checkpoint = Path(checkpoint)
        previous, reason = previous_checkpoint(checkpoint)
        logger.info("resume: %s", reason)
        process_id = os.getpid() if pid is None else pid
        current_boot = boot_identity() if boot_id is None else boot_id
        try:
            valid_boot = (isinstance(current_boot, str)
                          and str(uuid.UUID(current_boot)) == current_boot.lower())
        except (ValueError, AttributeError):
            valid_boot = False
        if type(process_id) is not int or process_id <= 0 or not valid_boot:
            raise ValueError("invalid bootstrap process identity")
        record = {"schema": 1, "previous": previous, "reason": reason,
                  "clean_shutdown": False, "pid": process_id, "boot_id": current_boot,
                  "started_utc": datetime.now(timezone.utc).isoformat()}
        atomic_checkpoint(checkpoint, record)
    send = notifier or (lambda message: None)
    send("READY=1\nSTATUS=Passive bootstrap ready; no engine or radio operations")
    # Feed from the supervised loop, never an independent keepalive thread.
    while not stop.wait(interval):
        send("WATCHDOG=1\nSTATUS=Bootstrap loop responsive")
    send("STOPPING=1\nSTATUS=Shutdown requested")
    if record is not None:
        record.update(clean_shutdown=True, stopped_utc=datetime.now(timezone.utc).isoformat())
        atomic_checkpoint(checkpoint, record)
    logger.info("redux image bootstrap stopped: shutdown requested")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()

    def shutdown(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    state = os.environ.get("REDUX_STATE_DIR")
    if state:
        validate_state_mount(Path(state))
    checkpoint = Path(state) / "boot.json" if state else None
    run(stop, logging.getLogger("redux.boot"), checkpoint, notify,
        watchdog_interval(os.environ, os.getpid()))


if __name__ == "__main__":
    main()
