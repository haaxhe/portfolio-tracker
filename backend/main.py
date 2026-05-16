"""
Portfolio Tracker — FastAPI entry point.

Run with:  python -m backend.main
"""
import asyncio
import csv
import io
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, UploadFile, File, Query, HTTPException, Request
from pydantic import BaseModel as PydanticBaseModel, Field
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend import db
from backend.auth import CurrentUser, get_current_user, get_optional_current_user
from backend.models import BrokerName, ClosedPosition, Position, TaxLot
from backend.signals.models import SignalType
from backend.brokers.csv_import import ALLOWED_ASSET_TYPES, CSVImporter, MAX_NAME_LENGTH, SYMBOL_RE
from backend.portfolio import get_portfolio, refresh_all, connect_brokers, price_update_only
from backend.scheduler import start_scheduler, stop_scheduler, set_event_loop
from backend.security import apply_security_headers, enforce_rate_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
MAX_CSV_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 2_000
MAX_SIGNAL_SCAN_SYMBOLS = 50
MAX_ANALYTICS_METADATA_BYTES = 4_096
MAX_ANALYTICS_TEXT_BYTES = 512
ANALYTICS_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
ANALYTICS_SESSION_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
CSV_CONTENT_TYPES = {
    "text/csv",
    "text/plain",
    "application/csv",
    "application/vnd.ms-excel",
    "application/octet-stream",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings.validate_for_startup()
    db.init_db()
    db.init_portfolio_history_table()
    set_event_loop(asyncio.get_event_loop())
    logger.info("Connecting to brokers...")
    results = await connect_brokers()
    for name, ok in results.items():
        logger.info(f"  {name}: {'connected' if ok else 'skipped'}")
    logger.info("Running startup price update...")
    await price_update_only(user_id=settings.DEFAULT_USER_ID)
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(title="Portfolio Tracker", version="0.1.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets", check_dir=False), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    apply_security_headers(response.headers)
    return response


def _csv_export_cell(value: object) -> str:
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


async def _read_csv_upload(file: UploadFile) -> str:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in CSV_CONTENT_TYPES:
        raise HTTPException(400, "CSV upload must use a text/csv-compatible content type")
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "CSV upload filename must end with .csv")

    raw = await file.read(MAX_CSV_UPLOAD_BYTES + 1)
    if len(raw) > MAX_CSV_UPLOAD_BYTES:
        raise HTTPException(413, "CSV upload is too large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "CSV upload must be UTF-8 text") from exc


def _validate_signal_symbols(symbols: list[str]) -> list[str]:
    if len(symbols) > MAX_SIGNAL_SCAN_SYMBOLS:
        raise HTTPException(400, f"Cannot scan more than {MAX_SIGNAL_SCAN_SYMBOLS} symbols at once")
    cleaned = [s.strip().upper() for s in symbols if s and s.strip()]
    if not cleaned:
        raise HTTPException(400, "At least one symbol is required")
    return cleaned


class AnalyticsEventIn(PydanticBaseModel):
    event_name: str
    session_id: str
    path: str = ""
    referrer: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def _truncate_analytics_text(value: str | None) -> str:
    return (value or "").strip()[:MAX_ANALYTICS_TEXT_BYTES]


def _clean_analytics_event(body: AnalyticsEventIn) -> dict[str, Any]:
    event_name = body.event_name.strip().lower()
    if not ANALYTICS_EVENT_RE.fullmatch(event_name):
        raise HTTPException(400, "event_name is invalid")

    session_id = body.session_id.strip()
    if not ANALYTICS_SESSION_RE.fullmatch(session_id):
        raise HTTPException(400, "session_id is invalid")

    try:
        metadata_json = json.dumps(body.metadata, default=str, sort_keys=True)
    except TypeError as exc:
        raise HTTPException(400, "metadata must be JSON serializable") from exc
    if len(metadata_json.encode("utf-8")) > MAX_ANALYTICS_METADATA_BYTES:
        raise HTTPException(413, "metadata is too large")

    return {
        "event_name": event_name,
        "session_id": session_id,
        "path": _truncate_analytics_text(body.path),
        "referrer": _truncate_analytics_text(body.referrer),
        "metadata": json.loads(metadata_json),
    }


# ─── API Routes ───────────────────────────────────────────────


@app.get("/api/public-config")
def api_public_config():
    """Frontend-safe runtime config."""
    return {
        "app_name": "WealthBrief",
        "environment": settings.environment_name,
        "app_base_url": settings.APP_BASE_URL,
        "auth_mode": settings.AUTH_MODE,
        "supabase_url": settings.SUPABASE_URL if settings.AUTH_MODE == "supabase" else "",
        "supabase_publishable_key": (
            settings.SUPABASE_PUBLISHABLE_KEY if settings.AUTH_MODE == "supabase" else ""
        ),
    }


@app.post("/api/analytics/events")
def api_track_analytics_event(
    request: Request,
    body: AnalyticsEventIn,
    current_user: CurrentUser | None = Depends(get_optional_current_user),
):
    """Record first-party product funnel analytics.

    This endpoint intentionally accepts anonymous events so landing-page and
    demo flows can be measured before signup.
    """
    cleaned = _clean_analytics_event(body)
    rate_key = current_user.user_id if current_user else f"anon:{cleaned['session_id']}"
    enforce_rate_limit(request, rate_key, "analytics_event", limit=240, window_seconds=300)
    event_id = db.save_analytics_event(
        user_id=current_user.user_id if current_user else None,
        event_name=cleaned["event_name"],
        session_id=cleaned["session_id"],
        path=cleaned["path"],
        referrer=cleaned["referrer"],
        user_agent=_truncate_analytics_text(request.headers.get("user-agent")),
        metadata=cleaned["metadata"],
    )
    return {"ok": True, "id": event_id}


@app.get("/api/portfolio")
def api_portfolio(current_user: CurrentUser = Depends(get_current_user)):
    """Get unified portfolio summary."""
    return get_portfolio(user_id=current_user.user_id)


@app.get("/api/positions/{broker}")
def api_positions_by_broker(
    broker: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get positions for a specific broker."""
    positions = db.load_positions(broker=broker, user_id=current_user.user_id)
    if not positions:
        raise HTTPException(404, f"No positions found for broker '{broker}'")
    return positions


@app.post("/api/import/csv")
async def api_import_csv(
    request: Request,
    file: UploadFile = File(...),
    broker: BrokerName = Query(BrokerName.CSV, description="Broker label for imported positions"),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Import positions from a CSV file."""
    enforce_rate_limit(request, current_user.user_id, "csv_import", limit=5, window_seconds=3600)
    content = await _read_csv_upload(file)
    try:
        positions = CSVImporter.parse(content, broker_label=broker, max_rows=MAX_CSV_ROWS)
    except ValueError as e:
        raise HTTPException(400, str(e))

    db.save_positions(positions, user_id=current_user.user_id)
    db.save_snapshot(positions, user_id=current_user.user_id)
    return {"imported": len(positions), "broker": broker.value}


class CashUpdate(PydanticBaseModel):
    broker: BrokerName
    amount: float


@app.get("/api/cash")
def api_get_cash(current_user: CurrentUser = Depends(get_current_user)):
    """Get all cash balances."""
    positions = db.load_positions(user_id=current_user.user_id)
    return [
        {"broker": p.broker.value, "amount": p.market_value}
        for p in positions if p.asset_type == "cash"
    ]


@app.post("/api/cash")
def api_set_cash(body: CashUpdate, current_user: CurrentUser = Depends(get_current_user)):
    """Set cash balance for a broker."""
    db.upsert_cash(body.broker.value, body.amount, user_id=current_user.user_id)
    return {"broker": body.broker.value, "amount": body.amount}


@app.get("/api/tax-lots")
def api_get_tax_lots(
    symbol: str | None = Query(None),
    broker: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List tax lots, optionally filtered by symbol and/or broker."""
    return db.load_tax_lots(symbol=symbol, broker=broker, user_id=current_user.user_id)


@app.post("/api/tax-lots")
def api_add_tax_lot(lot: TaxLot, current_user: CurrentUser = Depends(get_current_user)):
    """Add a tax lot for a position."""
    saved = db.save_tax_lot(lot, user_id=current_user.user_id)
    return saved


@app.delete("/api/tax-lots/{lot_id}")
def api_delete_tax_lot(lot_id: int, current_user: CurrentUser = Depends(get_current_user)):
    """Remove a tax lot."""
    ok = db.delete_tax_lot(lot_id, user_id=current_user.user_id)
    if not ok:
        raise HTTPException(404, f"Tax lot {lot_id} not found")
    return {"deleted": lot_id}


class TaxLotPatch(PydanticBaseModel):
    quantity: float | None = None
    cost_basis: float | None = None
    acquired_at: str | None = None


@app.patch("/api/tax-lots/{lot_id}")
def api_update_tax_lot(
    lot_id: int,
    body: TaxLotPatch,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Edit any subset of a tax lot's fields. Deletes the lot if quantity drops to 0."""
    if db.get_tax_lot(lot_id, user_id=current_user.user_id) is None:
        raise HTTPException(404, f"Tax lot {lot_id} not found")
    updated = db.update_tax_lot(
        lot_id,
        quantity=body.quantity,
        cost_basis=body.cost_basis,
        acquired_at=body.acquired_at,
        user_id=current_user.user_id,
    )
    return {"deleted": lot_id} if updated is None else updated


class TaxLotSell(PydanticBaseModel):
    quantity: float
    close_price: float
    closed_at: str | None = None  # YYYY-MM-DD; defaults to today


@app.post("/api/tax-lots/{lot_id}/sell")
def api_sell_from_tax_lot(
    lot_id: int,
    body: TaxLotSell,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Sell N shares from a specific tax lot.

    - Decrements (or deletes) the lot.
    - Decrements the parent position quantity (live broker feeds will reseed
      on next refresh, so this primarily matters for CSV-tracked positions).
    - Creates a ClosedPosition record using the lot's cost basis and acquisition
      date so realized gain and short/long classification are accurate.
    """
    from datetime import datetime as _dt

    lot = db.get_tax_lot(lot_id, user_id=current_user.user_id)
    if lot is None:
        raise HTTPException(404, f"Tax lot {lot_id} not found")
    if body.quantity <= 0:
        raise HTTPException(400, "quantity must be > 0")
    if body.quantity > lot.quantity + 1e-9:
        raise HTTPException(
            400, f"Cannot sell {body.quantity}; lot only has {lot.quantity}"
        )

    closed_at = body.closed_at or _dt.now().strftime("%Y-%m-%d")

    cp = ClosedPosition(
        symbol=lot.symbol,
        name=lot.symbol,
        broker=lot.broker,
        quantity=body.quantity,
        average_cost=lot.cost_basis,
        close_price=body.close_price,
        acquired_at=lot.acquired_at,
        closed_at=closed_at,
    )
    cp.compute_derived()
    saved_cp = db.save_closed_position(cp, user_id=current_user.user_id)

    db.update_tax_lot(lot_id, quantity=lot.quantity - body.quantity, user_id=current_user.user_id)
    db.decrement_position_quantity(lot.symbol, lot.broker.value, body.quantity, user_id=current_user.user_id)

    return {
        "closed_position": saved_cp,
        "remaining_lot_quantity": max(0.0, lot.quantity - body.quantity),
    }


@app.get("/api/closed-positions")
def api_get_closed_positions(current_user: CurrentUser = Depends(get_current_user)):
    """List all closed positions."""
    return db.load_closed_positions(user_id=current_user.user_id)


@app.post("/api/closed-positions")
def api_add_closed_position(
    cp: ClosedPosition,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add a closed/sold position."""
    cp.compute_derived()
    saved = db.save_closed_position(cp, user_id=current_user.user_id)
    return saved


@app.delete("/api/closed-positions/{position_id}")
def api_delete_closed_position(
    position_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Remove a closed position record."""
    ok = db.delete_closed_position(position_id, user_id=current_user.user_id)
    if not ok:
        raise HTTPException(404, f"Closed position {position_id} not found")
    return {"deleted": position_id}


@app.post("/api/refresh")
async def api_refresh(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Force refresh from all connected brokers."""
    enforce_rate_limit(request, current_user.user_id, "portfolio_refresh", limit=10, window_seconds=900)
    summary = await refresh_all(user_id=current_user.user_id)
    return {
        "total_value": summary.total_value,
        "positions_count": len(summary.positions),
        "broker_breakdown": summary.broker_breakdown,
    }


@app.get("/api/portfolio/history")
def api_history(
    limit: int = Query(90, ge=1, le=365),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get historical portfolio snapshots."""
    return db.load_snapshots(limit=limit, user_id=current_user.user_id)


@app.get("/api/price-history")
def api_price_history():
    """Return cached 30-day price history (populated at startup and on each refresh)."""
    from backend.portfolio import get_price_history_cache
    return get_price_history_cache()


@app.get("/api/snapshots/daily")
def api_daily_snapshots(
    days: int = Query(400, ge=1, le=1500),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get last snapshot per calendar day (for charting)."""
    return db.load_daily_snapshots(days=days, user_id=current_user.user_id)


class HistoryEntry(PydanticBaseModel):
    date: str
    total_value: float
    label: str = ""
    is_estimate: bool = True


@app.get("/api/portfolio-history")
def api_get_portfolio_history(current_user: CurrentUser = Depends(get_current_user)):
    """Get manually entered portfolio history entries."""
    return db.load_portfolio_history(user_id=current_user.user_id)


@app.post("/api/portfolio-history")
def api_add_portfolio_history(
    entry: HistoryEntry,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Add or replace a portfolio history entry."""
    return db.save_portfolio_history_entry(
        date=entry.date,
        total_value=entry.total_value,
        label=entry.label,
        is_estimate=entry.is_estimate,
        user_id=current_user.user_id,
    )


@app.delete("/api/portfolio-history/{entry_id}")
def api_delete_portfolio_history(
    entry_id: int,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete a portfolio history entry."""
    ok = db.delete_portfolio_history_entry(entry_id, user_id=current_user.user_id)
    if not ok:
        raise HTTPException(404, f"History entry {entry_id} not found")
    return {"deleted": entry_id}


@app.post("/api/admin/migrate")
def api_migrate(current_user: CurrentUser = Depends(get_current_user)):
    """Run DB migrations (idempotent)."""
    db.init_db()
    db.init_portfolio_history_table()
    from backend.youtube_monitor import init_youtube_monitor_tables
    init_youtube_monitor_tables()
    return {"status": "ok", "user_id": current_user.user_id}


class PositionUpsert(PydanticBaseModel):
    symbol: str
    name: str = ""
    broker: BrokerName
    quantity: float
    average_cost: float
    current_price: float
    asset_type: str = "stock"
    option_type: str | None = None        # 'call' | 'put'
    strike_price: float | None = None
    expiration_date: str | None = None    # YYYY-MM-DD


@app.post("/api/positions/upsert")
def api_upsert_position(
    pos: PositionUpsert,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upsert a single position WITHOUT wiping any other positions."""
    if pos.quantity <= 0:
        raise HTTPException(400, "quantity must be > 0")
    if pos.average_cost < 0:
        raise HTTPException(400, "average_cost must be >= 0")
    if pos.current_price < 0:
        raise HTTPException(400, "current_price must be >= 0")

    symbol = pos.symbol.upper().strip()
    if not SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(400, "symbol is invalid")
    name = pos.name.strip() or symbol
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(400, "name is too long")

    asset_type = pos.asset_type.lower().strip()
    if asset_type not in ALLOWED_ASSET_TYPES - {"cash"}:
        raise HTTPException(
            400,
            f"asset_type must be one of: {', '.join(sorted(ALLOWED_ASSET_TYPES - {'cash'}))}",
        )

    position = Position(
        symbol=symbol,
        name=name,
        broker=pos.broker,
        quantity=pos.quantity,
        average_cost=pos.average_cost,
        current_price=pos.current_price,
        asset_type=asset_type,
        option_type=pos.option_type.lower().strip() if pos.option_type else None,
        strike_price=pos.strike_price,
        expiration_date=pos.expiration_date,
    )
    saved = db.upsert_position(position, user_id=current_user.user_id)
    return saved


@app.delete("/api/admin/positions/broker/{broker_name}")
def api_delete_broker_positions(
    broker_name: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all non-cash positions for a broker (admin use)."""
    deleted = db.delete_broker_positions(broker_name, user_id=current_user.user_id)
    return {"deleted": deleted, "broker": broker_name}


@app.get("/api/export/csv")
def api_export_csv(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export all positions as CSV."""
    enforce_rate_limit(request, current_user.user_id, "csv_export", limit=30, window_seconds=3600)
    positions = db.load_positions(user_id=current_user.user_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Symbol", "Name", "Quantity", "Avg Cost", "Current Price",
        "Market Value", "Unrealized Gain", "Gain %", "Broker", "Type", "Updated"
    ])
    for p in positions:
        writer.writerow([
            _csv_export_cell(p.symbol), _csv_export_cell(p.name), p.quantity, f"{p.average_cost:.2f}",
            f"{p.current_price:.2f}", f"{p.market_value:.2f}",
            f"{p.unrealized_gain:.2f}", f"{p.unrealized_gain_pct:.2f}%",
            _csv_export_cell(p.broker.value), _csv_export_cell(p.asset_type), p.updated_at.isoformat(),
        ])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio_export.csv"},
    )


@app.get("/api/export/all")
def api_export_all_data(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export all WealthBrief app data for the current user as JSON."""
    enforce_rate_limit(request, current_user.user_id, "all_data_export", limit=10, window_seconds=3600)
    payload = db.export_user_data(user_id=current_user.user_id)
    output = io.StringIO(json.dumps(payload, indent=2, default=str))
    return StreamingResponse(
        output,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=wealthbrief_data_export.json"},
    )


@app.delete("/api/account/data")
def api_delete_account_data(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Delete all WealthBrief app data for the current user."""
    enforce_rate_limit(request, current_user.user_id, "account_data_delete", limit=3, window_seconds=3600)
    deleted = db.delete_user_data(user_id=current_user.user_id)
    return {"deleted": deleted}


# ─── Signals ───────────────────────────────────────────────────


class ScanRequest(PydanticBaseModel):
    symbols: list[str]
    sources: list[str] | None = None  # e.g. ["technical", "options_flow"]


@app.get("/api/signals")
def api_get_signals(
    symbol: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get cached signal summaries (optionally filtered by symbol)."""
    from backend.signals import get_cached, get_cached_symbol
    if symbol:
        result = get_cached_symbol(symbol.upper(), user_id=current_user.user_id)
        return result.model_dump(mode="json") if result else {"symbol": symbol, "signals": []}
    return {
        sym: s.model_dump(mode="json")
        for sym, s in get_cached(user_id=current_user.user_id).items()
    }


@app.post("/api/signals/scan")
async def api_scan_signals(
    request: Request,
    body: ScanRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scan specific symbols for signals."""
    enforce_rate_limit(request, current_user.user_id, "signals_scan", limit=20, window_seconds=3600)
    from backend.signals import scan_symbols
    sources = None
    if body.sources:
        sources = [SignalType(s) for s in body.sources]
    symbols = _validate_signal_symbols(body.symbols)
    results = scan_symbols(symbols, sources=sources, user_id=current_user.user_id)

    # Persist to DB
    all_sigs = []
    for summary in results.values():
        for sig in summary.signals:
            all_sigs.append({
                "symbol": sig.symbol,
                "signal_type": sig.signal_type.value,
                "direction": sig.direction.value,
                "conviction": sig.conviction,
                "name": sig.name,
                "description": sig.description,
                "data_json": json.dumps(sig.data, default=str),
                "timestamp": sig.timestamp.isoformat(),
            })
    db.save_signals(all_sigs, user_id=current_user.user_id)

    return {sym: s.model_dump(mode="json") for sym, s in results.items()}


@app.post("/api/signals/scan-portfolio")
async def api_scan_portfolio_signals(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scan all portfolio symbols for signals."""
    enforce_rate_limit(request, current_user.user_id, "signals_scan_portfolio", limit=10, window_seconds=3600)
    positions = db.load_positions(user_id=current_user.user_id)
    raw_symbols = list(set(
        p.symbol for p in positions
        if p.asset_type != "cash" and p.symbol != "CASH"
    ))
    if not raw_symbols:
        return {"error": "No positions to scan"}
    symbols = _validate_signal_symbols(raw_symbols)

    from backend.signals import scan_symbols
    results = scan_symbols(symbols, user_id=current_user.user_id)

    all_sigs = []
    for summary in results.values():
        for sig in summary.signals:
            all_sigs.append({
                "symbol": sig.symbol,
                "signal_type": sig.signal_type.value,
                "direction": sig.direction.value,
                "conviction": sig.conviction,
                "name": sig.name,
                "description": sig.description,
                "data_json": json.dumps(sig.data, default=str),
                "timestamp": sig.timestamp.isoformat(),
            })
    db.save_signals(all_sigs, user_id=current_user.user_id)

    return {sym: s.model_dump(mode="json") for sym, s in results.items()}


@app.get("/api/signals/history")
def api_signal_history(
    symbol: str | None = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get persisted signal history from DB."""
    return db.load_signals(
        symbol=symbol.upper() if symbol else None,
        user_id=current_user.user_id,
    )


# ─── YouTube Market Monitor ───────────────────────────────────


@app.post("/api/youtube-monitor/scan")
def api_scan_youtube_monitor(
    request: Request,
    summarize: bool = Query(False, description="Run optional OpenAI summarization for matched videos."),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Scan configured YouTube channels for market commentary."""
    enforce_rate_limit(request, current_user.user_id, "youtube_monitor_scan", limit=10, window_seconds=3600)
    if summarize:
        enforce_rate_limit(request, current_user.user_id, "youtube_monitor_summarize", limit=3, window_seconds=3600)
    from backend.youtube_monitor import run_monitor

    mentions = run_monitor(user_id=current_user.user_id, summarize=summarize)
    return {
        "mentions": len(mentions),
        "summarize_requested": summarize,
        "results": [
            {
                "video_id": mention.video.video_id,
                "channel_name": mention.video.channel_name,
                "title": mention.video.title,
                "url": mention.video.url,
                "published_at": mention.video.published_at.isoformat(),
                "score": mention.score,
                "tickers": mention.tickers,
                "themes": mention.themes,
                "snippets": mention.snippets,
                "transcript_status": mention.transcript_status,
            }
            for mention in mentions
        ],
    }


@app.get("/api/youtube-monitor/mentions")
def api_youtube_monitor_mentions(
    limit: int = Query(25, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return persisted YouTube market-commentary mentions."""
    from backend.youtube_monitor import load_market_mentions

    return load_market_mentions(limit=limit, user_id=current_user.user_id)


# ─── Dashboard ─────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the React dashboard."""
    dist_dashboard = DIST_DIR / "index.html"
    if dist_dashboard.exists():
        return HTMLResponse(dist_dashboard.read_text())

    if settings.is_production:
        raise HTTPException(503, "Frontend build is missing")

    local_fallback = FRONTEND_DIR / "dashboard.html"
    if settings.ALLOW_LEGACY_DASHBOARD and local_fallback.exists():
        return HTMLResponse(local_fallback.read_text())
    return HTMLResponse(
        "<h1>Frontend build is missing</h1><p>Run <code>npm run build</code> before starting the backend.</p>",
        status_code=503,
    )


# ─── Run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
