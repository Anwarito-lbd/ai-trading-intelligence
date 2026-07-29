"""Keyless / freemium market-data helpers.

Sources (no paid plan required):
  - Yahoo Finance chart API (no key)
  - yfinance
  - Frankfurter (EURUSD FX, no key)
  - Finnhub quote (optional free FINNHUB_API_KEY)
  - Stooq CSV (best-effort)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from uti_agents.ticker_map import to_yfinance

logger = logging.getLogger(__name__)

_YF_HEADERS = {"User-Agent": "Mozilla/5.0 UTI-FreeMarket/1.0"}


def yahoo_chart_price(symbol: str) -> dict[str, Any] | None:
    yf_sym = to_yfinance(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}"
    try:
        resp = requests.get(
            url,
            params={"interval": "1m", "range": "1d"},
            headers=_YF_HEADERS,
            timeout=8,
        )
        if resp.status_code >= 400:
            resp = requests.get(
                url,
                params={"interval": "5m", "range": "5d"},
                headers=_YF_HEADERS,
                timeout=8,
            )
        if resp.status_code >= 400:
            return None
        result = ((resp.json() or {}).get("chart") or {}).get("result") or []
        if not result:
            return None
        meta = result[0].get("meta") or {}
        price = meta.get("regularMarketPrice") or meta.get("postMarketPrice")
        if price is None:
            closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
            closes = [c for c in closes if c is not None]
            price = closes[-1] if closes else None
        if price is None:
            return None
        return {
            "symbol": symbol.upper(),
            "yf_symbol": yf_sym,
            "price": round(float(price), 4),
            "asof": datetime.now(timezone.utc).isoformat(),
            "source": "yahoo_chart",
        }
    except Exception as exc:
        logger.info("yahoo chart failed %s: %s", symbol, exc)
        return None


def frankfurter_eurusd() -> dict[str, Any] | None:
    try:
        resp = requests.get("https://api.frankfurter.app/latest?from=EUR&to=USD", timeout=8)
        if resp.status_code >= 400:
            return None
        rate = float(((resp.json() or {}).get("rates") or {}).get("USD") or 0)
        if rate <= 0:
            return None
        return {
            "symbol": "EURUSD",
            "yf_symbol": "EURUSD=X",
            "price": round(rate, 5),
            "asof": str((resp.json() or {}).get("date")),
            "source": "frankfurter",
        }
    except Exception as exc:
        logger.info("frankfurter failed: %s", exc)
        return None


def finnhub_quote(symbol: str) -> dict[str, Any] | None:
    import os

    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        return None
    # Finnhub free: equities / some forex; map futures to liquid ETFs / FX pairs
    mapped = {
        "XAUUSD": "OANDA:XAU_USD",
        "XAGUSD": "OANDA:XAG_USD",
        "EURUSD": "OANDA:EUR_USD",
        "NAS100": "QQQ",
        "US30": "DIA",
        "SPX500": "SPY",
        "USOIL": "USO",
    }.get(symbol.upper())
    if not mapped:
        return None
    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": mapped, "token": key},
            timeout=8,
        )
        if resp.status_code >= 400:
            return None
        data = resp.json() or {}
        price = data.get("c")
        if not price:
            return None
        return {
            "symbol": symbol.upper(),
            "yf_symbol": mapped,
            "price": round(float(price), 4),
            "asof": datetime.now(timezone.utc).isoformat(),
            "source": "finnhub",
        }
    except Exception as exc:
        logger.info("finnhub quote failed %s: %s", symbol, exc)
        return None


def fetch_free_price(symbol: str) -> dict[str, Any] | None:
    """Best-effort free live price across public APIs."""
    sym = (symbol or "").upper()
    if sym == "EURUSD":
        fx = frankfurter_eurusd()
        if fx:
            return fx
    for fn in (yahoo_chart_price, finnhub_quote):
        got = fn(sym)
        if got and got.get("price"):
            return got
    return None
