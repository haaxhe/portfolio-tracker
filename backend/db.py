"""SQLite persistence for positions and historical snapshots."""
import json
import sqlite3
import logging
from datetime import datetime
from backend.models import Position, ClosedPosition, TaxLot, Snapshot, BrokerName
from backend.config import settings

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            quantity REAL DEFAULT 0,
            average_cost REAL DEFAULT 0,
            current_price REAL DEFAULT 0,
            market_value REAL DEFAULT 0,
            unrealized_gain REAL DEFAULT 0,
            unrealized_gain_pct REAL DEFAULT 0,
            broker TEXT NOT NULL,
            account_id TEXT DEFAULT '',
            asset_type TEXT DEFAULT 'stock',
            acquired_at TEXT DEFAULT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tax_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            quantity REAL NOT NULL,
            cost_basis REAL NOT NULL,
            acquired_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tax_lots_sym ON tax_lots(symbol, broker);

        CREATE TABLE IF NOT EXISTS closed_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            broker TEXT NOT NULL,
            quantity REAL DEFAULT 0,
            average_cost REAL DEFAULT 0,
            close_price REAL DEFAULT 0,
            realized_gain REAL DEFAULT 0,
            realized_gain_pct REAL DEFAULT 0,
            acquired_at TEXT DEFAULT NULL,
            closed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_value REAL NOT NULL,
            total_cost REAL NOT NULL,
            total_gain REAL NOT NULL,
            positions_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_positions_broker ON positions(broker);
        CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);
        CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(timestamp);

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            conviction INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            data_json TEXT DEFAULT '{}',
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
    """)
    # Migrate existing DB: add acquired_at if missing
    try:
        conn.execute("ALTER TABLE positions ADD COLUMN acquired_at TEXT DEFAULT NULL")
        conn.commit()
        logger.info("Migrated positions table: added acquired_at column")
    except Exception:
        pass  # Column already exists
    conn.close()
    logger.info("Database initialized")


def upsert_cash(broker: str, amount: float) -> None:
    """Set the cash balance for a broker, replacing any existing cash row."""
    from datetime import datetime
    conn = _get_conn()
    conn.execute(
        "DELETE FROM positions WHERE broker = ? AND symbol = 'CASH'", (broker,)
    )
    if amount > 0:
        conn.execute(
            """INSERT INTO positions
            (symbol, name, quantity, average_cost, current_price,
             market_value, unrealized_gain, unrealized_gain_pct,
             broker, account_id, asset_type, updated_at)
            VALUES ('CASH','Cash',?,1.0,1.0,?,0.0,0.0,?,'','cash',?)""",
            (amount, amount, broker, datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()


def save_positions(positions: list[Position]) -> None:
    """Replace all non-cash positions for each broker present in the list."""
    conn = _get_conn()
    brokers_seen = set(p.broker.value for p in positions)
    for broker in brokers_seen:
        conn.execute(
            "DELETE FROM positions WHERE broker = ? AND symbol != 'CASH'", (broker,)
        )

    for p in positions:
        conn.execute(
            """INSERT INTO positions
            (symbol, name, quantity, average_cost, current_price,
             market_value, unrealized_gain, unrealized_gain_pct,
             broker, account_id, asset_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                p.symbol, p.name, p.quantity, p.average_cost, p.current_price,
                p.market_value, p.unrealized_gain, p.unrealized_gain_pct,
                p.broker.value, p.account_id, p.asset_type,
                p.updated_at.isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def load_positions(broker: str | None = None) -> list[Position]:
    conn = _get_conn()
    if broker:
        rows = conn.execute(
            "SELECT * FROM positions WHERE broker = ?", (broker,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM positions").fetchall()
    conn.close()

    return [
        Position(
            id=r["id"],
            symbol=r["symbol"],
            name=r["name"],
            quantity=r["quantity"],
            average_cost=r["average_cost"],
            current_price=r["current_price"],
            market_value=r["market_value"],
            unrealized_gain=r["unrealized_gain"],
            unrealized_gain_pct=r["unrealized_gain_pct"],
            broker=BrokerName(r["broker"]),
            account_id=r["account_id"],
            asset_type=r["asset_type"],
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
        for r in rows
    ]


def save_tax_lot(lot: TaxLot) -> TaxLot:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT INTO tax_lots (symbol, broker, quantity, cost_basis, acquired_at) VALUES (?, ?, ?, ?, ?)",
        (lot.symbol, lot.broker.value, lot.quantity, lot.cost_basis, lot.acquired_at),
    )
    lot.id = cur.lastrowid
    lot.compute_holding_period()
    conn.commit()
    conn.close()
    return lot


def load_tax_lots(symbol: str | None = None, broker: str | None = None) -> list[TaxLot]:
    conn = _get_conn()
    if symbol and broker:
        rows = conn.execute(
            "SELECT * FROM tax_lots WHERE symbol = ? AND broker = ? ORDER BY acquired_at",
            (symbol, broker),
        ).fetchall()
    elif symbol:
        rows = conn.execute(
            "SELECT * FROM tax_lots WHERE symbol = ? ORDER BY acquired_at", (symbol,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tax_lots ORDER BY symbol, acquired_at").fetchall()
    conn.close()
    lots = [
        TaxLot(
            id=r["id"],
            symbol=r["symbol"],
            broker=BrokerName(r["broker"]),
            quantity=r["quantity"],
            cost_basis=r["cost_basis"],
            acquired_at=r["acquired_at"],
        )
        for r in rows
    ]
    for lot in lots:
        lot.compute_holding_period()
    return lots


def delete_tax_lot(lot_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM tax_lots WHERE id = ?", (lot_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def save_closed_position(cp: ClosedPosition) -> ClosedPosition:
    """Insert a closed position and return it with its new id."""
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO closed_positions
        (symbol, name, broker, quantity, average_cost, close_price,
         realized_gain, realized_gain_pct, acquired_at, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cp.symbol, cp.name, cp.broker.value, cp.quantity,
            cp.average_cost, cp.close_price, cp.realized_gain,
            cp.realized_gain_pct, cp.acquired_at, cp.closed_at,
        ),
    )
    cp.id = cur.lastrowid
    conn.commit()
    conn.close()
    return cp


def load_closed_positions() -> list[ClosedPosition]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM closed_positions ORDER BY closed_at DESC"
    ).fetchall()
    conn.close()
    return [
        ClosedPosition(
            id=r["id"],
            symbol=r["symbol"],
            name=r["name"],
            broker=BrokerName(r["broker"]),
            quantity=r["quantity"],
            average_cost=r["average_cost"],
            close_price=r["close_price"],
            realized_gain=r["realized_gain"],
            realized_gain_pct=r["realized_gain_pct"],
            acquired_at=r["acquired_at"],
            closed_at=r["closed_at"],
        )
        for r in rows
    ]


def delete_closed_position(position_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM closed_positions WHERE id = ?", (position_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def save_snapshot(positions: list[Position]) -> None:
    total_value = sum(p.market_value for p in positions)
    total_cost = sum(p.quantity * p.average_cost for p in positions)
    total_gain = total_value - total_cost
    positions_json = json.dumps([p.model_dump(mode="json") for p in positions])

    conn = _get_conn()
    conn.execute(
        """INSERT INTO snapshots (timestamp, total_value, total_cost, total_gain, positions_json)
        VALUES (?, ?, ?, ?, ?)""",
        (datetime.now().isoformat(), total_value, total_cost, total_gain, positions_json),
    )
    conn.commit()
    conn.close()


def load_snapshots(limit: int = 90) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT timestamp, total_value, total_cost, total_gain FROM snapshots ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_daily_snapshots(days: int = 400) -> list[dict]:
    """Return the last snapshot of each calendar day (for charting)."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT substr(timestamp,1,10) as date,
                  total_value, total_cost, total_gain
           FROM snapshots
           WHERE id IN (
               SELECT MAX(id) FROM snapshots GROUP BY substr(timestamp,1,10)
           )
           ORDER BY date ASC
           LIMIT ?""",
        (days,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Portfolio History (manual year-end entries) ───────────────

def init_portfolio_history_table() -> None:
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL UNIQUE,
            total_value REAL NOT NULL,
            label       TEXT DEFAULT '',
            is_estimate INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    conn.close()


def save_portfolio_history_entry(
    date: str, total_value: float, label: str = "", is_estimate: bool = True
) -> dict:
    conn = _get_conn()
    cur = conn.execute(
        "INSERT OR REPLACE INTO portfolio_history (date, total_value, label, is_estimate) VALUES (?,?,?,?)",
        (date, total_value, label, 1 if is_estimate else 0),
    )
    entry_id = cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": entry_id, "date": date, "total_value": total_value,
            "label": label, "is_estimate": is_estimate}


def load_portfolio_history() -> list[dict]:
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, date, total_value, label, is_estimate FROM portfolio_history ORDER BY date"
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def delete_portfolio_history_entry(entry_id: int) -> bool:
    conn = _get_conn()
    cur = conn.execute("DELETE FROM portfolio_history WHERE id = ?", (entry_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ── Signals persistence ────────────────────────────────────────

def save_signals(signals: list[dict]) -> None:
    """Save a batch of signals (replaces previous signals for each symbol)."""
    if not signals:
        return
    conn = _get_conn()
    symbols_seen = set(s["symbol"] for s in signals)
    for sym in symbols_seen:
        conn.execute("DELETE FROM signals WHERE symbol = ?", (sym,))
    for s in signals:
        conn.execute(
            """INSERT INTO signals
            (symbol, signal_type, direction, conviction, name, description, data_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (s["symbol"], s["signal_type"], s["direction"], s["conviction"],
             s["name"], s["description"], s.get("data_json", "{}"),
             s["timestamp"]),
        )
    conn.commit()
    conn.close()


def load_signals(symbol: str | None = None) -> list[dict]:
    """Load signals, optionally filtered by symbol."""
    conn = _get_conn()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC", (symbol,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY symbol, timestamp DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_signals(symbol: str | None = None) -> int:
    """Clear signals, optionally for a specific symbol."""
    conn = _get_conn()
    if symbol:
        cur = conn.execute("DELETE FROM signals WHERE symbol = ?", (symbol,))
    else:
        cur = conn.execute("DELETE FROM signals")
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
