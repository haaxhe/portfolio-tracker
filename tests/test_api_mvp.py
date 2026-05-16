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
        self.original_trust_proxy_user_header = settings.TRUST_PROXY_USER_HEADER
        self.original_allow_legacy_dashboard = settings.ALLOW_LEGACY_DASHBOARD
        self.original_environment = settings.ENVIRONMENT
        self.original_database_url = settings.DATABASE_URL
        self.original_app_base_url = settings.APP_BASE_URL
        self.original_cors_origins = list(settings.CORS_ORIGINS)
        self.original_supabase_url = settings.SUPABASE_URL
        self.original_supabase_key = settings.SUPABASE_PUBLISHABLE_KEY
        settings.DB_PATH = str(Path(self.tmpdir.name) / "portfolio.db")
        settings.AUTH_MODE = "token"
        settings.API_TOKEN = "test-token"
        settings.DEFAULT_USER_ID = "fallback-user"
        settings.TRUST_PROXY_USER_HEADER = False
        settings.ENVIRONMENT = "local"
        db.init_db()
        db.init_portfolio_history_table()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        settings.DB_PATH = self.original_db_path
        settings.AUTH_MODE = self.original_auth_mode
        settings.API_TOKEN = self.original_token
        settings.DEFAULT_USER_ID = self.original_default_user
        settings.TRUST_PROXY_USER_HEADER = self.original_trust_proxy_user_header
        settings.ALLOW_LEGACY_DASHBOARD = self.original_allow_legacy_dashboard
        settings.ENVIRONMENT = self.original_environment
        settings.DATABASE_URL = self.original_database_url
        settings.APP_BASE_URL = self.original_app_base_url
        settings.CORS_ORIGINS = self.original_cors_origins
        settings.SUPABASE_URL = self.original_supabase_url
        settings.SUPABASE_PUBLISHABLE_KEY = self.original_supabase_key
        self.tmpdir.cleanup()

    def _headers(self, user_id: str | None = None, trusted_user_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": "Bearer test-token",
        }
        if user_id:
            headers["X-User-Id"] = user_id
        if trusted_user_id:
            headers["X-Authenticated-User-Id"] = trusted_user_id
        return headers

    def test_api_requires_token_in_token_mode(self) -> None:
        response = self.client.get("/api/portfolio")
        self.assertEqual(response.status_code, 401)

    def test_token_mode_ignores_browser_user_id(self) -> None:
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
        self.assertEqual([p["symbol"] for p in u2["positions"]], ["AAPL"])
        self.assertEqual([p.symbol for p in db.load_positions(user_id="fallback-user")], ["AAPL"])

    def test_csv_import_accepts_expanded_broker_labels(self) -> None:
        csv_body = (
            "Symbol,Description,Quantity,Cost Basis Per Share,Market Price,Security Type\n"
            "VTI,Vanguard Total Stock Market ETF,12,211.60,276.10,etf\n"
        )

        response = self.client.post(
            "/api/import/csv?broker=schwab",
            files={"file": ("schwab.csv", csv_body, "text/csv")},
            headers=self._headers("u1"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"imported": 1, "broker": "schwab"})

        portfolio = self.client.get("/api/portfolio", headers=self._headers("u1")).json()
        self.assertEqual(portfolio["positions"][0]["symbol"], "VTI")
        self.assertEqual(portfolio["positions"][0]["broker"], "schwab")

    def test_export_all_and_delete_account_data(self) -> None:
        created = self.client.post(
            "/api/positions/upsert",
            json={
                "symbol": "AAPL",
                "broker": "csv",
                "quantity": 2,
                "average_cost": 10,
                "current_price": 15,
                "asset_type": "stock",
            },
            headers=self._headers("u1"),
        )
        self.assertEqual(created.status_code, 200)

        exported = self.client.get("/api/export/all", headers=self._headers("u1"))
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()["positions"][0]["symbol"], "AAPL")

        deleted = self.client.delete("/api/account/data", headers=self._headers("u1"))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get("/api/portfolio", headers=self._headers("u1")).json()["positions"],
            [],
        )

    def test_analytics_accepts_anonymous_funnel_event(self) -> None:
        response = self.client.post(
            "/api/analytics/events",
            json={
                "event_name": "landing_view",
                "session_id": "anon-session-1",
                "path": "/",
                "referrer": "",
                "metadata": {"source": "direct"},
            },
        )

        self.assertEqual(response.status_code, 200)
        events = db.load_analytics_events(session_id="anon-session-1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_name"], "landing_view")
        self.assertIsNone(events[0]["user_id"])
        self.assertEqual(events[0]["metadata"], {"source": "direct"})

    def test_analytics_rejects_invalid_event_names(self) -> None:
        response = self.client.post(
            "/api/analytics/events",
            json={
                "event_name": "Landing View!",
                "session_id": "anon-session-1",
                "metadata": {},
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_analytics_attaches_authenticated_user_and_respects_data_controls(self) -> None:
        response = self.client.post(
            "/api/analytics/events",
            json={
                "event_name": "csv_import_success",
                "session_id": "session-auth-1",
                "path": "/",
                "metadata": {"broker": "csv", "imported": 2},
            },
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 200)

        events = db.load_analytics_events(user_id="fallback-user")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_name"], "csv_import_success")

        exported = self.client.get("/api/export/all", headers=self._headers())
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()["analytics_events"][0]["event_name"], "csv_import_success")

        deleted = self.client.delete("/api/account/data", headers=self._headers())
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["deleted"]["analytics_events"], 1)
        self.assertEqual(db.load_analytics_events(user_id="fallback-user"), [])

    def test_trusted_proxy_user_header_scopes_token_mode_when_enabled(self) -> None:
        settings.TRUST_PROXY_USER_HEADER = True

        created = self.client.post(
            "/api/positions/upsert",
            json={
                "symbol": "AAPL",
                "broker": "csv",
                "quantity": 2,
                "average_cost": 10,
                "current_price": 15,
                "asset_type": "stock",
            },
            headers=self._headers(user_id="ignored-browser-user", trusted_user_id="u1"),
        )
        self.assertEqual(created.status_code, 200)

        u1 = self.client.get("/api/portfolio", headers=self._headers(trusted_user_id="u1")).json()
        u2 = self.client.get("/api/portfolio", headers=self._headers(trusted_user_id="u2")).json()

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

    def test_production_startup_validation_fails_closed(self) -> None:
        settings.ENVIRONMENT = "production"
        settings.AUTH_MODE = "local"
        settings.DATABASE_URL = ""
        settings.APP_BASE_URL = "http://example.com"
        settings.CORS_ORIGINS = ["http://example.com"]

        with self.assertRaises(RuntimeError):
            settings.validate_for_startup()

    def test_production_startup_validation_accepts_supabase_https(self) -> None:
        settings.ENVIRONMENT = "production"
        settings.AUTH_MODE = "supabase"
        settings.DATABASE_URL = "postgresql://user:pass@db.example.com/postgres"
        settings.APP_BASE_URL = "https://getwealthbrief.com"
        settings.CORS_ORIGINS = ["https://getwealthbrief.com"]
        settings.SUPABASE_URL = "https://project.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "publishable-key"

        settings.validate_for_startup()

    def test_staging_startup_validation_accepts_supabase_postgres_on_localhost(self) -> None:
        settings.ENVIRONMENT = "staging"
        settings.AUTH_MODE = "supabase"
        settings.DATABASE_URL = "postgresql://user:pass@db.example.com/postgres"
        settings.APP_BASE_URL = "http://127.0.0.1:8000"
        settings.CORS_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]
        settings.SUPABASE_URL = "https://project.supabase.co"
        settings.SUPABASE_PUBLISHABLE_KEY = "publishable-key"

        settings.validate_for_startup()

    def test_staging_startup_validation_requires_live_like_services(self) -> None:
        settings.ENVIRONMENT = "staging"
        settings.AUTH_MODE = "local"
        settings.DATABASE_URL = ""
        settings.SUPABASE_URL = ""
        settings.SUPABASE_PUBLISHABLE_KEY = ""

        with self.assertRaises(RuntimeError):
            settings.validate_for_startup()

    def test_public_config_includes_environment(self) -> None:
        settings.ENVIRONMENT = "staging"

        response = self.client.get("/api/public-config")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["environment"], "staging")

    def test_anonymous_api_surface_is_limited(self) -> None:
        allowed = {"/api/public-config", "/api/price-history"}
        routes = [
            "/api/public-config",
            "/api/portfolio",
            "/api/export/csv",
            "/api/signals",
            "/api/youtube-monitor/mentions",
            "/api/price-history",
        ]

        for path in routes:
            with self.subTest(path=path):
                response = self.client.get(path)
                if path in allowed:
                    self.assertNotEqual(response.status_code, 401)
                else:
                    self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
