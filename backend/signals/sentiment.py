"""News sentiment analysis using FINVIZ headlines + VADER scoring.

Scrapes recent news headlines from FINVIZ, scores them with the
VADER sentiment analyzer, and generates signals based on aggregate
sentiment and sentiment shifts.
"""
import logging
import re
from datetime import datetime

import httpx

from backend.signals.models import Signal, SignalType, SignalDirection

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _get_vader():
    """Lazy-load VADER analyzer (returns None if not installed)."""
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        return SentimentIntensityAnalyzer()
    except ImportError:
        logger.warning("vaderSentiment not installed — sentiment analysis disabled")
        return None


def _fetch_finviz_news(symbol: str) -> list[dict]:
    """Scrape recent news headlines from FINVIZ."""
    url = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"FINVIZ fetch failed for {symbol}: {e}")
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
    except ImportError:
        logger.warning("beautifulsoup4 not installed — FINVIZ parsing disabled")
        return []

    news_table = soup.find("table", {"id": "news-table"})
    if not news_table:
        return []

    headlines: list[dict] = []
    current_date = ""

    for row in news_table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue

        date_cell = cells[0].get_text(strip=True)
        title_cell = cells[1]
        link_tag = title_cell.find("a")
        if not link_tag:
            continue

        headline = link_tag.get_text(strip=True)
        source_tag = title_cell.find("span")
        source = source_tag.get_text(strip=True) if source_tag else ""

        # Date format: "Apr-15-26 06:30AM" or just "06:30AM" (same day)
        if re.match(r"[A-Z][a-z]{2}-\d{2}-\d{2}", date_cell):
            current_date = date_cell.split(" ")[0]

        headlines.append({
            "date": current_date,
            "headline": headline,
            "source": source,
        })

    return headlines[:30]


def analyze(symbol: str) -> list[Signal]:
    """Analyze news sentiment for a symbol."""
    analyzer = _get_vader()
    if not analyzer:
        return []

    headlines = _fetch_finviz_news(symbol)
    if not headlines:
        return []

    # Score each headline
    scored: list[dict] = []
    for h in headlines:
        result = analyzer.polarity_scores(h["headline"])
        scored.append({
            **h,
            "compound": result["compound"],
            "pos": result["pos"],
            "neg": result["neg"],
        })

    compounds = [s["compound"] for s in scored]
    avg_sentiment = sum(compounds) / len(compounds)

    # Recent (last 5) vs older headlines for shift detection
    recent = compounds[:5]
    older = compounds[5:15] if len(compounds) > 5 else compounds
    recent_avg = sum(recent) / len(recent)
    older_avg = sum(older) / len(older) if older else 0
    sentiment_shift = recent_avg - older_avg

    positive_count = sum(1 for c in compounds if c > 0.3)
    negative_count = sum(1 for c in compounds if c < -0.3)

    signals: list[Signal] = []
    now = datetime.now()
    base_data = {
        "avg_sentiment": round(avg_sentiment, 3),
        "headline_count": len(scored),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "top_headlines": scored[:5],
    }

    # ── Overall Sentiment ──────────────────────────────────────
    if avg_sentiment > 0.15:
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.SENTIMENT,
            direction=SignalDirection.BULLISH,
            conviction=min(2, 1 + int(avg_sentiment > 0.3)),
            name="Positive News Sentiment",
            description=(
                f"Avg sentiment {avg_sentiment:.2f} across {len(scored)} headlines "
                f"— {positive_count} positive, {negative_count} negative"
            ),
            data=base_data, timestamp=now,
        ))
    elif avg_sentiment < -0.15:
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.SENTIMENT,
            direction=SignalDirection.BEARISH,
            conviction=min(2, 1 + int(avg_sentiment < -0.3)),
            name="Negative News Sentiment",
            description=(
                f"Avg sentiment {avg_sentiment:.2f} across {len(scored)} headlines "
                f"— {negative_count} negative, {positive_count} positive"
            ),
            data=base_data, timestamp=now,
        ))

    # ── Sentiment Shift ────────────────────────────────────────
    if abs(sentiment_shift) > 0.3:
        improving = sentiment_shift > 0
        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.SENTIMENT,
            direction=SignalDirection.BULLISH if improving else SignalDirection.BEARISH,
            conviction=2,
            name=f"Sentiment {'Improving' if improving else 'Deteriorating'}",
            description=(
                f"Recent headlines shifted {sentiment_shift:+.2f} vs prior — "
                f"narrative is {'improving' if improving else 'deteriorating'}"
            ),
            data={"recent_avg": round(recent_avg, 3),
                  "older_avg": round(older_avg, 3),
                  "shift": round(sentiment_shift, 3)},
            timestamp=now,
        ))

    return signals
