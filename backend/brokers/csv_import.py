"""
CSV import — the Day 1 approach.

Supports common CSV formats from Robinhood and E*Trade exports.
Auto-detects columns by matching common header names.
"""
import csv
import io
import logging
from backend.brokers import BaseBroker
from backend.models import Position, BrokerName

logger = logging.getLogger(__name__)

# Common column name mappings (lowercase)
SYMBOL_COLS = {"symbol", "ticker", "instrument"}
NAME_COLS = {"name", "description", "security", "company"}
QTY_COLS = {"quantity", "qty", "shares", "amount"}
AVG_COST_COLS = {"average cost", "avg cost", "average_cost", "cost basis per share", "avg_price", "average buy price", "average price"}
PRICE_COLS = {"current price", "price", "last price", "market price", "current_price", "last_trade"}
VALUE_COLS = {"market value", "market_value", "value", "total value", "equity"}
TYPE_COLS = {"type", "asset type", "asset_type", "security type"}


def _match_col(headers: list[str], candidates: set[str]) -> str | None:
    """Find which header matches a set of candidate names."""
    for h in headers:
        if h.lower().strip() in candidates:
            return h
    return None


class CSVImporter:
    """Parses uploaded CSV into Position objects."""

    @staticmethod
    def parse(
        content: str,
        broker_label: BrokerName = BrokerName.CSV,
    ) -> list[Position]:
        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []

        sym_col = _match_col(headers, SYMBOL_COLS)
        name_col = _match_col(headers, NAME_COLS)
        qty_col = _match_col(headers, QTY_COLS)
        cost_col = _match_col(headers, AVG_COST_COLS)
        price_col = _match_col(headers, PRICE_COLS)
        value_col = _match_col(headers, VALUE_COLS)
        type_col = _match_col(headers, TYPE_COLS)

        if not sym_col:
            raise ValueError(
                f"Could not find a symbol/ticker column. Headers found: {headers}"
            )

        positions = []
        for row in reader:
            symbol = row.get(sym_col, "").strip().upper()
            if not symbol:
                continue

            qty = _safe_float(row.get(qty_col, "0")) if qty_col else 0.0
            avg_cost = _safe_float(row.get(cost_col, "0")) if cost_col else 0.0
            price = _safe_float(row.get(price_col, "0")) if price_col else 0.0

            pos = Position(
                symbol=symbol,
                name=row.get(name_col, symbol) if name_col else symbol,
                quantity=qty,
                average_cost=avg_cost,
                current_price=price,
                broker=broker_label,
                asset_type=row.get(type_col, "stock").lower() if type_col else "stock",
            )
            pos.compute_derived()
            positions.append(pos)

        logger.info(f"CSV import: parsed {len(positions)} positions")
        return positions


def _safe_float(val: str) -> float:
    """Parse a string to float, stripping currency symbols and commas."""
    if not val:
        return 0.0
    cleaned = val.replace("$", "").replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0
