"""Signal data models for the trading signal system."""
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class SignalType(str, Enum):
    TECHNICAL = "technical"
    SENTIMENT = "sentiment"
    OPTIONS_FLOW = "options_flow"
    INSIDER = "insider"
    CUSTOM = "custom"


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Signal(BaseModel):
    """A single trading signal from any source."""
    symbol: str
    signal_type: SignalType
    direction: SignalDirection
    conviction: int = Field(ge=1, le=5, description="1=weak, 5=very strong")
    name: str
    description: str
    data: dict = {}
    timestamp: datetime = Field(default_factory=datetime.now)


class SymbolSignalSummary(BaseModel):
    """Aggregated signal view for one symbol."""
    symbol: str
    signals: list[Signal] = []
    indicators: dict = {}
    composite_score: float = 0.0
    direction: SignalDirection = SignalDirection.NEUTRAL
    signal_count: int = 0
    last_updated: datetime = Field(default_factory=datetime.now)

    def compute_composite(self) -> None:
        """Compute composite score from individual signals.

        Bullish signals add conviction points, bearish subtract.
        Score clamped to [-10, 10]. Direction thresholds: >=3 bullish, <=-3 bearish.
        """
        if not self.signals:
            return
        score = 0.0
        for s in self.signals:
            if s.direction == SignalDirection.BULLISH:
                score += s.conviction
            elif s.direction == SignalDirection.BEARISH:
                score -= s.conviction
        self.composite_score = max(-10.0, min(10.0, score))
        self.signal_count = len(self.signals)
        if self.composite_score >= 3:
            self.direction = SignalDirection.BULLISH
        elif self.composite_score <= -3:
            self.direction = SignalDirection.BEARISH
        else:
            self.direction = SignalDirection.NEUTRAL
        self.last_updated = datetime.now()
