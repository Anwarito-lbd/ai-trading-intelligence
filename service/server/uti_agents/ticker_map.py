"""Shared UTI symbol → Yahoo Finance ticker mapping."""

from __future__ import annotations

SYMBOL_TO_YF: dict[str, str] = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "NAS100": "NQ=F",
    "NASDAQ": "NQ=F",
    "US30": "YM=F",
    "DJ30": "YM=F",
    "SPX500": "ES=F",
    "SPX": "ES=F",
    "USOIL": "CL=F",
    "OIL": "CL=F",
    "WTI": "CL=F",
    "EURUSD": "EURUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}


def to_yfinance(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    return SYMBOL_TO_YF.get(sym, sym)
