"""Run APScheduler as an independent long-lived service."""

from __future__ import annotations

import logging
import signal
import threading

from server.config import get_settings
from server.migrate import upgrade_database
from server.scheduler import start_scheduler


log = logging.getLogger("intel.scheduler.service")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    if not settings.database_configured:
        raise RuntimeError("DATABASE_URL is required for the scheduler service")
    if settings.auto_migrate:
        upgrade_database()
    scheduler = start_scheduler(settings)
    if scheduler is None:
        raise RuntimeError("scheduler is disabled")

    stopped = threading.Event()

    def stop(signum: int, _frame: object) -> None:
        log.info("received signal %s; stopping scheduler", signum)
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        stopped.wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
