"""Persistence for positions and historical snapshots.

SQLite remains the default local development store. Set DATABASE_URL to a
Postgres URL for hosted deployments such as Supabase.
"""
import json
import sqlite3
import logging
from datetime import datetime
from backend.models import Position, ClosedPosition, TaxLot, Snapshot, BrokerName
from backend.config import settings

logger = logging.getLogger(__name__)


def _using_postgres() -> bool:
    return settings.DATABASE_URL.startswith(("postgres://", "postgresql://"))


def _owner(user_id: str | None = None) -> str:
    return user_id or settings.DEFAULT_USER_ID


class _PostgresConn:
    def __init__(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as e:
            raise RuntimeError(
                "DATABASE_URL is set to Postgres, but psycopg is not installed. "
                "Install requirements.txt in the deployment environment."
            ) from e

        self._conn = psycopg.connect(settings.DATABASE_URL, row_factory=dict_row)

    def execute(self, sql: str, params: tuple | list = ()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _get_conn() -> sqlite3.Connection | _PostgresConn:
    if _using_postgres():
        return _PostgresConn()
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if _using_postgres():
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
        return
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _insert_and_get_id(conn, sql: str, params: tuple) -> int:
    if _using_postgres():
        row = conn.execute(f"{sql} RETURNING id", params).fetchone()
        return int(row["id"])
    cur = conn.execute(sql, params)
    return int(cur.lastrowid)


def init_db() -> None:
    conn = _get_conn()
    if _using_postgres():
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            quantity DOUBLE PRECISION DEFAULT 0,
            average_cost DOUBLE PRECISION DEFAULT 0,
            current_price DOUBLE PRECISION DEFAULT 0,
            market_value DOUBLE PRECISION DEFAULT 0,
            unrealized_gain DOUBLE PRECISION DEFAULT 0,
            unrealized_gain_pct DOUBLE PRECISION DEFAULT 0,
            broker TEXT NOT NULL,
            account_id TEXT DEFAULT '',
            asset_type TEXT DEFAULT 'stock',
            acquired_at TEXT DEFAULT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tax_lots (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            cost_basis DOUBLE PRECISION NOT NULL,
            acquired_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS closed_positions (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            name TEXT DEFAULT '',
            broker TEXT NOT NULL,
            quantity DOUBLE PRECISION DEFAULT 0,
            average_cost DOUBLE PRECISION DEFAULT 0,
            close_price DOUBLE PRECISION DEFAULT 0,
            realized_gain DOUBLE PRECISION DEFAULT 0,
            realized_gain_pct DOUBLE PRECISION DEFAULT 0,
            acquired_at TEXT DEFAULT NULL,
            closed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            timestamp TEXT NOT NULL,
            total_value DOUBLE PRECISION NOT NULL,
            total_cost DOUBLE PRECISION NOT NULL,
            total_gain DOUBLE PRECISION NOT NULL,
            positions_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS signals (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            conviction INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            data_json TEXT DEFAULT '{}',
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS symbol_prices (
            symbol TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            current_price DOUBLE PRECISION NOT NULL,
            history_json TEXT DEFAULT '{}',
            source TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, asset_type)
        );
        """)
    else:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'local-user',
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
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            quantity REAL NOT NULL,
            cost_basis REAL NOT NULL,
            acquired_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tax_lots_sym ON tax_lots(symbol, broker);

        CREATE TABLE IF NOT EXISTS closed_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'local-user',
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
            user_id TEXT NOT NULL DEFAULT 'local-user',
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
            user_id TEXT NOT NULL DEFAULT 'local-user',
            symbol TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            conviction INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            data_json TEXT DEFAULT '{}',
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS symbol_prices (
            symbol TEXT NOT NULL,
            asset_type TEXT NOT NULL DEFAULT 'stock',
            current_price REAL NOT NULL,
            history_json TEXT DEFAULT '{}',
            source TEXT DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, asset_type)
        );
        CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
    """)
    for table in ("positions", "tax_lots", "closed_positions", "snapshots", "signals"):
        _ensure_column(conn, table, "user_id", f"TEXT NOT NULL DEFAULT '{settings.DEFAULT_USER_ID}'")
    _ensure_column(conn, "positions", "acquired_at", "TEXT DEFAULT NULL")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_tax_lots_user_sym ON tax_lots(user_id, symbol, broker);
        CREATE INDEX IF NOT EXISTS idx_positions_user_broker ON positions(user_id, broker);
        CREATE INDEX IF NOT EXISTS idx_positions_user_symbol ON positions(user_id, symbol);
        CREATE INDEX IF NOT EXISTS idx_snapshots_user_ts ON snapshots(user_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_signals_user_symbol ON signals(user_id, symbol);
        CREATE INDEX IF NOT EXISTS idx_signals_user_ts ON signals(user_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_symbol_prices_updated ON symbol_prices(updated_at);
    """)
    if not _using_postgres():
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_symbol_prices_updated ON symbol_prices(updated_at)"
        )
    conn.commit()
    conn.close()
    logger.info("Database initialized")


def save_symbol_prices(
    prices: dict[tuple[str, str], dict],
    source: str = "",
) -> None:
    """Upsert global symbol price cache rows keyed by (symbol, asset_type)."""
    if not prices:
        return

    conn = _get_conn()
    now = datetime.now().isoformat()
    for (symbol, asset_type), payload in prices.items():
        price = payload.get("current_price")
        if price is None:
            continue
        history = payload.get("history") or {}
        cache_source = payload.get("source") or source
        conn.execute(
            """INSERT INTO symbol_prices
               (symbol, asset_type, current_price, history_json, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, asset_type) DO UPDATE SET
                 current_price = excluded.current_price,
                 history_json = excluded.history_json,
                 source = excluded.source,
                 updated_at = excluded.updated_at""",
            (
                symbol.upper().strip(),
                asset_type,
                float(price),
                json.dumps(history),
                cache_source,
                now,
            ),
        )
    conn.commit()
    conn.close()


def load_symbol_prices(
    symbols: list[tuple[str, str]],
    max_age_seconds: int | None = None,
) -> dict[tuple[str, str], dict]:
    """Load global cached prices, optionally filtering out stale rows."""
    if not symbols:
        return {}

    wanted = {(symbol.upper().strip(), asset_type) for symbol, asset_type in symbols}
    conn = _get_conn()
    results: dict[tuple[str, str], dict] = {}
    for symbol, asset_type in wanted:
        row = conn.execute(
            """SELECT symbol, asset_type, current_price, history_json, source, updated_at
               FROM symbol_prices WHERE symbol = ? AND asset_type = ?""",
            (symbol, asset_type),
        ).fetchone()
        if not row:
            continue
        updated_at = datetime.fromisoformat(row["updated_at"])
        if max_age_seconds is not None:
            age_seconds = (datetime.now() - updated_at).total_seconds()
            if age_seconds > max_age_seconds:
                continue
        try:
            history = json.loads(row["history_json"] or "{}")
        except json.JSONDecodeError:
            history = {}
        results[(row["symbol"], row["asset_type"])] = {
            "current_price": row["current_price"],
            "history": history,
            "source": row["source"],
            "updated_at": row["updated_at"],
        }
    conn.close()
    return results


def upsert_cash(broker: str, amount: float, user_id: str | None = None) -> None:
    """Set the cash balance for a broker, replacing any existing cash row."""
    from datetime import datetime
    owner = _owner(user_id)
    conn = _get_conn()
    conn.execute(
        "DELETE FROM positions WHERE user_id = ? AND broker = ? AND symbol = 'CASH'",
        (owner, broker),
    )
    if amount > 0:
        conn.execute(
            """INSERT INTO positions
            (user_id, symbol, name, quantity, average_cost, current_price,
             market_value, unrealized_gain, unrealized_gain_pct,
             broker, account_id, asset_type, updated_at)
            VALUES (?,'CASH','Cash',?,1.0,1.0,?,0.0,0.0,?,'','cash',?)""",
            (owner, amount, amount, broker, datetime.now().isoformat()),
        )
    conn.commit()
    conn.close()


def upsert_position(position: Position, user_id: str | None = None) -> Position:
    """Insert or update one non-cash position without replacing broker holdings."""
    owner = _owner(user_id)
    position.symbol = position.symbol.upper().strip()
    position.name = position.name or position.symbol
    position.compute_derived()

    conn = _get_conn()
    row = conn.execute(
        """SELECT id FROM positions
           WHERE user_id = ? AND symbol = ? AND broker = ? AND asset_type != 'cash'
           ORDER BY id LIMIT 1""",
        (owner, position.symbol, position.broker.value),
    ).fetchone()

    if row:
        conn.execute(
            """UPDATE positions SET
               name = ?, quantity = ?, average_cost = ?, current_price = ?,
               market_value = ?, unrealized_gain = ?, unrealized_gain_pct = ?,
               account_id = ?, asset_type = ?, updated_at = ?
               WHERE id = ?""",
            (
                position.name,
                position.quantity,
                position.average_cost,
                position.current_price,
                position.market_value,
                position.unrealized_gain,
                position.unrealized_gain_pct,
                position.account_id,
                position.asset_type,
                position.updated_at.isoformat(),
                row["id"],
            ),
        )
        position.id = row["id"]
    else:
        insert_sql = """INSERT INTO positions
            (user_id, symbol, name, quantity, average_cost, current_price,
             market_value, unrealized_gain, unrealized_gain_pct,
             broker, account_id, asset_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        position.id = _insert_and_get_id(
            conn,
            insert_sql,
            (
                owner,
                position.symbol,
                position.name,
                position.quantity,
                position.average_cost,
                position.current_price,
                position.market_value,
                position.unrealized_gain,
                position.unrealized_gain_pct,
                position.broker.value,
                position.account_id,
                position.asset_type,
                position.updated_at.isoformat(),
            ),
        )

    conn.commit()
    conn.close()
    return position


def save_positions(positions: list[Position], user_id: str | None = None) -> None:
    """Replace all non-cash positions for each broker present in the list."""
    owner = _owner(user_id)
    conn = _get_conn()
    brokers_seen = set(p.broker.value for p in positions)
    for broker in brokers_seen:
        conn.execute(
            "DELETE FROM positions WHERE user_id = ? AND broker = ? AND symbol != 'CASH'",
            (owner, broker),
        )

    for p in positions:
        conn.execute(
            """INSERT INTO positions
            (user_id, symbol, name, quantity, average_cost, current_price,
             market_value, unrealized_gain, unrealized_gain_pct,
             broker, account_id, asset_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                owner,
                p.symbol, p.name, p.quantity, p.average_cost, p.current_price,
                p.market_value, p.unrealized_gain, p.unrealized_gain_pct,
                p.broker.value, p.account_id, p.asset_type,
                p.updated_at.isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def load_positions(broker: str | None = None, user_id: str | None = None) -> list[Position]:
    owner = _owner(user_id)
    conn = _get_conn()
    if broker:
        rows = conn.execute(
            "SELECT * FROM positions WHERE user_id = ? AND broker = ?", (owner, broker)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM positions WHERE user_id = ?", (owner,)).fetchall()
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


def save_tax_lot(lot: TaxLot, user_id: str | None = None) -> TaxLot:
    owner = _owner(user_id)
    conn = _get_conn()
    lot.id = _insert_and_get_id(
        conn,
        "INSERT INTO tax_lots (user_id, symbol, broker, quantity, cost_basis, acquired_at) VALUES (?, ?, ?, ?, ?, ?)",
        (owner, lot.symbol, lot.broker.value, lot.quantity, lot.cost_basis, lot.acquired_at),
    )
    lot.compute_holding_period()
    conn.commit()
    conn.close()
    return lot


def load_tax_lots(
    symbol: str | None = None,
    broker: str | None = None,
    user_id: str | None = None,
) -> list[TaxLot]:
    owner = _owner(user_id)
    conn = _get_conn()
    if symbol and broker:
        rows = conn.execute(
            "SELECT * FROM tax_lots WHERE user_id = ? AND symbol = ? AND broker = ? ORDER BY acquired_at",
            (owner, symbol, broker),
        ).fetchall()
    elif symbol:
        rows = conn.execute(
            "SELECT * FROM tax_lots WHERE user_id = ? AND symbol = ? ORDER BY acquired_at",
            (owner, symbol),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM tax_lots WHERE user_id = ? ORDER BY symbol, acquired_at",
            (owner,),
        ).fetchall()
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


def delete_tax_lot(lot_id: int, user_id: str | None = None) -> bool:
    owner = _owner(user_id)
    conn = _get_conn()
    cur = conn.execute("DELETE FROM tax_lots WHERE user_id = ? AND id = ?", (owner, lot_id))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def get_tax_lot(lot_id: int, user_id: str | None = None) -> TaxLot | None:
    owner = _owner(user_id)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM tax_lots WHERE user_id = ? AND id = ?", (owner, lot_id)
    ).fetchone()
    conn.close()
    if not row:
        return None
    lot = TaxLot(
        id=row["id"],
        symbol=row["symbol"],
        broker=BrokerName(row["broker"]),
        quantity=row["quantity"],
        cost_basis=row["cost_basis"],
        acquired_at=row["acquired_at"],
    )
    lot.compute_holding_period()
    return lot


def update_tax_lot(
    lot_id: int,
    quantity: float | None = None,
    cost_basis: float | None = None,
    acquired_at: str | None = None,
    user_id: str | None = None,
) -> TaxLot | None:
    """Patch any subset of fields on an existing lot. Deletes if quantity drops to ≤ 0."""
    owner = _owner(user_id)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM tax_lots WHERE user_id = ? AND id = ?", (owner, lot_id)
    ).fetchone()
    if not row:
        conn.close()
        return None

    new_qty = row["quantity"] if quantity is None else quantity
    new_cost = row["cost_basis"] if cost_basis is None else cost_basis
    new_date = row["acquired_at"] if acquired_at is None else acquired_at

    if new_qty <= 0:
        conn.execute("DELETE FROM tax_lots WHERE user_id = ? AND id = ?", (owner, lot_id))
        conn.commit()
        conn.close()
        return None

    conn.execute(
        "UPDATE tax_lots SET quantity = ?, cost_basis = ?, acquired_at = ? WHERE user_id = ? AND id = ?",
        (new_qty, new_cost, new_date, owner, lot_id),
    )
    conn.commit()
    conn.close()
    return get_tax_lot(lot_id, user_id=owner)


def decrement_position_quantity(
    symbol: str,
    broker: str,
    delta: float,
    user_id: str | None = None,
) -> None:
    """Reduce a position's quantity by delta and recompute derived fields.
    Deletes the row if the resulting quantity is ≤ 0. No-op if the position
    doesn't exist (e.g. live broker feed will reseed on next refresh)."""
    owner = _owner(user_id)
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM positions WHERE user_id = ? AND symbol = ? AND broker = ? AND symbol != 'CASH'",
        (owner, symbol, broker),
    ).fetchone()
    if not row:
        conn.close()
        return

    new_qty = row["quantity"] - delta
    if new_qty <= 0:
        conn.execute("DELETE FROM positions WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return

    avg_cost = row["average_cost"]
    price = row["current_price"]
    market_value = new_qty * price
    cost_basis = new_qty * avg_cost
    unrealized_gain = market_value - cost_basis
    unrealized_gain_pct = (unrealized_gain / cost_basis * 100) if cost_basis else 0
    conn.execute(
        """UPDATE positions SET quantity = ?, market_value = ?,
           unrealized_gain = ?, unrealized_gain_pct = ?, updated_at = ?
           WHERE id = ?""",
        (new_qty, market_value, unrealized_gain, unrealized_gain_pct,
         datetime.now().isoformat(), row["id"]),
    )
    conn.commit()
    conn.close()


def save_closed_position(cp: ClosedPosition, user_id: str | None = None) -> ClosedPosition:
    """Insert a closed position and return it with its new id."""
    owner = _owner(user_id)
    conn = _get_conn()
    cp.id = _insert_and_get_id(
        conn,
        """INSERT INTO closed_positions
        (user_id, symbol, name, broker, quantity, average_cost, close_price,
         realized_gain, realized_gain_pct, acquired_at, closed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            owner,
            cp.symbol, cp.name, cp.broker.value, cp.quantity,
            cp.average_cost, cp.close_price, cp.realized_gain,
            cp.realized_gain_pct, cp.acquired_at, cp.closed_at,
        ),
    )
    conn.commit()
    conn.close()
    return cp


def load_closed_positions(user_id: str | None = None) -> list[ClosedPosition]:
    owner = _owner(user_id)
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM closed_positions WHERE user_id = ? ORDER BY closed_at DESC",
        (owner,),
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


def delete_closed_position(position_id: int, user_id: str | None = None) -> bool:
    owner = _owner(user_id)
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM closed_positions WHERE user_id = ? AND id = ?",
        (owner, position_id),
    )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def save_snapshot(positions: list[Position], user_id: str | None = None) -> None:
    owner = _owner(user_id)
    total_value = sum(p.market_value for p in positions)
    total_cost = sum(p.quantity * p.average_cost for p in positions)
    total_gain = total_value - total_cost
    positions_json = json.dumps([p.model_dump(mode="json") for p in positions])

    conn = _get_conn()
    conn.execute(
        """INSERT INTO snapshots (user_id, timestamp, total_value, total_cost, total_gain, positions_json)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (owner, datetime.now().isoformat(), total_value, total_cost, total_gain, positions_json),
    )
    conn.commit()
    conn.close()


def load_snapshots(limit: int = 90, user_id: str | None = None) -> list[dict]:
    owner = _owner(user_id)
    conn = _get_conn()
    rows = conn.execute(
        """SELECT timestamp, total_value, total_cost, total_gain
           FROM snapshots WHERE user_id = ?
           ORDER BY timestamp DESC LIMIT ?""",
        (owner, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_daily_snapshots(days: int = 400, user_id: str | None = None) -> list[dict]:
    """Return the last snapshot of each calendar day (for charting)."""
    owner = _owner(user_id)
    conn = _get_conn()
    rows = conn.execute(
        """SELECT substr(timestamp,1,10) as date,
                  total_value, total_cost, total_gain
           FROM snapshots
           WHERE user_id = ?
             AND id IN (
               SELECT MAX(id) FROM snapshots WHERE user_id = ? GROUP BY substr(timestamp,1,10)
           )
           ORDER BY date ASC
           LIMIT ?""",
        (owner, owner, days),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_broker_positions(broker_name: str, user_id: str | None = None) -> int:
    """Delete all non-cash positions for one broker owned by the user."""
    owner = _owner(user_id)
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM positions WHERE user_id = ? AND broker = ? AND symbol != 'CASH'",
        (owner, broker_name),
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


# ── Portfolio History (manual year-end entries) ───────────────

def init_portfolio_history_table() -> None:
    conn = _get_conn()
    if _using_postgres():
        conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id BIGSERIAL PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'local-user',
            date TEXT NOT NULL,
            total_value DOUBLE PRECISION NOT NULL,
            label TEXT DEFAULT '',
            is_estimate INTEGER DEFAULT 1,
            UNIQUE(user_id, date)
        )
        """)
    else:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS portfolio_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL DEFAULT 'local-user',
            date        TEXT NOT NULL,
            total_value REAL NOT NULL,
            label       TEXT DEFAULT '',
            is_estimate INTEGER DEFAULT 1,
            UNIQUE(user_id, date)
        )
    """)
    _ensure_column(conn, "portfolio_history", "user_id", f"TEXT NOT NULL DEFAULT '{settings.DEFAULT_USER_ID}'")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolio_history_user_date ON portfolio_history(user_id, date)"
    )
    conn.commit()
    conn.close()


def save_portfolio_history_entry(
    date: str,
    total_value: float,
    label: str = "",
    is_estimate: bool = True,
    user_id: str | None = None,
) -> dict:
    owner = _owner(user_id)
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO portfolio_history (user_id, date, total_value, label, is_estimate)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, date) DO UPDATE SET
             total_value = excluded.total_value,
             label = excluded.label,
             is_estimate = excluded.is_estimate""",
        (owner, date, total_value, label, 1 if is_estimate else 0),
    )
    row = conn.execute(
        "SELECT id FROM portfolio_history WHERE user_id = ? AND date = ?", (owner, date)
    ).fetchone()
    entry_id = row["id"] if row else cur.lastrowid
    conn.commit()
    conn.close()
    return {"id": entry_id, "date": date, "total_value": total_value,
            "label": label, "is_estimate": is_estimate}


def load_portfolio_history(user_id: str | None = None) -> list[dict]:
    owner = _owner(user_id)
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, date, total_value, label, is_estimate
               FROM portfolio_history WHERE user_id = ? ORDER BY date""",
            (owner,),
        ).fetchall()
    except Exception:
        rows = []
    conn.close()
    return [dict(r) for r in rows]


def delete_portfolio_history_entry(entry_id: int, user_id: str | None = None) -> bool:
    owner = _owner(user_id)
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM portfolio_history WHERE user_id = ? AND id = ?", (owner, entry_id)
    )
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


# ── Signals persistence ────────────────────────────────────────

def save_signals(signals: list[dict], user_id: str | None = None) -> None:
    """Save a batch of signals (replaces previous signals for each symbol)."""
    if not signals:
        return
    owner = _owner(user_id)
    conn = _get_conn()
    symbols_seen = set(s["symbol"] for s in signals)
    for sym in symbols_seen:
        conn.execute("DELETE FROM signals WHERE user_id = ? AND symbol = ?", (owner, sym))
    for s in signals:
        conn.execute(
            """INSERT INTO signals
            (user_id, symbol, signal_type, direction, conviction, name, description, data_json, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner, s["symbol"], s["signal_type"], s["direction"], s["conviction"],
             s["name"], s["description"], s.get("data_json", "{}"),
             s["timestamp"]),
        )
    conn.commit()
    conn.close()


def load_signals(symbol: str | None = None, user_id: str | None = None) -> list[dict]:
    """Load signals, optionally filtered by symbol."""
    owner = _owner(user_id)
    conn = _get_conn()
    if symbol:
        rows = conn.execute(
            "SELECT * FROM signals WHERE user_id = ? AND symbol = ? ORDER BY timestamp DESC",
            (owner, symbol),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM signals WHERE user_id = ? ORDER BY symbol, timestamp DESC",
            (owner,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def clear_signals(symbol: str | None = None, user_id: str | None = None) -> int:
    """Clear signals, optionally for a specific symbol."""
    owner = _owner(user_id)
    conn = _get_conn()
    if symbol:
        cur = conn.execute("DELETE FROM signals WHERE user_id = ? AND symbol = ?", (owner, symbol))
    else:
        cur = conn.execute("DELETE FROM signals WHERE user_id = ?", (owner,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted
