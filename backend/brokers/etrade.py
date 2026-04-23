"""
E*Trade integration via official REST API (OAuth 1.0a).

Requires an API key from https://developer.etrade.com
"""
import logging
import webbrowser
from backend.brokers import BaseBroker
from backend.models import Position, BrokerName
from backend.config import settings

logger = logging.getLogger(__name__)

# E*Trade API base URLs
ETRADE_BASE_SANDBOX = "https://apisb.etrade.com"
ETRADE_BASE_PROD = "https://api.etrade.com"


class ETradeBroker(BaseBroker):
    def __init__(self):
        self._connected = False
        self._session = None
        self._base_url = ETRADE_BASE_SANDBOX if settings.ETRADE_SANDBOX else ETRADE_BASE_PROD
        self._accounts: list[dict] = []

    @property
    def broker_name(self) -> str:
        return "etrade"

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """
        OAuth 1.0a flow for E*Trade.
        On first run, opens browser for user to authorize.
        """
        if not settings.etrade_configured:
            logger.warning("E*Trade credentials not configured — skipping")
            return False
        try:
            from rauth import OAuth1Service

            etrade_service = OAuth1Service(
                name="etrade",
                consumer_key=settings.ETRADE_CONSUMER_KEY,
                consumer_secret=settings.ETRADE_CONSUMER_SECRET,
                request_token_url="https://api.etrade.com/oauth/request_token",
                access_token_url="https://api.etrade.com/oauth/access_token",
                authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
                base_url=self._base_url,
            )

            # Step 1: Get request token
            request_token, request_token_secret = etrade_service.get_request_token(
                params={"oauth_callback": "oob", "format": "json"}
            )

            # Step 2: Open browser for user authorization
            authorize_url = etrade_service.authorize_url.format(
                settings.ETRADE_CONSUMER_KEY, request_token
            )
            print(f"\n{'='*60}")
            print("E*Trade Authorization Required")
            print(f"{'='*60}")
            print(f"Opening browser to: {authorize_url}")
            print("After authorizing, enter the verification code below.")
            print(f"{'='*60}\n")

            webbrowser.open(authorize_url)
            verifier = input("Enter E*Trade verification code: ").strip()

            # Step 3: Get access token
            self._session = etrade_service.get_auth_session(
                request_token,
                request_token_secret,
                params={"oauth_verifier": verifier},
            )

            # Step 4: Fetch account list
            resp = self._session.get(
                f"{self._base_url}/v1/accounts/list.json"
            )
            data = resp.json()
            self._accounts = (
                data.get("AccountListResponse", {})
                .get("Accounts", {})
                .get("Account", [])
            )

            self._connected = True
            logger.info(f"E*Trade: connected, found {len(self._accounts)} accounts")
            return True

        except Exception as e:
            logger.error(f"E*Trade login failed: {e}")
            self._connected = False
            return False

    async def get_positions(self) -> list[Position]:
        if not self._connected or not self._session:
            return []

        all_positions = []
        try:
            for account in self._accounts:
                account_id_key = account.get("accountIdKey", "")
                resp = self._session.get(
                    f"{self._base_url}/v1/accounts/{account_id_key}/portfolio.json"
                )

                if resp.status_code != 200:
                    logger.warning(f"E*Trade portfolio fetch returned {resp.status_code}")
                    continue

                data = resp.json()
                portfolio_resp = data.get("PortfolioResponse", {})
                account_portfolios = portfolio_resp.get("AccountPortfolio", [])

                for ap in account_portfolios:
                    for item in ap.get("Position", []):
                        product = item.get("Product", {})
                        quick = item.get("Quick", {})

                        pos = Position(
                            symbol=product.get("symbol", "???"),
                            name=product.get("securityType", ""),
                            quantity=float(item.get("quantity", 0)),
                            average_cost=float(item.get("costPerShare", 0)),
                            current_price=float(quick.get("lastTrade", 0)),
                            broker=BrokerName.ETRADE,
                            account_id=account_id_key,
                            asset_type=product.get("securityType", "stock").lower(),
                        )
                        pos.compute_derived()
                        all_positions.append(pos)

            logger.info(f"E*Trade: fetched {len(all_positions)} positions")

        except Exception as e:
            logger.error(f"E*Trade fetch failed: {e}")

        return all_positions
