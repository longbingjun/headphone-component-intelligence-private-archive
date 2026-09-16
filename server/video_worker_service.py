"""Long-running process entry point for the subtitle/OCR Video Worker container."""

from __future__ import annotations

import json
import logging
import signal
import threading

from server.config import get_settings
from server.video_worker import run_once


log = logging.getLogger("intel.video-worker")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("Video Worker started; single concurrency; poll=%ss", settings.video_worker_poll_seconds)
    while not stop.is_set():
        results = run_once(settings)
        log.info("worker cycle: %s", json.dumps([item.to_dict() for item in results], ensure_ascii=False))
        if stop.wait(settings.video_worker_poll_seconds):
            break
    log.info("Video Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
