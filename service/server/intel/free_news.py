"""Free news enrichers (no WorldMonitor Pro key required).

Sources (all free / freemium):
  - Yahoo Finance ticker news (via yfinance)
  - Public RSS (Yahoo finance, Reuters business, CNBC)
  - Finnhub free tier (optional FINNHUB_API_KEY)
  - NewsAPI free tier (optional NEWS_API_KEY)
  - Alpha Vantage news sentiment (optional ALPHA_VANTAGE_API_KEY)
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

SYMBOL_QUERY = {
    "XAUUSD": "gold price OR XAUUSD OR bullion OR \"gold futures\"",
    "XAGUSD": "silver price OR XAGUSD OR \"silver futures\"",
    "NAS100": "Nasdaq OR \"Nasdaq 100\" OR NDX",
    "US30": "Dow OR \"Dow Jones\" OR DJIA",
    "SPX500": "\"S&P 500\" OR SPX OR \"S&P500\"",
    "USOIL": "\"crude oil\" OR WTI OR Brent OR \"oil prices\"",
    "EURUSD": "EURUSD OR \"euro dollar\" OR ECB forex",
}

BULLISH_WORDS = re.compile(
    r"\b(rally|surge|gain|bull|rise|upbeat|growth|beat|record high|optimistic)\b",
    re.I,
)
BEARISH_WORDS = re.compile(
    r"\b(fall|drop|crash|bear|slump|fear|recession|miss|selloff|war|strike)\b",
    re.I,
)


def _score_headline(text: str) -> float:
    bull = len(BULLISH_WORDS.findall(text or ""))
    bear = len(BEARISH_WORDS.findall(text or ""))
    if bull == bear == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / max(bull + bear, 1)))


def _rss_items(url: str, limit: int = 8) -> list[str]:
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "UTI-NewsBot/1.0"})
        if resp.status_code >= 400:
            return []
        root = ET.fromstring(resp.content)
        titles: list[str] = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            if title:
                titles.append(title)
        return titles
    except Exception as exc:
        logger.info("RSS fetch failed %s: %s", url, exc)
        return []


def fetch_gdelt_headlines(symbol: str) -> list[str]:
    """GDELT DOC API — free, no key (optional; often slow/blocked on free hosts)."""
    if os.getenv("UTI_NEWS_GDELT", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return []
    query = SYMBOL_QUERY.get(symbol.upper(), symbol).split(" OR ")[0].strip().strip('"')
    try:
        resp = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query,
                "mode": "ArtList",
                "maxrecords": 8,
                "format": "json",
                "sort": "DateDesc",
            },
            timeout=5,
            headers={"User-Agent": "UTI-NewsBot/1.0"},
        )
        if resp.status_code >= 400:
            return []
        arts = (resp.json() or {}).get("articles") or []
        return [str(a.get("title")).strip() for a in arts if a.get("title")]
    except Exception as exc:
        logger.info("GDELT skipped: %s", exc)
        return []


def fetch_rss_headlines(symbol: str) -> list[str]:
    query = SYMBOL_QUERY.get(symbol.upper(), symbol)
    # Skip hosts that often fail DNS on free PaaS (e.g. feeds.reuters.com on Render)
    feeds = [
        f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
    ]
    out: list[str] = []
    for url in feeds:
        for title in _rss_items(url, limit=5):
            if title not in out:
                out.append(title)
        if len(out) >= 10:
            break
    return out[:10]


def fetch_finnhub_news(symbol: str) -> list[str]:
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return []
    # Map to equity-ish tickers where possible
    ticker = {
        "XAUUSD": "GLD",
        "XAGUSD": "SLV",
        "NAS100": "QQQ",
        "US30": "DIA",
        "SPX500": "SPY",
        "USOIL": "USO",
        "EURUSD": "FXE",
    }.get(symbol.upper(), symbol)
    try:
        from datetime import datetime, timedelta, timezone

        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=7)
        resp = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={
                "symbol": ticker,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": key,
            },
            timeout=8,
        )
        if resp.status_code >= 400:
            return []
        return [str(a.get("headline")) for a in (resp.json() or [])[:8] if a.get("headline")]
    except Exception as exc:
        logger.info("Finnhub news skipped: %s", exc)
        return []


def fetch_newsapi(symbol: str) -> list[str]:
    key = os.getenv("NEWS_API_KEY", "").strip()
    if not key:
        return []
    q = SYMBOL_QUERY.get(symbol.upper(), symbol)
    try:
        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": q, "language": "en", "pageSize": 8, "sortBy": "publishedAt", "apiKey": key},
            timeout=8,
        )
        if resp.status_code >= 400:
            return []
        arts = (resp.json() or {}).get("articles") or []
        return [str(a.get("title")) for a in arts if a.get("title")]
    except Exception as exc:
        logger.info("NewsAPI skipped: %s", exc)
        return []


def enrich_news(symbol: str, existing: list[str] | None = None) -> dict[str, Any]:
    """Merge free news sources into one brief."""
    headlines = list(existing or [])
    sources_used: list[str] = []

    rss = fetch_rss_headlines(symbol)
    if rss:
        sources_used.append("rss")
        for h in rss:
            if h not in headlines:
                headlines.append(h)

    gdelt = fetch_gdelt_headlines(symbol)
    if gdelt:
        sources_used.append("gdelt")
        for h in gdelt:
            if h not in headlines:
                headlines.append(h)

    for name, fn in (("finnhub", fetch_finnhub_news), ("newsapi", fetch_newsapi)):
        got = fn(symbol)
        if got:
            sources_used.append(name)
            for h in got:
                if h not in headlines:
                    headlines.append(h)

    headlines = headlines[:12]
    scores = [_score_headline(h) for h in headlines]
    news_score = sum(scores) / len(scores) if scores else 0.0
    news_score = max(-1.0, min(1.0, news_score))
    macro = "BULLISH" if news_score > 0.15 else "BEARISH" if news_score < -0.15 else "NEUTRAL"
    return {
        "headlines": headlines,
        "news_score": round(news_score, 4),
        "macro_bias": macro,
        "sources": sources_used or ["none"],
        "count": len(headlines),
    }
