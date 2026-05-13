"""
Live price updates via yfinance (Yahoo Finance).

Uses yf.download for a single batched request (start=2026-01-01)
covering both current prices and YTD history.
Retries up to 3 times with exponential backoff on rate-limit errors.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
import time
import requests
import yfinance as yf
from backend.config import settings
from backend.models import Position

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]   # seconds between attempts
_CACHE_READY = False


def _ensure_cache_location() -> None:
    """Keep yfinance cache local to this app and away from stale global cookies."""
    global _CACHE_READY
    if _CACHE_READY:
        return

    cache_dir = Path(__file__).resolve().parents[2] / ".yfinance-cache"
    cache_dir.mkdir(exist_ok=True)
    yf.set_tz_cache_location(str(cache_dir))
    _CACHE_READY = True


def _yf_symbol(symbol: str, asset_type: str) -> str:
    return f"{symbol}-USD" if asset_type == "crypto" else symbol


def _alpaca_symbol(symbol: str, asset_type: str) -> str | None:
    return symbol.upper() if asset_type in {"stock", "etf"} else None


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": settings.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": settings.ALPACA_SECRET_KEY,
    }


def _fetch_alpaca_history(symbols: list[str]) -> dict[str, dict]:
    """Fetch daily stock/ETF bars from Alpaca market data."""
    if not settings.alpaca_configured or not symbols:
        return {}

    base_url = settings.ALPACA_DATA_ENDPOINT.rstrip("/")
    bars_path = "/stocks/bars" if base_url.endswith("/v2") else "/v2/stocks/bars"
    feed = (settings.ALPACA_STOCK_FEED or "iex").lower()
    results: dict[str, dict] = {}
    page_token: str | None = None

    try:
        while True:
            params = {
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": "2026-01-01T00:00:00Z",
                "adjustment": "raw",
                "feed": feed,
                "limit": 10000,
            }
            if page_token:
                params["page_token"] = page_token

            response = requests.get(
                f"{base_url}{bars_path}",
                headers=_alpaca_headers(),
                params=params,
                timeout=10,
            )
            if response.status_code in {401, 403}:
                logger.warning(
                    "Alpaca market data rejected credentials or feed access "
                    f"(HTTP {response.status_code}, feed={feed})"
                )
                return {}
            response.raise_for_status()

            payload = response.json()
            bars_by_symbol = payload.get("bars") or {}
            for symbol, bars in bars_by_symbol.items():
                if not isinstance(bars, list):
                    continue
                bucket = results.setdefault(symbol, {"dates": [], "closes": []})
                for bar in bars:
                    close = bar.get("c")
                    timestamp = bar.get("t")
                    if close is None or not timestamp:
                        continue
                    bucket["dates"].append(str(timestamp)[:10])
                    bucket["closes"].append(float(close))

            page_token = payload.get("next_page_token")
            if not page_token:
                break
    except Exception as e:
        logger.warning(f"Alpaca market data fetch failed: {e}")
        return {}

    return {sym: data for sym, data in results.items() if data["closes"]}


def _fetch_chart_history(yf_symbols: list[str]) -> dict[str, dict]:
    """Fetch daily closes directly from Yahoo chart JSON as a yfinance fallback."""
    start = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(time.time())
    results: dict[str, dict] = {}
    headers = {"User-Agent": "Mozilla/5.0"}

    for yf_sym in yf_symbols:
        try:
            response = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}",
                params={
                    "period1": start,
                    "period2": end,
                    "interval": "1d",
                    "events": "history",
                },
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            chart = payload.get("chart", {})
            if chart.get("error"):
                logger.warning(f"Yahoo chart fallback failed for {yf_sym}: {chart['error']}")
                continue
            result = (chart.get("result") or [None])[0]
            if not result:
                continue

            timestamps = result.get("timestamp") or []
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []

            dates: list[str] = []
            clean_closes: list[float] = []
            for ts, close in zip(timestamps, closes):
                if close is None:
                    continue
                dates.append(datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat())
                clean_closes.append(float(close))

            if clean_closes:
                results[yf_sym] = {"dates": dates, "closes": clean_closes}
        except Exception as e:
            logger.warning(f"Yahoo chart fallback failed for {yf_sym}: {e}")

    return results


def fetch_prices_and_history(
    positions: list[Position],
    *,
    use_cache: bool = True,
    cache_ttl_seconds: int = 300,
) -> tuple[list[Position], dict[str, dict]]:
    """Single yfinance download (2026-01-01 → today) that:
    - Updates current prices on each position (last close)
    - Returns full YTD history: { symbol: { "dates": [...], "closes": [...] } }

    Retries up to 3 times with backoff on rate-limit errors.
    """
    updatable = [p for p in positions if p.asset_type != "cash"]
    if not updatable:
        return positions, {}

    symbol_map: dict[str, tuple[str, str]] = {}   # yf_symbol → (portfolio symbol, asset_type)
    alpaca_symbol_map: dict[str, tuple[str, str]] = {}   # Alpaca symbol → (portfolio symbol, asset_type)
    for p in updatable:
        yfs = _yf_symbol(p.symbol, p.asset_type)
        symbol_map[yfs] = (p.symbol, p.asset_type)
        alpaca_sym = _alpaca_symbol(p.symbol, p.asset_type)
        if alpaca_sym:
            alpaca_symbol_map[alpaca_sym] = (p.symbol, p.asset_type)

    yf_symbols = list(symbol_map.keys())
    prices: dict[str, float] = {}
    history: dict[str, dict] = {}
    fetched_cache: dict[tuple[str, str], dict] = {}

    if use_cache:
        from backend import db

        cached = db.load_symbol_prices(
            list({key for key in symbol_map.values()}),
            max_age_seconds=cache_ttl_seconds,
        )
        for (sym, _asset_type), values in cached.items():
            prices[sym] = float(values["current_price"])
            if values.get("history"):
                history[sym] = values["history"]

        if cached:
            logger.info(f"Symbol price cache: reused {len(cached)} fresh prices ({[k[0] for k in cached.keys()]})")

    missing_alpaca_symbols = [
        alpaca_sym for alpaca_sym, (sym, _asset_type) in alpaca_symbol_map.items()
        if sym not in prices
    ]
    alpaca_history = _fetch_alpaca_history(missing_alpaca_symbols) if missing_alpaca_symbols else {}
    if alpaca_history:
        for alpaca_sym, values in alpaca_history.items():
            sym, asset_type = alpaca_symbol_map[alpaca_sym]
            prices[sym] = values["closes"][-1]
            history[sym] = values
            fetched_cache[(sym, asset_type)] = {
                "current_price": values["closes"][-1],
                "history": values,
                "source": "alpaca",
            }
        logger.info(
            f"Alpaca: updated {len(alpaca_history)} prices + history "
            f"({[alpaca_symbol_map[s][0] for s in alpaca_history.keys()]})"
        )

    missing_yf_symbols = [
        yf_sym for yf_sym, (sym, _asset_type) in symbol_map.items()
        if sym not in prices
    ]
    if not missing_yf_symbols:
        for p in positions:
            if p.symbol in prices:
                p.current_price = prices[p.symbol]
                p.compute_derived()
        if fetched_cache:
            from backend import db

            db.save_symbol_prices(fetched_cache)
        return positions, history

    def _pack(series) -> dict:
        return {
            "dates": [str(idx.date()) for idx in series.index],
            "closes": [float(v) for v in series],
        }

    _ensure_cache_location()

    def _apply(prices_map: dict[str, float]) -> list[Position]:
        for p in positions:
            if p.symbol in prices_map:
                p.current_price = prices_map[p.symbol]
                p.compute_derived()
        return positions

    for attempt in range(_MAX_RETRIES):
        try:
            data = yf.download(
                missing_yf_symbols,
                start="2026-01-01",
                progress=False,
                auto_adjust=True,
                threads=False,
            )

            if data.empty:
                logger.warning(f"yfinance: empty response (attempt {attempt + 1})")
                fallback = _fetch_chart_history(missing_yf_symbols)
                if fallback:
                    for yf_sym, values in fallback.items():
                        sym, asset_type = symbol_map[yf_sym]
                        prices[sym] = values["closes"][-1]
                        history[sym] = values
                        fetched_cache[(sym, asset_type)] = {
                            "current_price": values["closes"][-1],
                            "history": values,
                            "source": "yahoo_chart",
                        }
                    for p in positions:
                        if p.symbol in prices:
                            p.current_price = prices[p.symbol]
                            p.compute_derived()
                    from backend import db

                    db.save_symbol_prices(fetched_cache)
                    logger.info(
                        f"Yahoo chart fallback: filled {len(fallback)} missing prices + history "
                        f"({[symbol_map[yf_sym][0] for yf_sym in fallback.keys()]}); "
                        f"total refreshed {len(prices)}"
                    )
                    return positions, history
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.info(f"Retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                return _apply(prices), history

            close = data["Close"]
            # Newer yfinance returns a MultiIndex-column DataFrame even for a
            # single symbol; older versions returned a flat Series. Coerce to
            # a DataFrame keyed by yf_symbol so one branch handles both.
            if not hasattr(close, "columns"):
                close = close.to_frame(missing_yf_symbols[0])
            for yf_sym in missing_yf_symbols:
                if yf_sym in close.columns:
                    series = close[yf_sym].dropna()
                    if not series.empty:
                        sym, asset_type = symbol_map[yf_sym]
                        prices[sym] = float(series.iloc[-1])
                        history[sym] = _pack(series)
                        fetched_cache[(sym, asset_type)] = {
                            "current_price": prices[sym],
                            "history": history[sym],
                            "source": "yfinance",
                        }

            remaining_yf_symbols = [
                yf_sym for yf_sym, (sym, _asset_type) in symbol_map.items()
                if sym not in prices
            ]
            if remaining_yf_symbols:
                fallback = _fetch_chart_history(remaining_yf_symbols)
                for yf_sym, values in fallback.items():
                    sym, asset_type = symbol_map[yf_sym]
                    prices[sym] = values["closes"][-1]
                    history[sym] = values
                    fetched_cache[(sym, asset_type)] = {
                        "current_price": values["closes"][-1],
                        "history": values,
                        "source": "yahoo_chart",
                    }

            for p in positions:
                if p.symbol in prices:
                    p.current_price = prices[p.symbol]
                    p.compute_derived()

            if fetched_cache:
                from backend import db

                db.save_symbol_prices(fetched_cache)

            logger.info(
                f"Price refresh: updated {len(prices)} prices + history "
                f"({list(prices.keys())})"
            )
            return positions, history

        except Exception as e:
            err = str(e)
            is_rate_limit = "RateLimit" in err or "Too Many" in err or "rate" in err.lower()
            if is_rate_limit and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(f"yfinance rate limited (attempt {attempt + 1}), retrying in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"yfinance fetch failed: {e}")
                if fetched_cache:
                    from backend import db

                    db.save_symbol_prices(fetched_cache)
                return _apply(prices), history

    if fetched_cache:
        from backend import db

        db.save_symbol_prices(fetched_cache)
    return _apply(prices), history
