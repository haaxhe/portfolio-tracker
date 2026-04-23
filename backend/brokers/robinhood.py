"""
Robinhood integration via robin_stocks (unofficial API).

NOTE: This uses an unofficial, reverse-engineered API.
Robinhood may change it at any time. Use at your own risk.
"""
import logging
from backend.brokers import BaseBroker
from backend.models import Position, BrokerName
from backend.config import settings

logger = logging.getLogger(__name__)


class RobinhoodBroker(BaseBroker):
    def __init__(self):
        self._connected = False

    @property
    def broker_name(self) -> str:
        return "robinhood"

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        if not settings.robinhood_configured:
            logger.warning("Robinhood credentials not configured — skipping")
            return False
        try:
            import robin_stocks.robinhood as rh
            import pyotp

            totp = pyotp.TOTP(settings.RH_TOTP_SECRET).now() if settings.RH_TOTP_SECRET else None
            rh.login(
                settings.RH_USERNAME,
                settings.RH_PASSWORD,
                mfa_code=totp,
                store_session=True,
            )
            self._connected = True
            logger.info("Robinhood: connected successfully")
            return True
        except Exception as e:
            logger.error(f"Robinhood login failed: {e}")
            self._connected = False
            return False

    async def get_positions(self) -> list[Position]:
        if not self._connected:
            return []
        try:
            import robin_stocks.robinhood as rh

            holdings = rh.build_holdings()
            positions = []
            for symbol, data in holdings.items():
                pos = Position(
                    symbol=symbol,
                    name=data.get("name", symbol),
                    quantity=float(data.get("quantity", 0)),
                    average_cost=float(data.get("average_buy_price", 0)),
                    current_price=float(data.get("price", 0)),
                    broker=BrokerName.ROBINHOOD,
                    asset_type="stock",
                )
                pos.compute_derived()
                positions.append(pos)

            logger.info(f"Robinhood: fetched {len(positions)} positions")
            return positions
        except Exception as e:
            logger.error(f"Robinhood fetch failed: {e}")
            return []
