import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Robinhood
    RH_USERNAME: str = os.getenv("RH_USERNAME", "")
    RH_PASSWORD: str = os.getenv("RH_PASSWORD", "")
    RH_TOTP_SECRET: str = os.getenv("RH_TOTP_SECRET", "")

    # E*Trade
    ETRADE_CONSUMER_KEY: str = os.getenv("ETRADE_CONSUMER_KEY", "")
    ETRADE_CONSUMER_SECRET: str = os.getenv("ETRADE_CONSUMER_SECRET", "")
    ETRADE_SANDBOX: bool = os.getenv("ETRADE_SANDBOX", "true").lower() == "true"

    # App
    REFRESH_INTERVAL_MINUTES: int = int(os.getenv("REFRESH_INTERVAL_MINUTES", "5"))
    DB_PATH: str = os.getenv("DB_PATH", "./portfolio.db")
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    @property
    def robinhood_configured(self) -> bool:
        return bool(self.RH_USERNAME and self.RH_PASSWORD)

    @property
    def etrade_configured(self) -> bool:
        return bool(self.ETRADE_CONSUMER_KEY and self.ETRADE_CONSUMER_SECRET)


settings = Settings()
