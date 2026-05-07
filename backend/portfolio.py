"""Portfolio aggregation — merges positions across brokers."""
import logging
from backend.models import Position, PortfolioSummary
from backend.brokers.robinhood import RobinhoodBroker
from backend.brokers.etrade import ETradeBroker
from backend import db

logger = logging.getLogger(__name__)

# Broker instances (singletons for the app lifetime)
_robinhood = RobinhoodBroker()
_etrade = ETradeBroker()

# In-memory cache for 30-day price history (populated on every refresh)
_price_history: dict[str, dict] = {}


def get_price_history_cache() -> dict[str, dict]:
    return _price_history


async def connect_brokers() -> dict[str, bool]:
    """Attempt to connect all configured brokers."""
    from backend.config import settings

    if not settings.ENABLE_BROKER_CONNECTORS:
        logger.info("Broker connectors disabled; using manual/CSV MVP mode")
        return {"robinhood": False, "etrade": False}

    results = {}
    results["robinhood"] = await _robinhood.connect()
    results["etrade"] = await _etrade.connect()
    return results


async def refresh_all(user_id: str | None = None) -> PortfolioSummary:
    """Fetch latest positions from all connected brokers, update prices, save."""
    global _price_history
    from backend.brokers.yfinance_updater import fetch_prices_and_history

    live_positions: list[Position] = []

    if _robinhood.is_connected():
        live_positions.extend(await _robinhood.get_positions())

    if _etrade.is_connected():
        live_positions.extend(await _etrade.get_positions())

    # Merge in any DB positions not covered by live broker feeds
    live_symbols = {p.symbol for p in live_positions}
    for p in db.load_positions(user_id=user_id):
        if p.asset_type != "cash" and p.symbol not in live_symbols:
            live_positions.append(p)

    live_positions, history = fetch_prices_and_history(live_positions)
    if history:
        _price_history = history

    if live_positions:
        db.save_positions(live_positions, user_id=user_id)
        db.save_snapshot(live_positions, user_id=user_id)

    return get_portfolio(user_id=user_id)


async def price_update_only(user_id: str | None = None) -> None:
    """Startup refresh: update prices for all DB positions and prime the history cache."""
    global _price_history
    from backend.brokers.yfinance_updater import fetch_prices_and_history

    positions = [p for p in db.load_positions(user_id=user_id) if p.asset_type != "cash"]
    if not positions:
        return

    positions, history = fetch_prices_and_history(positions)
    if history:
        _price_history = history

    db.save_positions(positions, user_id=user_id)
    logger.info(f"Startup: refreshed {len(positions)} prices, cached history for {len(history)} symbols")


def get_portfolio(user_id: str | None = None) -> PortfolioSummary:
    """Load latest positions from DB and return summary."""
    positions = db.load_positions(user_id=user_id)
    summary = PortfolioSummary(positions=positions)
    summary.compute_from_positions()
    return summary
