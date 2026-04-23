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


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
