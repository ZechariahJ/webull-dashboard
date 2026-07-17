"""Optional news feed for the dashboard.

Webull's API has no news endpoint, so headlines come from a free third-party
provider. Finnhub's free tier (https://finnhub.io) is the default: set
NEWS_API_KEY in .env. With no key, news is simply skipped and the rest of the
dashboard still renders.

Only the pure parser (``parse_finnhub``) is unit-tested; the HTTP fetch is a thin
wrapper so no network is needed to test.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import List

import requests

log = logging.getLogger("news")

_FINNHUB_MARKET = "https://finnhub.io/api/v1/news"
_FINNHUB_COMPANY = "https://finnhub.io/api/v1/company-news"

# Only surface reputable, widely-cited financial outlets in the digest.
_TRUSTED_SOURCES = {
    "reuters", "bloomberg", "cnbc", "the wall street journal", "wsj",
    "associated press", "ap", "marketwatch", "financial times", "ft",
    "barron's", "yahoo", "forbes", "business insider", "seekingalpha",
}


def parse_finnhub(items, limit: int = 15, trusted_only: bool = False) -> List[dict]:
    """Normalize Finnhub news JSON into [{headline, source, url, datetime, summary}]."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        source = str(it.get("source", "")).strip()
        if trusted_only and source.lower() not in _TRUSTED_SOURCES:
            continue
        ts = it.get("datetime")
        when = ""
        if isinstance(ts, (int, float)):
            when = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="minutes")
        out.append({
            "headline": str(it.get("headline", "")).strip(),
            "source": source,
            "url": it.get("url", ""),
            "datetime": when,
            "summary": str(it.get("summary", "")).strip(),
        })
    return out[:limit]


def fetch_market_news(api_key: str, limit: int = 15, timeout: int = 10) -> List[dict]:
    """Fetch general market news from Finnhub. Returns [] on any failure or no key."""
    if not api_key:
        return []
    try:
        resp = requests.get(_FINNHUB_MARKET,
                            params={"category": "general", "token": api_key},
                            timeout=timeout)
        if resp.status_code != 200:
            log.warning("news: finnhub returned %s", resp.status_code)
            return []
        return parse_finnhub(resp.json(), limit=limit)
    except Exception as e:  # noqa: BLE001 - news is best-effort
        log.warning("news: fetch failed: %s", e)
        return []


def fetch_company_news(api_key: str, symbol: str, days: int = 7, limit: int = 5,
                       timeout: int = 10) -> List[dict]:
    """Fetch recent headlines for one ticker. Returns [] on any failure or no key.

    Used at build time to embed each earnings stock's news into the static page
    (the browser can't call Finnhub directly — that would expose the API key).
    """
    if not api_key or not symbol:
        return []
    today = date.today()
    try:
        resp = requests.get(_FINNHUB_COMPANY, timeout=timeout, params={
            "symbol": symbol,
            "from": (today - timedelta(days=days)).isoformat(),
            "to": today.isoformat(),
            "token": api_key,
        })
        if resp.status_code != 200:
            log.warning("news: company-news %s returned %s", symbol, resp.status_code)
            return []
        # Finnhub returns newest-first already; parse_finnhub preserves order.
        return parse_finnhub(resp.json(), limit=limit)
    except Exception as e:  # noqa: BLE001 - news is best-effort
        log.warning("news: company-news %s failed: %s", symbol, e)
        return []
