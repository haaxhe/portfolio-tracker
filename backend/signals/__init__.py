"""Signal engine — orchestrates all signal sources and caches results."""
import logging
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.signals.models import Signal, SignalType, SignalDirection, SymbolSignalSummary
from backend.signals import technical, options_flow, insider, sentiment

logger = logging.getLogger(__name__)

# Module registry: signal type → analyze function
_ANALYZERS = {
    SignalType.TECHNICAL: technical.analyze,
    SignalType.OPTIONS_FLOW: options_flow.analyze,
    SignalType.INSIDER: insider.analyze,
    SignalType.SENTIMENT: sentiment.analyze,
}

# In-memory cache of latest scan results, scoped by user id for hosted MVP use.
_cache: dict[str, dict[str, SymbolSignalSummary]] = {}


def scan_symbol(
    symbol: str,
    sources: list[SignalType] | None = None,
    user_id: str = "local-user",
) -> SymbolSignalSummary:
    """Run all (or selected) signal analyzers for a single symbol."""
    active = sources or list(_ANALYZERS.keys())
    all_signals: list[Signal] = []

    for stype in active:
        analyzer = _ANALYZERS.get(stype)
        if not analyzer:
            continue
        try:
            result = analyzer(symbol)
            all_signals.extend(result)
        except Exception as e:
            logger.error(f"{stype.value} analyzer failed for {symbol}: {e}")

    # Fetch indicator snapshot if technical was included
    indicators = {}
    if SignalType.TECHNICAL in active:
        try:
            indicators = technical.get_indicators(symbol)
        except Exception as e:
            logger.error(f"Indicator snapshot failed for {symbol}: {e}")

    summary = SymbolSignalSummary(
        symbol=symbol,
        signals=all_signals,
        indicators=indicators,
    )
    summary.compute_composite()
    _cache.setdefault(user_id, {})[symbol] = summary
    return summary


def scan_symbols(
    symbols: list[str],
    sources: list[SignalType] | None = None,
    max_workers: int = 4,
    user_id: str = "local-user",
) -> dict[str, SymbolSignalSummary]:
    """Scan multiple symbols in parallel."""
    results: dict[str, SymbolSignalSummary] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(scan_symbol, sym, sources, user_id): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            try:
                results[sym] = future.result()
            except Exception as e:
                logger.error(f"Scan failed for {sym}: {e}")
                results[sym] = SymbolSignalSummary(symbol=sym)

    return results


def get_cached(user_id: str = "local-user") -> dict[str, SymbolSignalSummary]:
    """Return all cached scan results."""
    return dict(_cache.get(user_id, {}))


def get_cached_symbol(symbol: str, user_id: str = "local-user") -> SymbolSignalSummary | None:
    """Return cached result for a single symbol."""
    return _cache.get(user_id, {}).get(symbol)


def clear_cache(user_id: str | None = None) -> None:
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)
