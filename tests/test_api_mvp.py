import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend import db
from backend.config import settings
from backend.main import app


class ApiMvpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_db_path = settings.DB_PATH
        self.original_auth_mode = settings.AUTH_MODE
        self.original_token = settings.API_TOKEN
        self.original_default_user = settings.DEFAULT_USER_ID
        self.original_supabase_url = settings.SUPABASE_URL
        self.original_supabase_key = settings.SUPABASE_PUBLISHABLE_KEY
        settings.DB_PATH = str(Path(self.tmpdir.name) / "portfolio.db")
        settings.AUTH_MODE = "token"
        settings.API_TOKEN = "test-token"
        settings.DEFAULT_USER_ID = "fallback-user"
        db.init_db()
        db.init_portfolio_history_table()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        settings.DB_PATH = self.original_db_path
        settings.AUTH_MODE = self.original_auth_mode
        settings.API_TOKEN = self.original_token
        settings.DEFAULT_USER_ID = self.original_default_user
        settings.SUPABASE_URL = self.original_supabase_url
        settings.SUPABASE_PUBLISHABLE_KEY = self.original_supabase_key
        self.tmpdir.cleanup()

    def _headers(self, user_id: str) -> dict[str, str]:
        return {
            "Authorization": "Bearer test-token",
            "X-User-Id": user_id,
        }

    def test_api_requires_token_in_token_mode(self) -> None:
        response = self.client.get("/api/portfolio")
        self.assertEqual(response.status_code, 401)

    def test_portfolio_routes_are_scoped_by_user(self) -> None:
        payload = {
            "symbol": "AAPL",
            "broker": "csv",
            "quantity": 2,
            "average_cost": 10,
            "current_price": 15,
            "asset_type": "stock",
        }
        created = self.client.post(
            "/api/positions/upsert",
            json=payload,
            headers=self._headers("u1"),
        )
        self.assertEqual(created.status_code, 200)

        u1 = self.client.get("/api/portfolio", headers=self._headers("u1")).json()
        u2 = self.client.get("/api/portfolio", headers=self._headers("u2")).json()

        self.assertEqual([p["symbol"] for p in u1["positions"]], ["AAPL"])
        self.assertEqual(u2["positions"], [])

    def test_supabase_auth_uses_verified_user_id(self) -> None:
        settings.AUTH_MODE = "supabase"
        settings.SUPABASE_URL = "https://project.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "publishable-key"

        response = Mock()
        response.status_code = 200
        response.json.return_value = {"id": "supabase-user-1", "email": "u@example.com"}

        with patch("backend.auth.requests.get", return_value=response) as get:
            created = self.client.post(
                "/api/positions/upsert",
                json={
                    "symbol": "MSFT",
                    "broker": "csv",
                    "quantity": 1,
                    "average_cost": 100,
                    "current_price": 110,
                    "asset_type": "stock",
                },
                headers={"Authorization": "Bearer supabase-token"},
            )

        self.assertEqual(created.status_code, 200)
        self.assertEqual(
            [p.symbol for p in db.load_positions(user_id="supabase-user-1")],
            ["MSFT"],
        )
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
