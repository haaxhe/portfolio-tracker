import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import db
from backend.config import settings
from backend.models import BrokerName, Position
from backend.portfolio import refresh_all


class PriceCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.DB_PATH
        settings.DB_PATH = str(Path(self.tmpdir.name) / "portfolio.db")
        db.init_db()
        db.init_portfolio_history_table()

    def tearDown(self) -> None:
        settings.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def _position(self, user_id: str) -> None:
        pos = Position(
            symbol="AAPL",
            name="Apple Inc.",
            quantity=2,
            average_cost=150,
            current_price=175,
            broker=BrokerName.CSV,
            asset_type="stock",
        )
        db.upsert_position(pos, user_id=user_id)

    def test_price_cache_reuses_symbol_across_users(self) -> None:
        self._position("u1")
        self._position("u2")
        calls = []

        def fake_alpaca(symbols):
            calls.append(tuple(symbols))
            return {
                "AAPL": {
                    "dates": ["2026-05-11"],
                    "closes": [190.0],
                }
            }

        with patch("backend.brokers.yfinance_updater._fetch_alpaca_history", side_effect=fake_alpaca):
            asyncio.run(refresh_all(user_id="u1"))
            asyncio.run(refresh_all(user_id="u2"))

        self.assertEqual(calls, [("AAPL",)])
        self.assertEqual(db.load_positions(user_id="u1")[0].current_price, 190.0)
        self.assertEqual(db.load_positions(user_id="u2")[0].current_price, 190.0)


if __name__ == "__main__":
    unittest.main()
