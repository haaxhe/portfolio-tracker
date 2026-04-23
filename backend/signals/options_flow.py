"""Options flow analysis using yfinance options chains.

Computes Put/Call ratios and detects unusual volume on individual strikes.
"""
import logging
from datetime import datetime, date, timedelta

import yfinance as yf

from backend.signals.models import Signal, SignalType, SignalDirection

logger = logging.getLogger(__name__)


def _make_session():
    try:
        from curl_cffi import requests as cffi_requests
        return cffi_requests.Session(impersonate="chrome")
    except Exception:
        return None


_session = _make_session()

_MIN_TOTAL_VOL = 100        # ignore symbols with trivial options activity
_UNUSUAL_VOL_THRESHOLD = 5  # volume / OI ratio to flag
_UNUSUAL_MIN_VOL = 500      # minimum absolute volume to flag


def analyze(symbol: str) -> list[Signal]:
    """Analyze near-term options flow for a symbol."""
    try:
        ticker = yf.Ticker(symbol, session=_session)
        expiries = ticker.options
    except Exception as e:
        logger.error(f"Options flow: failed to get expiries for {symbol}: {e}")
        return []

    if not expiries:
        return []

    # Only near-term expirations (next 45 days)
    today = date.today()
    cutoff = today + timedelta(days=45)
    near = [e for e in expiries if date.fromisoformat(e) <= cutoff]
    if not near:
        near = expiries[:2]

    total_call_vol = 0
    total_put_vol = 0
    total_call_oi = 0
    total_put_oi = 0
    unusual_strikes: list[dict] = []

    for expiry in near[:3]:
        try:
            chain = ticker.option_chain(expiry)
        except Exception:
            continue

        calls, puts = chain.calls, chain.puts
        total_call_vol += calls["volume"].fillna(0).sum()
        total_put_vol += puts["volume"].fillna(0).sum()
        total_call_oi += calls["openInterest"].fillna(0).sum()
        total_put_oi += puts["openInterest"].fillna(0).sum()

        # Unusual single-strike activity
        for side, frame in [("call", calls), ("put", puts)]:
            for _, row in frame.iterrows():
                vol = row.get("volume") or 0
                oi = row.get("openInterest") or 0
                if vol >= _UNUSUAL_MIN_VOL and oi > 0 and vol > _UNUSUAL_VOL_THRESHOLD * oi:
                    unusual_strikes.append({
                        "type": side, "expiry": expiry,
                        "strike": float(row["strike"]),
                        "volume": int(vol), "oi": int(oi),
                        "ratio": round(vol / oi, 1),
                    })

    signals: list[Signal] = []
    now = datetime.now()

    if total_call_vol + total_put_vol < _MIN_TOTAL_VOL:
        return signals

    # ── Put/Call Ratio ──────────────────────────────────────────
    if total_call_vol > 0:
        pc_ratio = total_put_vol / total_call_vol
        pc_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 0
        flow_data = {
            "pc_ratio": round(pc_ratio, 2),
            "pc_oi_ratio": round(pc_oi, 2),
            "call_vol": int(total_call_vol),
            "put_vol": int(total_put_vol),
            "call_oi": int(total_call_oi),
            "put_oi": int(total_put_oi),
        }

        if pc_ratio > 1.3:
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.OPTIONS_FLOW,
                direction=SignalDirection.BEARISH, conviction=2,
                name="Elevated Put/Call Ratio",
                description=(
                    f"P/C volume ratio {pc_ratio:.2f} — heavy put buying "
                    f"(contrarian: may signal fear-driven bottom)"
                ),
                data=flow_data, timestamp=now,
            ))
        elif pc_ratio < 0.5:
            signals.append(Signal(
                symbol=symbol, signal_type=SignalType.OPTIONS_FLOW,
                direction=SignalDirection.BULLISH, conviction=1,
                name="Low Put/Call Ratio",
                description=(
                    f"P/C volume ratio {pc_ratio:.2f} — heavy call buying "
                    f"(contrarian: may signal complacency)"
                ),
                data=flow_data, timestamp=now,
            ))

    # ── Unusual Volume ─────────────────────────────────────────
    unusual_calls = [u for u in unusual_strikes if u["type"] == "call"]
    unusual_puts = [u for u in unusual_strikes if u["type"] == "put"]

    if unusual_calls:
        top = max(unusual_calls, key=lambda x: x["volume"])
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.OPTIONS_FLOW,
            direction=SignalDirection.BULLISH, conviction=2,
            name="Unusual Call Volume",
            description=(
                f"{len(unusual_calls)} strike(s) with call vol >{_UNUSUAL_VOL_THRESHOLD}x OI — "
                f"largest: ${top['strike']} exp {top['expiry']} "
                f"({top['volume']:,} vol vs {top['oi']:,} OI)"
            ),
            data={"unusual_calls": unusual_calls[:5]}, timestamp=now,
        ))

    if unusual_puts:
        top = max(unusual_puts, key=lambda x: x["volume"])
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.OPTIONS_FLOW,
            direction=SignalDirection.BEARISH, conviction=2,
            name="Unusual Put Volume",
            description=(
                f"{len(unusual_puts)} strike(s) with put vol >{_UNUSUAL_VOL_THRESHOLD}x OI — "
                f"largest: ${top['strike']} exp {top['expiry']} "
                f"({top['volume']:,} vol vs {top['oi']:,} OI)"
            ),
            data={"unusual_puts": unusual_puts[:5]}, timestamp=now,
        ))

    return signals
