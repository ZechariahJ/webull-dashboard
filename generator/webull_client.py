"""Thin wrapper around the Webull OpenAPI SDK.

Isolates every SDK call behind simple methods so the strategy code stays
readable and the SDK surface is easy to swap/mock in tests.

Docs:
  - Trading getting started: https://developer.webull.com/apis/docs/trade-api/getting-started
  - Market data getting started: https://developer.webull.com/apis/docs/market-data-api/getting-started
  - API reference: https://developer.webull.com/apis/docs/webull-open-api-reference

Verified against webull-openapi-python-sdk 2.0.13 (July 2026):
  - trade.account_v2.get_account_list()          -> requests.Response
  - trade.account_v2.get_account_position(acct)  -> requests.Response
  - trade.order_v3.place_order(acct, [order])    -> requests.Response
  - trade.order_v3.cancel_order(acct, coid)      -> requests.Response
  - data.market_data.get_history_bar(sym, cat, span, count) -> requests.Response
If the SDK version you install differs, this wrapper is the only file to touch.
"""
import uuid
import logging

from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient
from webull.data.data_client import DataClient
from webull.data.common.category import Category
from webull.data.common.timespan import Timespan
from webull.core.exception.exceptions import ServerException

from config import Config

log = logging.getLogger("webull")


class MarketDataSubscriptionError(RuntimeError):
    """Raised when a market-data call is rejected for lack of an OpenAPI quote
    subscription. Webull signals this inconsistently — sometimes an HTTP 403
    response, sometimes a raised 401 ServerException ("please subscribe to stock
    quotes") — so we normalize both to this one clear error."""


def _md_call(fn, label: str):
    """Run a market-data SDK call, translating the 'no quote subscription' rejection
    (which the SDK raises as a 401 ServerException) into MarketDataSubscriptionError."""
    try:
        return fn()
    except ServerException as e:
        msg = (e.get_error_msg() or "")
        low = msg.lower()
        if e.get_http_status() in (401, 403) and (
                "subscribe" in low or "quote" in low or "insufficient permission" in low):
            raise MarketDataSubscriptionError(
                f"{label}: {msg} Enable the market-data quote subscription in the "
                "Webull Developer Portal ('Subscribe Advanced Quotes').") from None
        raise


def _rows(payload):
    """Normalize a JSON payload into a list of row dicts.

    Webull endpoints variously return a bare list, or a dict wrapping the list
    under `data`, `items`, `positions`, or `bars`. Handle them all so a minor
    server-side shape change doesn't break the bot.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "positions", "bars", "rows"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        # Some endpoints nest one level deeper: {"data": {"positions": [...]}}
        data = payload.get("data")
        if isinstance(data, dict):
            return _rows(data)
    return []


def _first(d: dict, *keys, default=None):
    """Return the first present, non-None value among `keys` in dict `d`."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


class WebullClient:
    def __init__(self, cfg: Config = Config):
        self.cfg = cfg
        self.api_client = ApiClient(cfg.APP_KEY, cfg.APP_SECRET, cfg.REGION)
        self.api_client.add_endpoint(cfg.REGION, cfg.http_host())
        self.trade = TradeClient(self.api_client)
        self.data = DataClient(self.api_client)
        self.account_id = cfg.ACCOUNT_ID

    # --- Account ---
    def ensure_account_id(self) -> str:
        """Fetch and cache the first account_id if not already set."""
        if self.account_id:
            return self.account_id
        res = self.trade.account_v2.get_account_list()
        if res.status_code != 200:
            raise RuntimeError(f"get_account_list failed: {res.status_code} {res.text}")
        rows = _rows(res.json())
        if not rows:
            raise RuntimeError(f"get_account_list returned no accounts: {res.json()}")
        acct = _first(rows[0], "account_id", "accountId", "id")
        if not acct:
            raise RuntimeError(f"Could not find account_id in: {rows[0]}")
        self.account_id = acct
        log.info("Using account_id=%s", self.account_id)
        return self.account_id

    def get_positions(self):
        """Return the raw positions payload for the account."""
        acct = self.ensure_account_id()
        res = self.trade.account_v2.get_account_position(acct)
        if res.status_code != 200:
            raise RuntimeError(f"get_account_position failed: {res.status_code} {res.text}")
        return res.json()

    def position_qty(self, symbol: str) -> float:
        """Net shares held for `symbol` (0 if none)."""
        for p in _rows(self.get_positions()):
            sym = _first(p, "symbol", "ticker", "tickerSymbol")
            if sym == symbol:
                return float(_first(p, "quantity", "position", "qty", "holdings", default=0) or 0)
        return 0.0

    # --- Market data ---
    def get_history_bars(self, symbol: str, timespan=Timespan.M1, count: int = 200):
        """Recent candlestick bars for `symbol`. Requires a market-data
        subscription in production (403 otherwise)."""
        res = _md_call(lambda: self.data.market_data.get_history_bar(
            symbol, Category.US_STOCK.name, timespan.name, str(count)), "history_bar")
        if res.status_code == 403:
            raise MarketDataSubscriptionError(
                "Market data returned HTTP 403. US stock/ETF quotes (historical and "
                "real-time) require an active OpenAPI market-data subscription. "
                "Subscribe via developer.webull.com -> 'Subscribe Advanced Quotes'."
            )
        if res.status_code != 200:
            raise RuntimeError(f"get_history_bar failed: {res.status_code} {res.text}")
        return res.json()

    # --- Research / screeners (for the dashboard) ---
    def get_gainers_losers(self, direction: str = "DESC", rank_type: str = "DAY_1",
                           sort_by: str = "CHANGE_RATIO", count: int = 10):
        """Top movers by price change. direction='DESC' for gainers, 'ASC' for losers.

        rank_type: PRE_MARKET, AFTER_MARKET, MIN_3/5, DAY_1, DAY_5, MONTH_1/3, WEEK_52.
        Returns the raw JSON payload.
        """
        res = _md_call(lambda: self.data.screener.get_gainers_losers(
            rank_type, Category.US_STOCK.name, sort_by,
            page_index=1, page_size=count, direction=direction), "gainers_losers")
        if res.status_code == 403:
            raise MarketDataSubscriptionError(
                "Screener returned HTTP 403 — needs an active market-data subscription.")
        if res.status_code != 200:
            raise RuntimeError(f"get_gainers_losers failed: {res.status_code} {res.text}")
        return res.json()

    def get_most_active(self, count: int = 10):
        """Most actively traded US stocks (by volume). Returns raw JSON."""
        res = _md_call(lambda: self.data.screener.get_most_active(
            Category.US_STOCK.name, page_index=1, page_size=count), "most_active")
        if res.status_code == 403:
            raise MarketDataSubscriptionError(
                "Screener returned HTTP 403 — needs an active market-data subscription.")
        if res.status_code != 200:
            raise RuntimeError(f"get_most_active failed: {res.status_code} {res.text}")
        return res.json()

    def get_earnings_calendar(self, symbol: str):
        """Earnings calendar for one symbol (±6 months). Rows without `eps_actual`
        are upcoming reports. Returns raw JSON."""
        res = _md_call(lambda: self.data.fundamentals.get_earnings_calendar(
            symbol, Category.US_STOCK.name), f"earnings:{symbol}")
        if res.status_code == 403:
            raise MarketDataSubscriptionError(
                "Fundamentals returned HTTP 403 — needs an active market-data subscription.")
        if res.status_code != 200:
            raise RuntimeError(f"get_earnings_calendar({symbol}) failed: {res.status_code} {res.text}")
        return res.json()

    # --- Orders ---
    def preview_order(self, orders):
        """Validate an order server-side WITHOUT submitting it. Returns the JSON
        preview (est. cost, buying-power impact, warnings). Use before going live."""
        acct = self.ensure_account_id()
        res = self.trade.order_v3.preview_order(acct, orders)
        if res.status_code != 200:
            raise RuntimeError(f"preview_order failed: {res.status_code} {res.text}")
        return res.json()

    def place_amount_order(self, symbol: str, amount: float, side: str = "BUY"):
        """Place a dollar-based FRACTIONAL market order (buy `amount` dollars of
        `symbol`). This is how the $5/day DCA works.

        NOTE: fractional orders are market orders sized by notional `amount`
        (entrust_type=AMOUNT). The exact `amount` field name should be confirmed
        against a live/sandbox response the first time you run non-dry.
        Returns (client_order_id, response_json).
        """
        acct = self.ensure_account_id()
        client_order_id = uuid.uuid4().hex
        orders = [{
            "combo_type": "NORMAL",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "instrument_type": "EQUITY",
            "market": "US",
            "order_type": "MARKET",
            "amount": f"{amount:.2f}",
            "support_trading_session": "CORE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "AMOUNT",
        }]
        if self.cfg.DRY_RUN:
            log.info("[DRY_RUN] would place %s $%.2f of %s (coid=%s)",
                     side, amount, symbol, client_order_id)
            return client_order_id, {"dry_run": True, "orders": orders}
        res = self.trade.order_v3.place_order(acct, orders)
        if res.status_code != 200:
            raise RuntimeError(f"place_amount_order failed: {res.status_code} {res.text}")
        log.info("Placed %s $%.2f of %s -> %s", side, amount, symbol, res.json())
        return client_order_id, res.json()

    def place_limit_order(self, symbol: str, side: str, qty: int, limit_price: str):
        """Place a DAY limit order. `side` is 'BUY' or 'SELL'.

        Returns (client_order_id, response_json).
        """
        acct = self.ensure_account_id()
        client_order_id = uuid.uuid4().hex
        orders = [{
            "combo_type": "NORMAL",
            "client_order_id": client_order_id,
            "symbol": symbol,
            "instrument_type": "EQUITY",
            "market": "US",
            "order_type": "LIMIT",
            "limit_price": str(limit_price),
            "quantity": str(qty),
            "support_trading_session": "CORE",
            "side": side,
            "time_in_force": "DAY",
            "entrust_type": "QTY",
        }]
        if self.cfg.DRY_RUN:
            log.info("[DRY_RUN] would place %s %s x%s @ %s (coid=%s)",
                     side, symbol, qty, limit_price, client_order_id)
            return client_order_id, {"dry_run": True}
        res = self.trade.order_v3.place_order(acct, orders)
        if res.status_code != 200:
            raise RuntimeError(f"place_order failed: {res.status_code} {res.text}")
        log.info("Placed %s %s x%s @ %s -> %s", side, symbol, qty, limit_price, res.json())
        return client_order_id, res.json()

    def cancel_order(self, client_order_id: str):
        acct = self.ensure_account_id()
        if self.cfg.DRY_RUN:
            log.info("[DRY_RUN] would cancel %s", client_order_id)
            return {"dry_run": True}
        res = self.trade.order_v3.cancel_order(acct, client_order_id)
        if res.status_code != 200:
            raise RuntimeError(f"cancel_order failed: {res.status_code} {res.text}")
        return res.json()
