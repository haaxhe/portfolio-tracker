"""Technical indicator analysis using yfinance price data.

Computes SMA, EMA, RSI, MACD, Bollinger Bands, ATR, and volume patterns.
Generates Signal objects when indicator thresholds are hit.
"""
import logging
from datetime import datetime

import pandas as pd
import yfinance as yf

from backend.signals.models import Signal, SignalType, SignalDirection

logger = logging.getLogger(__name__)


# ── Indicator computations ────────────────────────────────────────

def _make_session():
    """Return a curl_cffi session (same approach as yfinance_updater)."""
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception:
        return None


_session = _make_session()


def _fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    try:
        ticker = yf.Ticker(symbol, session=_session)
        df = ticker.history(period=period, auto_adjust=True)
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"Failed to fetch history for {symbol}: {e}")
        return None


def _sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window).mean()


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _macd(closes: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bollinger(closes: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = _sma(closes, window)
    std = closes.rolling(window=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _crossover_recent(fast: pd.Series, slow: pd.Series, lookback: int = 5) -> str | None:
    """Detect if fast crossed above/below slow in the last `lookback` bars.
    Returns 'bullish', 'bearish', or None.
    """
    if len(fast.dropna()) < lookback + 1 or len(slow.dropna()) < lookback + 1:
        return None
    recent_diff = (fast - slow).iloc[-(lookback + 1):]
    for i in range(1, len(recent_diff)):
        prev, curr = recent_diff.iloc[i - 1], recent_diff.iloc[i]
        if pd.isna(prev) or pd.isna(curr):
            continue
        if prev <= 0 < curr:
            return "bullish"
        if prev >= 0 > curr:
            return "bearish"
    return None


# ── Indicator snapshot (raw values for display) ───────────────────

def get_indicators(symbol: str) -> dict:
    """Return current indicator values for dashboard display."""
    df = _fetch_history(symbol, period="1y")
    if df is None or len(df) < 20:
        return {}

    closes = df["Close"]
    price = float(closes.iloc[-1])
    result: dict = {"price": price}

    for period in (20, 50, 200):
        sma = _sma(closes, period)
        if len(sma.dropna()) > 0:
            result[f"sma{period}"] = round(float(sma.iloc[-1]), 2)

    rsi = _rsi(closes)
    if len(rsi.dropna()) > 0:
        result["rsi"] = round(float(rsi.iloc[-1]), 2)

    ml, sl, hist = _macd(closes)
    if len(ml.dropna()) > 0:
        result["macd"] = round(float(ml.iloc[-1]), 4)
        result["macd_signal"] = round(float(sl.iloc[-1]), 4)
        result["macd_hist"] = round(float(hist.iloc[-1]), 4)

    bu, bm, bl = _bollinger(closes)
    if len(bl.dropna()) > 0:
        result["bb_upper"] = round(float(bu.iloc[-1]), 2)
        result["bb_middle"] = round(float(bm.iloc[-1]), 2)
        result["bb_lower"] = round(float(bl.iloc[-1]), 2)

    atr = _atr(df)
    if len(atr.dropna()) > 0:
        result["atr"] = round(float(atr.iloc[-1]), 2)

    return result


# ── Signal generation ─────────────────────────────────────────────

def analyze(symbol: str) -> list[Signal]:
    """Run all technical checks and return signals."""
    df = _fetch_history(symbol, period="1y")
    if df is None or len(df) < 50:
        return []

    signals: list[Signal] = []
    closes = df["Close"]
    price = float(closes.iloc[-1])
    now = datetime.now()

    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)

    # ── Trend: price vs 200-day MA ──
    if len(sma200.dropna()) > 0:
        ma200 = float(sma200.iloc[-1])
        above = price > ma200
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.TECHNICAL,
            direction=SignalDirection.BULLISH if above else SignalDirection.BEARISH,
            conviction=1,
            name="Above 200-day MA" if above else "Below 200-day MA",
            description=f"Price ${price:.2f} {'above' if above else 'below'} 200-day MA ${ma200:.2f}",
            data={"price": price, "sma200": ma200},
            timestamp=now,
        ))

    # ── Golden / Death Cross (50 vs 200 MA) ──
    if len(sma50.dropna()) > 5 and len(sma200.dropna()) > 5:
        cross = _crossover_recent(sma50, sma200, lookback=5)
        if cross == "bullish":
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BULLISH, conviction=3,
                name="Golden Cross",
                description="50-day MA crossed above 200-day MA — strong bullish trend signal",
                data={"sma50": float(sma50.iloc[-1]), "sma200": float(sma200.iloc[-1])},
                timestamp=now,
            ))
        elif cross == "bearish":
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BEARISH, conviction=3,
                name="Death Cross",
                description="50-day MA crossed below 200-day MA — strong bearish trend signal",
                data={"sma50": float(sma50.iloc[-1]), "sma200": float(sma200.iloc[-1])},
                timestamp=now,
            ))

    # ── RSI ──
    rsi_series = _rsi(closes)
    rsi_val = float(rsi_series.iloc[-1]) if len(rsi_series.dropna()) > 0 else 50.0
    if rsi_val < 30:
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.TECHNICAL,
            direction=SignalDirection.BULLISH, conviction=2,
            name="RSI Oversold",
            description=f"RSI at {rsi_val:.1f} — oversold, potential bounce",
            data={"rsi": rsi_val}, timestamp=now,
        ))
    elif rsi_val > 70:
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.TECHNICAL,
            direction=SignalDirection.BEARISH, conviction=2,
            name="RSI Overbought",
            description=f"RSI at {rsi_val:.1f} — overbought, potential pullback",
            data={"rsi": rsi_val}, timestamp=now,
        ))

    # ── MACD crossover ──
    macd_line, signal_line, _ = _macd(closes)
    if len(macd_line.dropna()) > 5:
        cross = _crossover_recent(macd_line, signal_line, lookback=5)
        if cross == "bullish":
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BULLISH, conviction=2,
                name="MACD Bullish Crossover",
                description="MACD line crossed above signal line — bullish momentum shift",
                data={"macd": float(macd_line.iloc[-1]), "signal": float(signal_line.iloc[-1])},
                timestamp=now,
            ))
        elif cross == "bearish":
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BEARISH, conviction=2,
                name="MACD Bearish Crossover",
                description="MACD line crossed below signal line — bearish momentum shift",
                data={"macd": float(macd_line.iloc[-1]), "signal": float(signal_line.iloc[-1])},
                timestamp=now,
            ))

    # ── Bollinger Bands ──
    bb_upper, bb_mid, bb_lower = _bollinger(closes)
    if len(bb_lower.dropna()) > 0:
        bb_u = float(bb_upper.iloc[-1])
        bb_l = float(bb_lower.iloc[-1])
        bb_m = float(bb_mid.iloc[-1])
        bb_width = (bb_u - bb_l) / bb_m if bb_m > 0 else 0

        if price <= bb_l * 1.01 and rsi_val < 40:
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BULLISH, conviction=2,
                name="Bollinger Lower Band Touch",
                description=f"Price near lower band ${bb_l:.2f} with RSI {rsi_val:.0f} — potential bounce",
                data={"price": price, "bb_lower": bb_l, "bb_upper": bb_u, "rsi": rsi_val},
                timestamp=now,
            ))
        elif price >= bb_u * 0.99 and rsi_val > 60:
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.BEARISH, conviction=1,
                name="Bollinger Upper Band Touch",
                description=f"Price near upper band ${bb_u:.2f} with RSI {rsi_val:.0f} — potential pullback",
                data={"price": price, "bb_lower": bb_l, "bb_upper": bb_u, "rsi": rsi_val},
                timestamp=now,
            ))

        if bb_width < 0.04:
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.TECHNICAL,
                direction=SignalDirection.NEUTRAL, conviction=2,
                name="Bollinger Squeeze",
                description=f"Bollinger width {bb_width:.3f} — tight bands, expect volatility expansion",
                data={"bb_width": bb_width}, timestamp=now,
            ))

    # ── Volume Spike ──
    if "Volume" in df.columns:
        vol = df["Volume"]
        avg_vol = vol.rolling(20).mean()
        if len(avg_vol.dropna()) > 0:
            current_vol = float(vol.iloc[-1])
            avg = float(avg_vol.iloc[-1])
            if avg > 0 and current_vol > 2 * avg:
                vol_dir = SignalDirection.BULLISH if price > float(closes.iloc[-2]) else SignalDirection.BEARISH
                signals.append(Signal(
                    symbol=symbol, signal_type=SignalType.TECHNICAL,
                    direction=vol_dir, conviction=1,
                    name="Volume Spike",
                    description=f"Volume {current_vol:,.0f} is {current_vol / avg:.1f}x the 20-day average",
                    data={"volume": current_vol, "avg_volume": avg, "ratio": current_vol / avg},
                    timestamp=now,
                ))

    return signals
