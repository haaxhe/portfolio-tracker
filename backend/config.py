import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


def _load_claude_env() -> dict[str, str]:
    """Read Claude Code env settings as a local fallback without logging secrets."""
    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        data = json.loads(settings_path.read_text())
    except Exception:
        return {}
    env = data.get("env", {})
    return env if isinstance(env, dict) else {}


_claude_env = _load_claude_env()


def _getenv(name: str, default: str = "") -> str:
    return os.getenv(name) or str(_claude_env.get(name) or default)


def _getbool(name: str, default: str = "false") -> bool:
    return _getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # Robinhood
    RH_USERNAME: str = _getenv("RH_USERNAME")
    RH_PASSWORD: str = _getenv("RH_PASSWORD")
    RH_TOTP_SECRET: str = _getenv("RH_TOTP_SECRET")

    # E*Trade
    ETRADE_CONSUMER_KEY: str = _getenv("ETRADE_CONSUMER_KEY")
    ETRADE_CONSUMER_SECRET: str = _getenv("ETRADE_CONSUMER_SECRET")
    ETRADE_SANDBOX: bool = _getenv("ETRADE_SANDBOX", "true").lower() == "true"

    # Alpaca
    ALPACA_API_KEY: str = _getenv("ALPACA_API_KEY")
    ALPACA_SECRET_KEY: str = _getenv("ALPACA_SECRET_KEY")
    ALPACA_ENDPOINT: str = _getenv("ALPACA_ENDPOINT")
    ALPACA_DATA_ENDPOINT: str = _getenv("ALPACA_DATA_ENDPOINT", "https://data.alpaca.markets")
    ALPACA_STOCK_FEED: str = _getenv("ALPACA_STOCK_FEED", "iex")

    # App
    REFRESH_INTERVAL_MINUTES: int = int(_getenv("REFRESH_INTERVAL_MINUTES", "5"))
    DB_PATH: str = _getenv("DB_PATH", "./portfolio.db")
    DATABASE_URL: str = _getenv("DATABASE_URL")
    HOST: str = _getenv("HOST", "127.0.0.1")
    PORT: int = int(_getenv("PORT", "8000"))
    ENVIRONMENT: str = _getenv("ENVIRONMENT", "local")
    APP_BASE_URL: str = _getenv("APP_BASE_URL", "http://127.0.0.1:8000")
    DEFAULT_USER_ID: str = _getenv("DEFAULT_USER_ID", "local-user")
    AUTH_MODE: str = _getenv("AUTH_MODE", "local")  # local, token, or supabase
    API_TOKEN: str = _getenv("API_TOKEN")
    TRUST_PROXY_USER_HEADER: bool = _getbool("TRUST_PROXY_USER_HEADER")
    SUPABASE_URL: str = _getenv("SUPABASE_URL")
    SUPABASE_PUBLISHABLE_KEY: str = (
        _getenv("SUPABASE_PUBLISHABLE_KEY")
        or _getenv("SUPABASE_ANON_KEY")
    )
    CORS_ORIGINS: list[str] = [
        origin.strip()
        for origin in _getenv("CORS_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
        if origin.strip()
    ]
    ENABLE_BROKER_CONNECTORS: bool = _getbool("ENABLE_BROKER_CONNECTORS")
    YOUTUBE_MONITOR_ENABLED: bool = _getbool("YOUTUBE_MONITOR_ENABLED")
    YOUTUBE_MONITOR_CONFIG_PATH: str = _getenv("YOUTUBE_MONITOR_CONFIG_PATH", "config/youtube_sources.json")
    YOUTUBE_MONITOR_INTERVAL_HOURS: int = int(_getenv("YOUTUBE_MONITOR_INTERVAL_HOURS", "24"))
    YOUTUBE_MONITOR_LLM_ENABLED: bool = _getbool("YOUTUBE_MONITOR_LLM_ENABLED")
    YOUTUBE_MONITOR_SUMMARIZE_LIMIT: int = int(_getenv("YOUTUBE_MONITOR_SUMMARIZE_LIMIT", "3"))
    OPENAI_API_KEY: str = _getenv("OPENAI_API_KEY")
    OPENAI_MODEL: str = _getenv("OPENAI_MODEL", "gpt-5.2")

    @property
    def robinhood_configured(self) -> bool:
        return bool(self.RH_USERNAME and self.RH_PASSWORD)

    @property
    def etrade_configured(self) -> bool:
        return bool(self.ETRADE_CONSUMER_KEY and self.ETRADE_CONSUMER_SECRET)

    @property
    def alpaca_configured(self) -> bool:
        return bool(self.ALPACA_API_KEY and self.ALPACA_SECRET_KEY and self.ALPACA_DATA_ENDPOINT)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"prod", "production"}

    def validate_for_startup(self) -> None:
        auth_mode = self.AUTH_MODE.strip().lower()
        errors: list[str] = []

        if auth_mode not in {"local", "token", "supabase"}:
            errors.append("AUTH_MODE must be local, token, or supabase")

        if "*" in self.CORS_ORIGINS:
            errors.append("CORS_ORIGINS must not include '*' when credentials are enabled")

        if self.is_production:
            if auth_mode != "supabase":
                errors.append("ENVIRONMENT=production requires AUTH_MODE=supabase")
            if not self.DATABASE_URL:
                errors.append("ENVIRONMENT=production requires DATABASE_URL")
            if not self.APP_BASE_URL.startswith("https://"):
                errors.append("ENVIRONMENT=production requires an HTTPS APP_BASE_URL")
            if not self.SUPABASE_URL or not self.SUPABASE_PUBLISHABLE_KEY:
                errors.append("ENVIRONMENT=production requires Supabase auth settings")
            if any(not origin.startswith("https://") for origin in self.CORS_ORIGINS):
                errors.append("ENVIRONMENT=production requires HTTPS CORS_ORIGINS")

        if errors:
            raise RuntimeError("Invalid security configuration: " + "; ".join(errors))


settings = Settings()
