from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from server.config import Settings, get_settings
from server.tasks import run_refresh


log = logging.getLogger("intel.scheduler")


def start_scheduler(settings: Settings | None = None) -> BackgroundScheduler | None:
    settings = settings or get_settings()
    if not settings.scheduler_enabled or not settings.database_configured:
        log.info("scheduler disabled or DATABASE_URL missing")
        return None
    scheduler = BackgroundScheduler(timezone=settings.scheduler_timezone)
    if settings.scheduler_test_interval_seconds:
        trigger = IntervalTrigger(
            seconds=settings.scheduler_test_interval_seconds,
            timezone=settings.scheduler_timezone,
        )
        job_id = "test-interval-research-refresh"
    else:
        trigger = CronTrigger(
            hour=settings.scheduler_hour,
            minute=settings.scheduler_minute,
            timezone=settings.scheduler_timezone,
        )
        job_id = "daily-research-refresh"
    scheduler.add_job(
        run_refresh,
        trigger,
        id=job_id,
        kwargs={"trigger": "schedule"},
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=settings.scheduler_misfire_grace_seconds,
    )
    scheduler.start()
    if settings.scheduler_test_interval_seconds:
        log.warning(
            "TEST MODE: refresh scheduled every %s seconds",
            settings.scheduler_test_interval_seconds,
        )
    else:
        log.info(
            "daily refresh scheduled at %02d:%02d %s",
            settings.scheduler_hour,
            settings.scheduler_minute,
            settings.scheduler_timezone,
        )
    return scheduler
