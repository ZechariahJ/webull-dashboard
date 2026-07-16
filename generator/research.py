"""Research helpers for the dashboard.

Pure parsers (``parse_movers``, ``upcoming_from_earnings``) turn raw Webull JSON
into simple dicts and are unit-tested without any network. ``gather`` orchestrates
the live calls and degrades gracefully: if one section 403s or errors, the rest of
the report still renders.

This is informational only — it summarizes public market data. It is NOT a
recommendation to buy or sell anything.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import List

log = logging.getLogger("research")

_UNIVERSE_FILE = os.path.join(os.path.dirname(__file__), "data", "major_tickers.txt")


@lru_cache(maxsize=1)
def load_universe(path: str = _UNIVERSE_FILE) -> frozenset:
    """Load the S&P 500 + NASDAQ-100 ticker set. Returns an empty set (no filtering)
    if the file is missing, so the report still works."""
    try:
        with open(path, encoding="utf-8") as f:
            syms = {ln.strip().upper() for ln in f
                    if ln.strip() and not ln.startswith("#")}
        return frozenset(syms)
    except FileNotFoundError:
        log.warning("research: universe file not found (%s); movers unfiltered.", path)
        return frozenset()


def filter_universe(rows: List[dict], universe) -> List[dict]:
    """Keep only rows whose symbol is in `universe`. Empty universe = no filtering."""
    if not universe:
        return rows
    return [r for r in rows if str(r.get("symbol", "")).upper() in universe]


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "rows", "list", "records"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
        data = payload.get("data")
        if isinstance(data, dict):
            return _rows(data)
    return []


def _get(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_movers(payload) -> List[dict]:
    """Normalize a screener payload into [{symbol, name, price, change_ratio, volume}]."""
    out = []
    for r in _rows(payload):
        if not isinstance(r, dict):
            continue
        out.append({
            "symbol": _get(r, "symbol", "ticker", "tickerSymbol", default="?"),
            "name": _get(r, "name", "shortName", default=""),
            "price": _num(_get(r, "price", "close", "last")),
            # change_ratio may arrive as a fraction (0.05) or already a percent (5.0).
            "change_ratio": _num(_get(r, "change_ratio", "changeRatio", "changeRate")),
            "volume": _num(_get(r, "volume", "vol")),
        })
    return out


def _parse_date(v):
    """Best-effort parse of a date/epoch into a date. Returns None on failure."""
    if v is None:
        return None
    # Epoch (seconds or millis)?
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        n = float(v)
        if n > 1e12:  # milliseconds
            n /= 1000.0
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc).date()
        except (OverflowError, OSError, ValueError):
            return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(v)[:len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


def upcoming_from_earnings(symbol: str, payload, today: date | None = None) -> List[dict]:
    """From one symbol's earnings-calendar payload, return upcoming reports only.

    A row is "upcoming" when it has no `eps_actual` (per the API docs) and its
    date is today or later.
    """
    today = today or datetime.now(timezone.utc).date()
    out = []
    for r in _rows(payload):
        if not isinstance(r, dict):
            continue
        if _get(r, "eps_actual", "epsActual") is not None:
            continue  # already reported
        d = _parse_date(_get(r, "earnings_date", "earningsDate", "date", "report_date"))
        if d is None or d < today:
            continue
        out.append({
            "symbol": symbol,
            "date": d.isoformat(),
            "eps_estimate": _get(r, "eps_estimate", "epsEstimate", "eps_forecast"),
            "time": _get(r, "earnings_time", "time", "session", default=""),
        })
    return out


def gather(wb, cfg) -> dict:
    """Run the live calls and assemble the report data. Never raises for a single
    failed section — logs and continues.

    Movers are filtered to the S&P 500 + NASDAQ-100 universe (data/major_tickers.txt)
    so the report shows real index names, not micro-cap/penny movers. We fetch a wide
    page (the raw top movers are almost all penny stocks) and keep the top index names.
    """
    universe = load_universe()
    fetch_n = getattr(cfg, "MOVERS_FETCH", 250)
    top_n = getattr(cfg, "MOVERS_TOP", 10)
    data = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "gainers": [], "losers": [], "most_active": [],
            "universe_size": len(universe), "errors": []}

    def _try(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - report-level resilience
            log.warning("research: %s failed: %s", label, e)
            data["errors"].append(f"{label}: {e}")
            return None

    def _movers(label, direction, reverse):
        raw = _try(label, lambda: wb.get_gainers_losers(direction=direction, count=fetch_n))
        if raw is None:
            return []
        rows = filter_universe(parse_movers(raw), universe)
        rows.sort(key=lambda r: r["change_ratio"], reverse=reverse)
        return rows[:top_n]

    data["gainers"] = _movers("gainers", "DESC", reverse=True)
    data["losers"] = _movers("losers", "ASC", reverse=False)

    a = _try("most_active", lambda: wb.get_most_active(count=fetch_n))
    if a is not None:
        active = filter_universe(parse_movers(a), universe)
        data["most_active"] = active[:top_n]
    return data
