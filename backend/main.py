"""
Portfolio Tracker — FastAPI entry point.

Run with:  python -m backend.main
"""
import asyncio
import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from pydantic import BaseModel as PydanticBaseModel
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend import db
from backend.models import BrokerName, ClosedPosition, TaxLot
from backend.signals.models import SignalType
from backend.brokers.csv_import import CSVImporter
from backend.portfolio import get_portfolio, refresh_all, connect_brokers, price_update_only
from backend.scheduler import start_scheduler, stop_scheduler, set_event_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    db.init_db()
    set_event_loop(asyncio.get_event_loop())
    logger.info("Connecting to brokers...")
    results = await connect_brokers()
    for name, ok in results.items():
        logger.info(f"  {name}: {'connected' if ok else 'skipped'}")
    logger.info("Running startup price update...")
    await price_update_only()
    start_scheduler()
    yield
    # Shutdown
    stop_scheduler()


app = FastAPI(title="Portfolio Tracker", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── API Routes ───────────────────────────────────────────────


@app.get("/api/portfolio")
def api_portfolio():
    """Get unified portfolio summary."""
    return get_portfolio()


@app.get("/api/positions/{broker}")
def api_positions_by_broker(broker: str):
    """Get positions for a specific broker."""
    positions = db.load_positions(broker=broker)
    if not positions:
        raise HTTPException(404, f"No positions found for broker '{broker}'")
    return positions


@app.post("/api/import/csv")
async def api_import_csv(
    file: UploadFile = File(...),
    broker: BrokerName = Query(BrokerName.CSV, description="Label: robinhood, etrade, or csv"),
):
    """Import positions from a CSV file."""
    content = (await file.read()).decode("utf-8")
    try:
        positions = CSVImporter.parse(content, broker_label=broker)
    except ValueError as e:
        raise HTTPException(400, str(e))

    db.save_positions(positions)
    db.save_snapshot(positions)
    return {"imported": len(positions), "broker": broker.value}


class CashUpdate(PydanticBaseModel):
    broker: BrokerName
    amount: float


@app.get("/api/cash")
def api_get_cash():
    """Get all cash balances."""
    positions = db.load_positions()
    return [
        {"broker": p.broker.value, "amount": p.market_value}
        for p in positions if p.asset_type == "cash"
    ]


@app.post("/api/cash")
def api_set_cash(body: CashUpdate):
    """Set cash balance for a broker."""
    db.upsert_cash(body.broker.value, body.amount)
    return {"broker": body.broker.value, "amount": body.amount}


@app.get("/api/tax-lots")
def api_get_tax_lots(symbol: str | None = Query(None), broker: str | None = Query(None)):
    """List tax lots, optionally filtered by symbol and/or broker."""
    return db.load_tax_lots(symbol=symbol, broker=broker)


@app.post("/api/tax-lots")
def api_add_tax_lot(lot: TaxLot):
    """Add a tax lot for a position."""
    saved = db.save_tax_lot(lot)
    return saved


@app.delete("/api/tax-lots/{lot_id}")
def api_delete_tax_lot(lot_id: int):
    """Remove a tax lot."""
    ok = db.delete_tax_lot(lot_id)
    if not ok:
        raise HTTPException(404, f"Tax lot {lot_id} not found")
    return {"deleted": lot_id}


class TaxLotPatch(PydanticBaseModel):
    quantity: float | None = None
    cost_basis: float | None = None
    acquired_at: str | None = None


@app.patch("/api/tax-lots/{lot_id}")
def api_update_tax_lot(lot_id: int, body: TaxLotPatch):
    """Edit any subset of a tax lot's fields. Deletes the lot if quantity drops to 0."""
    if db.get_tax_lot(lot_id) is None:
        raise HTTPException(404, f"Tax lot {lot_id} not found")
    updated = db.update_tax_lot(
        lot_id,
        quantity=body.quantity,
        cost_basis=body.cost_basis,
        acquired_at=body.acquired_at,
    )
    return {"deleted": lot_id} if updated is None else updated


class TaxLotSell(PydanticBaseModel):
    quantity: float
    close_price: float
    closed_at: str | None = None  # YYYY-MM-DD; defaults to today


@app.post("/api/tax-lots/{lot_id}/sell")
def api_sell_from_tax_lot(lot_id: int, body: TaxLotSell):
    """Sell N shares from a specific tax lot.

    - Decrements (or deletes) the lot.
    - Decrements the parent position quantity (live broker feeds will reseed
      on next refresh, so this primarily matters for CSV-tracked positions).
    - Creates a ClosedPosition record using the lot's cost basis and acquisition
      date so realized gain and short/long classification are accurate.
    """
    from datetime import datetime as _dt

    lot = db.get_tax_lot(lot_id)
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
    saved_cp = db.save_closed_position(cp)

    db.update_tax_lot(lot_id, quantity=lot.quantity - body.quantity)
    db.decrement_position_quantity(lot.symbol, lot.broker.value, body.quantity)

    return {
        "closed_position": saved_cp,
        "remaining_lot_quantity": max(0.0, lot.quantity - body.quantity),
    }


@app.get("/api/closed-positions")
def api_get_closed_positions():
    """List all closed positions."""
    return db.load_closed_positions()


@app.post("/api/closed-positions")
def api_add_closed_position(cp: ClosedPosition):
    """Add a closed/sold position."""
    cp.compute_derived()
    saved = db.save_closed_position(cp)
    return saved


@app.delete("/api/closed-positions/{position_id}")
def api_delete_closed_position(position_id: int):
    """Remove a closed position record."""
    ok = db.delete_closed_position(position_id)
    if not ok:
        raise HTTPException(404, f"Closed position {position_id} not found")
    return {"deleted": position_id}


@app.post("/api/refresh")
async def api_refresh():
    """Force refresh from all connected brokers."""
    summary = await refresh_all()
    return {
        "total_value": summary.total_value,
        "positions_count": len(summary.positions),
        "broker_breakdown": summary.broker_breakdown,
    }


@app.get("/api/portfolio/history")
def api_history(limit: int = Query(90, ge=1, le=365)):
    """Get historical portfolio snapshots."""
    return db.load_snapshots(limit=limit)


@app.get("/api/price-history")
def api_price_history():
    """Return cached 30-day price history (populated at startup and on each refresh)."""
    from backend.portfolio import get_price_history_cache
    return get_price_history_cache()


@app.get("/api/snapshots/daily")
def api_daily_snapshots(days: int = Query(400, ge=1, le=1500)):
    """Get last snapshot per calendar day (for charting)."""
    return db.load_daily_snapshots(days=days)


class HistoryEntry(PydanticBaseModel):
    date: str
    total_value: float
    label: str = ""
    is_estimate: bool = True


@app.get("/api/portfolio-history")
def api_get_portfolio_history():
    """Get manually entered portfolio history entries."""
    return db.load_portfolio_history()


@app.post("/api/portfolio-history")
def api_add_portfolio_history(entry: HistoryEntry):
    """Add or replace a portfolio history entry."""
    return db.save_portfolio_history_entry(
        date=entry.date,
        total_value=entry.total_value,
        label=entry.label,
        is_estimate=entry.is_estimate,
    )


@app.delete("/api/portfolio-history/{entry_id}")
def api_delete_portfolio_history(entry_id: int):
    """Delete a portfolio history entry."""
    ok = db.delete_portfolio_history_entry(entry_id)
    if not ok:
        raise HTTPException(404, f"History entry {entry_id} not found")
    return {"deleted": entry_id}


@app.post("/api/admin/migrate")
def api_migrate():
    """Run DB migrations (idempotent)."""
    db.init_db()
    db.init_portfolio_history_table()
    return {"status": "ok"}


class PositionUpsert(PydanticBaseModel):
    symbol: str
    name: str = ""
    broker: BrokerName
    quantity: float
    average_cost: float
    current_price: float
    asset_type: str = "stock"


@app.post("/api/positions/upsert")
def api_upsert_position(pos: PositionUpsert):
    """Upsert a single position WITHOUT wiping any other positions."""
    from datetime import datetime
    conn = db._get_conn()
    market_value = pos.quantity * pos.current_price
    unrealized_gain = market_value - pos.quantity * pos.average_cost
    unrealized_gain_pct = (unrealized_gain / (pos.quantity * pos.average_cost) * 100) if pos.average_cost else 0
    conn.execute("""
        INSERT INTO positions (symbol, name, broker, quantity, average_cost, current_price,
            market_value, unrealized_gain, unrealized_gain_pct, account_id, asset_type, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        ON CONFLICT(symbol, broker) DO UPDATE SET
            name=excluded.name, quantity=excluded.quantity,
            average_cost=excluded.average_cost, current_price=excluded.current_price,
            market_value=excluded.market_value, unrealized_gain=excluded.unrealized_gain,
            unrealized_gain_pct=excluded.unrealized_gain_pct,
            asset_type=excluded.asset_type, updated_at=excluded.updated_at
    """, (pos.symbol, pos.name or pos.symbol, pos.broker.value, pos.quantity,
          pos.average_cost, pos.current_price, market_value, unrealized_gain,
          unrealized_gain_pct, pos.asset_type, datetime.now().isoformat()))
    conn.commit()
    return {"symbol": pos.symbol, "broker": pos.broker.value, "market_value": market_value,
            "unrealized_gain": unrealized_gain}


@app.delete("/api/admin/positions/broker/{broker_name}")
def api_delete_broker_positions(broker_name: str):
    """Delete all non-cash positions for a broker (admin use)."""
    conn = db._get_conn()
    cur = conn.execute(
        "DELETE FROM positions WHERE broker = ? AND symbol != 'CASH'",
        (broker_name,)
    )
    conn.commit()
    return {"deleted": cur.rowcount, "broker": broker_name}


@app.get("/api/export/csv")
def api_export_csv():
    """Export all positions as CSV."""
    positions = db.load_positions()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Symbol", "Name", "Quantity", "Avg Cost", "Current Price",
        "Market Value", "Unrealized Gain", "Gain %", "Broker", "Type", "Updated"
    ])
    for p in positions:
        writer.writerow([
            p.symbol, p.name, p.quantity, f"{p.average_cost:.2f}",
            f"{p.current_price:.2f}", f"{p.market_value:.2f}",
            f"{p.unrealized_gain:.2f}", f"{p.unrealized_gain_pct:.2f}%",
            p.broker.value, p.asset_type, p.updated_at.isoformat(),
        ])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=portfolio_export.csv"},
    )


# ─── Signals ───────────────────────────────────────────────────


class ScanRequest(PydanticBaseModel):
    symbols: list[str]
    sources: list[str] | None = None  # e.g. ["technical", "options_flow"]


@app.get("/api/signals")
def api_get_signals(symbol: str | None = Query(None)):
    """Get cached signal summaries (optionally filtered by symbol)."""
    from backend.signals import get_cached, get_cached_symbol
    if symbol:
        result = get_cached_symbol(symbol.upper())
        return result.model_dump(mode="json") if result else {"symbol": symbol, "signals": []}
    return {sym: s.model_dump(mode="json") for sym, s in get_cached().items()}


@app.post("/api/signals/scan")
async def api_scan_signals(body: ScanRequest):
    """Scan specific symbols for signals."""
    from backend.signals import scan_symbols
    sources = None
    if body.sources:
        sources = [SignalType(s) for s in body.sources]
    symbols = [s.upper() for s in body.symbols]
    results = scan_symbols(symbols, sources=sources)

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
    db.save_signals(all_sigs)

    return {sym: s.model_dump(mode="json") for sym, s in results.items()}


@app.post("/api/signals/scan-portfolio")
async def api_scan_portfolio_signals():
    """Scan all portfolio symbols for signals."""
    positions = db.load_positions()
    symbols = list(set(
        p.symbol for p in positions
        if p.asset_type != "cash" and p.symbol != "CASH"
    ))
    if not symbols:
        return {"error": "No positions to scan"}

    from backend.signals import scan_symbols
    results = scan_symbols(symbols)

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
    db.save_signals(all_sigs)

    return {sym: s.model_dump(mode="json") for sym, s in results.items()}


@app.get("/api/signals/history")
def api_signal_history(symbol: str | None = Query(None)):
    """Get persisted signal history from DB."""
    return db.load_signals(symbol=symbol.upper() if symbol else None)


# ─── Dashboard ─────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    """Serve the single-file React dashboard."""
    dashboard_path = Path(__file__).parent.parent / "frontend" / "dashboard.html"
    if dashboard_path.exists():
        return HTMLResponse(dashboard_path.read_text())
    return HTMLResponse("<h1>Dashboard not found</h1><p>Place dashboard.html in frontend/</p>")


# ─── Run ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
    )
