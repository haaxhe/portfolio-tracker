"""Insider trading signals from SEC EDGAR Form 4 filings.

Fetches recent Form 4 filings, parses XML for buy/sell transactions,
and generates signals based on insider activity patterns.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta

import httpx

from backend.signals.models import Signal, SignalType, SignalDirection

logger = logging.getLogger(__name__)

_EDGAR_HEADERS = {
    "User-Agent": "PortfolioTracker admin@portfolio-tracker.local",
    "Accept-Encoding": "gzip, deflate",
}

# Cached ticker → CIK mapping (loaded once per process)
_TICKER_CIK: dict[str, str] = {}


def _load_ticker_cik_map() -> dict[str, str]:
    """Load SEC ticker-to-CIK mapping (cached in memory)."""
    if _TICKER_CIK:
        return _TICKER_CIK
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_EDGAR_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        for entry in resp.json().values():
            _TICKER_CIK[entry["ticker"].upper()] = str(entry["cik_str"])
        logger.info(f"Loaded {len(_TICKER_CIK)} ticker-CIK mappings from SEC")
        return _TICKER_CIK
    except Exception as e:
        logger.error(f"Failed to load SEC ticker map: {e}")
        return {}


def _get_cik(ticker: str) -> str | None:
    mapping = _load_ticker_cik_map()
    return mapping.get(ticker.upper())


def _get_recent_form4s(cik: str, limit: int = 15) -> list[dict]:
    """Fetch recent Form 4 filing metadata from EDGAR submissions."""
    padded = cik.zfill(10)
    try:
        resp = httpx.get(
            f"https://data.sec.gov/submissions/CIK{padded}.json",
            headers=_EDGAR_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"EDGAR submissions fetch failed for CIK {cik}: {e}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    result = []
    for i, form_type in enumerate(forms):
        if form_type in ("4", "4/A") and i < len(dates):
            result.append({
                "filing_date": dates[i],
                "accession": accessions[i],
                "primary_doc": docs[i],
            })
            if len(result) >= limit:
                break
    return result


def _parse_form4_xml(cik: str, filing: dict) -> dict | None:
    """Parse a Form 4 XML to extract transaction details."""
    acc_clean = filing["accession"].replace("-", "")
    primary = filing["primary_doc"]
    # EDGAR often returns an XSLT viewer path (e.g. xslF345X06/form4.xml).
    # The raw XML is at the filename only, without the xsl* prefix directory.
    if "/" in primary:
        primary = primary.split("/")[-1]
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik}/"
        f"{acc_clean}/{primary}"
    )
    try:
        resp = httpx.get(url, headers=_EDGAR_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.debug(f"Failed to fetch Form 4 XML at {url}: {e}")
        return None

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return None

    owner_name = root.findtext(".//rptOwnerName", "Unknown")
    is_officer = root.findtext(".//isOfficer", "0") == "1"
    is_director = root.findtext(".//isDirector", "0") == "1"
    is_ten_pct = root.findtext(".//isTenPercentOwner", "0") == "1"
    title = root.findtext(".//officerTitle", "")

    transactions = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = txn.findtext(".//transactionCode", "")
        shares_str = txn.findtext(".//transactionShares/value", "0")
        price_str = txn.findtext(".//transactionPricePerShare/value", "0")
        acq_disp = txn.findtext(".//transactionAcquiredDisposedCode/value", "")
        try:
            shares = float(shares_str)
            price = float(price_str) if price_str else 0.0
        except ValueError:
            continue
        if code in ("P", "S"):
            transactions.append({
                "code": code, "shares": shares, "price": price,
                "acquired_disposed": acq_disp,
            })

    return {
        "filing_date": filing["filing_date"],
        "owner_name": owner_name,
        "is_officer": is_officer,
        "is_director": is_director,
        "is_ten_pct": is_ten_pct,
        "officer_title": title,
        "transactions": transactions,
    }


def analyze(symbol: str, lookback_days: int = 90) -> list[Signal]:
    """Analyze insider trading activity for a symbol."""
    cik = _get_cik(symbol)
    if not cik:
        logger.debug(f"No CIK found for {symbol}")
        return []

    form4s = _get_recent_form4s(cik)
    if not form4s:
        return []

    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    recent = [f for f in form4s if f["filing_date"] >= cutoff]
    if not recent:
        return []

    # Parse each filing for transaction details
    buys: list[dict] = []
    sells: list[dict] = []

    for filing in recent[:10]:
        parsed = _parse_form4_xml(cik, filing)
        if not parsed:
            continue
        for txn in parsed["transactions"]:
            entry = {
                "owner": parsed["owner_name"],
                "is_officer": parsed["is_officer"],
                "is_director": parsed["is_director"],
                "title": parsed["officer_title"],
                "date": parsed["filing_date"],
                "shares": txn["shares"],
                "price": txn["price"],
                "value": round(txn["shares"] * txn["price"], 2),
            }
            if txn["code"] == "P":
                buys.append(entry)
            elif txn["code"] == "S":
                sells.append(entry)

    signals: list[Signal] = []
    now = datetime.now()

    # ── Insider Buy Signals ────────────────────────────────────
    if buys:
        unique_buyers = set(b["owner"] for b in buys)
        total_value = sum(b["value"] for b in buys)
        officer_buys = [b for b in buys if b["is_officer"]]

        if len(unique_buyers) >= 3:
            conviction = 3
            name = "Insider Buy Cluster"
            desc = (f"{len(unique_buyers)} insiders bought in last "
                    f"{lookback_days}d — total ${total_value:,.0f}")
        elif officer_buys:
            conviction = 2
            top = max(officer_buys, key=lambda x: x["value"])
            name = "Officer Purchase"
            desc = (f"{top['title'] or 'Officer'} {top['owner']} "
                    f"bought ${top['value']:,.0f} worth")
        else:
            conviction = 1
            name = "Insider Purchase"
            desc = f"{len(unique_buyers)} insider(s) bought — total ${total_value:,.0f}"

        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.INSIDER,
            direction=SignalDirection.BULLISH, conviction=conviction,
            name=name, description=desc,
            data={"buys": buys[:5], "unique_buyers": len(unique_buyers),
                  "total_value": total_value},
            timestamp=now,
        ))

    # ── Insider Sell Signals ───────────────────────────────────
    if sells:
        unique_sellers = set(s["owner"] for s in sells)
        total_value = sum(s["value"] for s in sells)

        if len(unique_sellers) >= 3:
            conviction = 2
            name = "Insider Sell Cluster"
            desc = (f"{len(unique_sellers)} insiders sold in last "
                    f"{lookback_days}d — total ${total_value:,.0f}")
        else:
            conviction = 1
            name = "Insider Selling"
            desc = (f"{len(unique_sellers)} insider(s) sold — total "
                    f"${total_value:,.0f} (may be routine compensation)")

        signals.append(Signal(
            symbol=symbol, signal_type=SignalType.INSIDER,
            direction=SignalDirection.BEARISH, conviction=conviction,
            name=name, description=desc,
            data={"sells": sells[:5], "unique_sellers": len(unique_sellers),
                  "total_value": total_value},
            timestamp=now,
        ))

    return signals
