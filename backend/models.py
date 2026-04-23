from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class BrokerName(str, Enum):
    ROBINHOOD = "robinhood"
    ETRADE = "etrade"
    CSV = "csv"


class Position(BaseModel):
    """A single holding in one broker account."""
    id: int | None = None
    symbol: str
    name: str = ""
    quantity: float
    average_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    unrealized_gain: float = 0.0
    unrealized_gain_pct: float = 0.0
    broker: BrokerName
    account_id: str = ""
    asset_type: str = "stock"  # stock, etf, option, crypto
    updated_at: datetime = Field(default_factory=datetime.now)

    def compute_derived(self) -> None:
        """Recalculate market_value and gain fields from price data."""
        self.market_value = self.quantity * self.current_price
        cost_basis = self.quantity * self.average_cost
        self.unrealized_gain = self.market_value - cost_basis
        if cost_basis > 0:
            self.unrealized_gain_pct = (self.unrealized_gain / cost_basis) * 100
        self.updated_at = datetime.now()


class PortfolioSummary(BaseModel):
    """Aggregated view across all brokers."""
    total_value: float = 0.0
    total_cost: float = 0.0
    total_gain: float = 0.0
    total_gain_pct: float = 0.0
    positions: list[Position] = []
    broker_breakdown: dict[str, float] = {}  # broker -> total value
    sector_breakdown: dict[str, float] = {}  # sector -> total value
    last_refresh: datetime | None = None

    def compute_from_positions(self) -> None:
        self.total_value = sum(p.market_value for p in self.positions)
        self.total_cost = sum(p.quantity * p.average_cost for p in self.positions)
        self.total_gain = self.total_value - self.total_cost
        if self.total_cost > 0:
            self.total_gain_pct = (self.total_gain / self.total_cost) * 100

        self.broker_breakdown = {}
        for p in self.positions:
            self.broker_breakdown[p.broker.value] = (
                self.broker_breakdown.get(p.broker.value, 0) + p.market_value
            )
        self.last_refresh = datetime.now()


class TaxLot(BaseModel):
    """A single purchase lot for cost-basis / tax tracking."""
    id: int | None = None
    symbol: str
    broker: BrokerName
    quantity: float
    cost_basis: float          # price per share at purchase
    acquired_at: str           # YYYY-MM-DD
    holding_period: str = ""   # computed: "long" (≥1 yr) or "short" (<1 yr)

    def compute_holding_period(self) -> None:
        from datetime import date
        days = (date.today() - date.fromisoformat(self.acquired_at)).days
        self.holding_period = "long" if days >= 365 else "short"


class ClosedPosition(BaseModel):
    """A position that has been fully sold/closed."""
    id: int | None = None
    symbol: str
    name: str = ""
    broker: BrokerName
    quantity: float
    average_cost: float
    close_price: float
    realized_gain: float = 0.0
    realized_gain_pct: float = 0.0
    acquired_at: str | None = None  # ISO date string YYYY-MM-DD
    closed_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    def compute_derived(self) -> None:
        cost_basis = self.quantity * self.average_cost
        self.realized_gain = (self.close_price - self.average_cost) * self.quantity
        if cost_basis > 0:
            self.realized_gain_pct = (self.realized_gain / cost_basis) * 100


class Snapshot(BaseModel):
    """Point-in-time portfolio snapshot for history tracking."""
    timestamp: datetime = Field(default_factory=datetime.now)
    total_value: float
    total_cost: float
    total_gain: float
    positions_json: str  # JSON-serialized positions
