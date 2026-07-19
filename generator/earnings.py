"""Earnings calendar + per-stock news, via Finnhub's free tier.

Webull's own earnings endpoint needs a paid market-data quote subscription, so we
source the calendar from Finnhub (same key as the news feed). Each listed stock's
headlines are fetched at build time and embedded into the static page — the browser
can't call Finnhub itself without exposing the API key.

`parse_calendar` is pure and unit-tested; the HTTP calls are thin wrappers.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import List

import requests

import news
from research import filter_universe, company_name

log = logging.getLogger("earnings")

_FINNHUB_CALENDAR = "https://finnhub.io/api/v1/calendar/earnings"

# Finnhub's `hour` codes -> readable session labels.
_SESSION = {"bmo": "Before open", "amc": "After close", "dmh": "During market"}


def parse_calendar(payload) -> List[dict]:
    """Normalize Finnhub's earnings-calendar JSON into simple rows."""
    rows = (payload or {}).get("earningsCalendar") if isinstance(payload, dict) else payload
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol", "")).strip().upper()
        day = str(r.get("date", "")).strip()
        if not sym or not day:
            continue
        out.append({
            "symbol": sym,
            "date": day,
            "session": _SESSION.get(str(r.get("hour", "")).lower(), ""),
            "eps_estimate": r.get("epsEstimate"),
        })
    return out


def fetch_calendar(api_key: str, days: int = 7, timeout: int = 20):
    """Raw earnings calendar for the next `days` days. {} on failure/no key."""
    if not api_key:
        return {}
    today = date.today()
    try:
        resp = requests.get(_FINNHUB_CALENDAR, timeout=timeout, params={
            "from": today.isoformat(),
            "to": (today + timedelta(days=days)).isoformat(),
            "token": api_key,
        })
        if resp.status_code != 200:
            log.warning("earnings: calendar returned %s", resp.status_code)
            return {}
        return resp.json()
    except Exception as e:  # noqa: BLE001 - best-effort section
        log.warning("earnings: calendar fetch failed: %s", e)
        return {}


def gather(api_key: str, universe, days: int = 7, max_rows: int = 60,
           news_per: int = 5, pace: float = 1.1) -> List[dict]:
    """Upcoming earnings for index names, each with its own headlines attached.

    Filtered to `universe` (S&P 500 + NASDAQ-100) and capped at `max_rows`, because
    every listed row costs one extra Finnhub call for its news. `pace` throttles
    those calls to stay under Finnhub's free-tier limit (60/min).
    """
    rows = filter_universe(parse_calendar(fetch_calendar(api_key, days)), universe)
    # One row per symbol (soonest first), then cap.
    seen, uniq = set(), []
    for r in sorted(rows, key=lambda x: (x["date"], x["symbol"])):
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        uniq.append(r)
    uniq = uniq[:max_rows]

    log.info("earnings: %d index names in the next %dd; fetching news for %d",
             len(seen), days, len(uniq))
    for i, r in enumerate(uniq):
        r["name"] = company_name(r["symbol"])
        r["news"] = news.fetch_company_news(api_key, r["symbol"], limit=news_per)
        if i < len(uniq) - 1:
            time.sleep(pace)  # stay under the free-tier rate limit
    return uniq
