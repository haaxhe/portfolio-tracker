import tempfile
import unittest
from pathlib import Path

from backend import db
from backend.config import settings
from backend.models import BrokerName, ClosedPosition, Position, TaxLot


class DbTenancyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.DB_PATH
        settings.DB_PATH = str(Path(self.tmpdir.name) / "portfolio.db")
        db.init_db()
        db.init_portfolio_history_table()

    def tearDown(self) -> None:
        settings.DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def _position(self, symbol: str) -> Position:
        pos = Position(
            symbol=symbol,
            name=symbol,
            quantity=2,
            average_cost=10,
            current_price=15,
            broker=BrokerName.CSV,
        )
        pos.compute_derived()
        return pos

    def test_positions_are_scoped_by_user(self) -> None:
        db.upsert_position(self._position("AAPL"), user_id="u1")
        db.upsert_position(self._position("MSFT"), user_id="u2")

        self.assertEqual([p.symbol for p in db.load_positions(user_id="u1")], ["AAPL"])
        self.assertEqual([p.symbol for p in db.load_positions(user_id="u2")], ["MSFT"])

    def test_tax_lots_and_closed_positions_are_scoped_by_user(self) -> None:
        lot = TaxLot(
            symbol="NVDA",
            broker=BrokerName.CSV,
            quantity=1,
            cost_basis=100,
            acquired_at="2025-01-01",
        )
        saved = db.save_tax_lot(lot, user_id="u1")

        self.assertIsNotNone(db.get_tax_lot(saved.id, user_id="u1"))
        self.assertIsNone(db.get_tax_lot(saved.id, user_id="u2"))
        self.assertFalse(db.delete_tax_lot(saved.id, user_id="u2"))

        closed = ClosedPosition(
            symbol="NVDA",
            broker=BrokerName.CSV,
            quantity=1,
            average_cost=100,
            close_price=125,
            closed_at="2026-01-02",
        )
        closed.compute_derived()
        db.save_closed_position(closed, user_id="u1")

        self.assertEqual(len(db.load_closed_positions(user_id="u1")), 1)
        self.assertEqual(db.load_closed_positions(user_id="u2"), [])

    def test_snapshots_and_history_are_scoped_by_user(self) -> None:
        db.save_snapshot([self._position("TSLA")], user_id="u1")
        db.save_snapshot([self._position("AMD")], user_id="u2")
        db.save_portfolio_history_entry("2026-01-01", 1000, user_id="u1")
        db.save_portfolio_history_entry("2026-01-01", 2000, user_id="u2")

        self.assertEqual(db.load_snapshots(user_id="u1")[0]["total_value"], 30)
        self.assertEqual(db.load_snapshots(user_id="u2")[0]["total_value"], 30)
        self.assertEqual(db.load_portfolio_history(user_id="u1")[0]["total_value"], 1000)
        self.assertEqual(db.load_portfolio_history(user_id="u2")[0]["total_value"], 2000)


if __name__ == "__main__":
    unittest.main()
