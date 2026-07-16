"""Central configuration, loaded from environment / .env file.

All secrets and tunables live here so the rest of the code never hard-codes
credentials or endpoints. See .env.example for the full list of variables.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Endpoints ---
# Webull routes by environment. Sandbox = paper money, production = real money.
# HTTP host is used for account/order/market-data REST calls.
# Events host is the gRPC stream for order-status updates.
_ENDPOINTS = {
    "sandbox": {
        "http": "api.sandbox.webull.com",
        "events": "events-api.sandbox.webull.com",
    },
    "production": {
        "http": "api.webull.com",
        "events": "events-api.webull.com",
        "mqtt": "data-api.webull.com",  # real-time market data stream (prod only)
    },
}


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


class Config:
    APP_KEY = os.getenv("WEBULL_APP_KEY", "")
    APP_SECRET = os.getenv("WEBULL_APP_SECRET", "")
    ACCOUNT_ID = os.getenv("WEBULL_ACCOUNT_ID", "") or None

    ENV = os.getenv("WEBULL_ENV", "sandbox").lower()
    REGION = os.getenv("WEBULL_REGION", "us").lower()

    TICKER = os.getenv("TICKER", "AAPL")
    ORDER_QTY = _env_int("ORDER_QTY", 1)
    MAX_POSITION = _env_int("MAX_POSITION", 5)
    POLL_INTERVAL = _env_int("POLL_INTERVAL", 60)
    DRY_RUN = _env_bool("DRY_RUN", True)

    # --- Risk controls ---
    # Hard cap on the number of orders the bot may submit in a single day.
    MAX_ORDERS_PER_DAY = _env_int("MAX_ORDERS_PER_DAY", 10)
    # Reject any order whose notional (limit_price * qty) exceeds this. 0 = off.
    MAX_NOTIONAL_PER_ORDER = _env_float("MAX_NOTIONAL_PER_ORDER", 2000.0)
    # Only trade during US regular hours (09:30-16:00 America/New_York, Mon-Fri).
    RTH_ONLY = _env_bool("RTH_ONLY", True)
    # Seconds to pause after an unhandled cycle error before trying again.
    ERROR_COOLDOWN = _env_int("ERROR_COOLDOWN", 30)
    # If a file with this name exists in the working dir, the bot halts placing
    # orders immediately. A dead-simple manual kill switch: `touch STOP`.
    KILL_SWITCH_FILE = os.getenv("KILL_SWITCH_FILE", "STOP")

    # --- $/day dollar-cost-averaging (DCA) ---
    # Fixed ticker the daily auto-invest buys (you choose it — not advice).
    DCA_TICKER = os.getenv("DCA_TICKER", "VOO")
    # Dollars to invest each run (fractional/notional market order).
    DCA_AMOUNT = _env_float("DCA_AMOUNT", 5.0)
    # Records the last date DCA ran, so it fires at most once per calendar day.
    DCA_STATE_FILE = os.getenv("DCA_STATE_FILE", ".dca_state.json")

    # --- Research dashboard ---
    # How many raw movers to fetch before filtering to the S&P/NASDAQ universe
    # (the raw top movers are mostly penny stocks, so we cast a wide net) and how
    # many index names to show in each list.
    MOVERS_FETCH = _env_int("MOVERS_FETCH", 250)
    MOVERS_TOP = _env_int("MOVERS_TOP", 10)
    # Where the generated HTML dashboard is written.
    REPORT_OUTPUT = os.getenv("REPORT_OUTPUT", "dashboard.html")
    # Optional free news API (Finnhub free tier). Leave blank to skip news.
    NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
    NEWS_PROVIDER = os.getenv("NEWS_PROVIDER", "finnhub").lower()

    @classmethod
    def http_host(cls) -> str:
        return _ENDPOINTS[cls.ENV]["http"]

    @classmethod
    def events_host(cls) -> str:
        return _ENDPOINTS[cls.ENV]["events"]

    @classmethod
    def validate(cls) -> None:
        if not cls.APP_KEY or not cls.APP_SECRET:
            raise SystemExit(
                "Missing WEBULL_APP_KEY / WEBULL_APP_SECRET. "
                "Copy .env.example to .env and fill them in."
            )
        if cls.ENV not in _ENDPOINTS:
            raise SystemExit(f"WEBULL_ENV must be 'sandbox' or 'production', got '{cls.ENV}'.")
        if cls.ORDER_QTY <= 0:
            raise SystemExit("ORDER_QTY must be a positive integer.")
        if cls.ENV == "production" and cls.DRY_RUN is False:
            print("\n*** WARNING: PRODUCTION environment with DRY_RUN=false. "
                  "This will place orders with REAL money. ***\n")
