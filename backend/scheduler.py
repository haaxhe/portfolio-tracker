"""APScheduler setup for periodic portfolio refresh."""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.config import settings

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()
_main_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop


def _run_refresh():
    """Schedule the async refresh coroutine on the main event loop."""
    if _main_loop is None:
        logger.warning("Scheduler: no event loop set, skipping refresh")
        return
    from backend.portfolio import refresh_all
    asyncio.run_coroutine_threadsafe(refresh_all(), _main_loop)
    logger.info("Scheduled portfolio refresh triggered")


def _run_youtube_monitor():
    """Run the low-cost YouTube monitor on APScheduler's worker thread."""
    from backend.youtube_monitor import run_monitor

    mentions = run_monitor(
        config_path=settings.YOUTUBE_MONITOR_CONFIG_PATH,
        summarize=settings.YOUTUBE_MONITOR_LLM_ENABLED,
    )
    logger.info("Scheduled YouTube monitor found %d market mentions", len(mentions))


def start_scheduler():
    if settings.REFRESH_INTERVAL_MINUTES > 0:
        scheduler.add_job(
            _run_refresh,
            "interval",
            minutes=settings.REFRESH_INTERVAL_MINUTES,
            id="portfolio_refresh",
            replace_existing=True,
        )
        scheduler.start()
        logger.info(f"Scheduler started: refreshing every {settings.REFRESH_INTERVAL_MINUTES} min")
    else:
        logger.info("Scheduler disabled (REFRESH_INTERVAL_MINUTES=0)")

    if settings.YOUTUBE_MONITOR_ENABLED:
        scheduler.add_job(
            _run_youtube_monitor,
            "interval",
            hours=settings.YOUTUBE_MONITOR_INTERVAL_HOURS,
            id="youtube_monitor",
            replace_existing=True,
        )
        if not scheduler.running:
            scheduler.start()
        logger.info(
            "YouTube monitor scheduled every %s hours",
            settings.YOUTUBE_MONITOR_INTERVAL_HOURS,
        )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
