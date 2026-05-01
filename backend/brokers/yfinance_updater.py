"""
Live price updates via yfinance (Yahoo Finance).

Uses yf.download with a curl_cffi session for a single batched request
(start=2026-01-01) covering both current prices and YTD history.
Retries up to 3 times with exponential backoff on rate-limit errors.
"""
import logging
import time
import yfinance as yf
from backend.models import Position

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]   # seconds between attempts


def _make_session():
    """Return a curl_cffi session that yfinance accepts.

    curl_cffi is required — without it Yahoo rate-limits (429) almost immediately.
    """
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except ImportError:
        logger.error(
            "curl_cffi not installed — yfinance will be rate-limited. "
            "Install with: pip install 'curl_cffi>=0.7'"
        )
        return None


def _yf_symbol(symbol: str, asset_type: str) -> str:
    return f"{symbol}-USD" if asset_type == "crypto" else symbol


def fetch_prices_and_history(
    positions: list[Position],
) -> tuple[list[Position], dict[str, dict]]:
    """Single yfinance download (2026-01-01 → today) that:
    - Updates current prices on each position (last close)
    - Returns full YTD history: { symbol: { "dates": [...], "closes": [...] } }

    Retries up to 3 times with backoff on rate-limit errors.
    """
    updatable = [p for p in positions if p.asset_type != "cash"]
    if not updatable:
        return positions, {}

    symbol_map: dict[str, str] = {}   # yf_symbol → portfolio symbol
    for p in updatable:
        yfs = _yf_symbol(p.symbol, p.asset_type)
        symbol_map[yfs] = p.symbol

    yf_symbols = list(symbol_map.keys())

    def _pack(series) -> dict:
        return {
            "dates": [str(idx.date()) for idx in series.index],
            "closes": [float(v) for v in series],
        }

    session = _make_session()

    for attempt in range(_MAX_RETRIES):
        try:
            kwargs = dict(start="2026-01-01", progress=False, auto_adjust=True)
            if session is not None:
                kwargs["session"] = session

            data = yf.download(yf_symbols, **kwargs)

            if data.empty:
                logger.warning(f"yfinance: empty response (attempt {attempt + 1})")
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s…")
                    time.sleep(delay)
                    continue
                return positions, {}

            prices: dict[str, float] = {}
            history: dict[str, dict] = {}

            if len(yf_symbols) == 1:
                series = data["Close"].dropna()
                sym = symbol_map[yf_symbols[0]]
                if not series.empty:
                    prices[sym] = float(series.iloc[-1])
                    history[sym] = _pack(series)
            else:
                close = data["Close"]
                for yf_sym, sym in symbol_map.items():
                    if yf_sym in close.columns:
                        series = close[yf_sym].dropna()
                        if not series.empty:
                            prices[sym] = float(series.iloc[-1])
                            history[sym] = _pack(series)

            for p in positions:
                if p.symbol in prices:
                    p.current_price = prices[p.symbol]
                    p.compute_derived()

            logger.info(
                f"yfinance: updated {len(prices)} prices + history "
                f"({list(prices.keys())})"
            )
            return positions, history

        except Exception as e:
            err = str(e)
            is_rate_limit = "RateLimit" in err or "Too Many" in err or "rate" in err.lower()
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(f"yfinance rate limited (attempt {attempt + 1}), retrying in {delay}s…")
                time.sleep(delay)
            else:
                logger.error(f"yfinance fetch failed: {e}")
                return positions, {}

    return positions, {}
