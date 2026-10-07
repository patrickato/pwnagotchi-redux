"""Task 1.1 boot target; live supervisor integration is task 1.5."""
from __future__ import annotations

import logging
import signal
from threading import Event


def run(stop: Event, logger: logging.Logger) -> None:
    """Stay alive until shutdown without inventing radio or capture state."""
    logger.info(
        "redux image bootstrap active; radio and bettercap integration pending "
        "tasks 1.2–1.5; no radio operations are started"
    )
    stop.wait()
    logger.info("redux image bootstrap stopped: shutdown requested")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    stop = Event()

    def shutdown(signum, frame):
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    run(stop, logging.getLogger("redux.boot"))


if __name__ == "__main__":
    main()
