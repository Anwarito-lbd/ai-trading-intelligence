"""Live market price helpers for UTI paper fills."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

SYMBOL_TO_YF = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "SILVER": "SI=F",
    "NAS100": "NQ=F",
    "NASDAQ": "NQ=F",
    "US100": "NQ=F",
    "NDX": "^NDX",
    "US30": "YM=F",
    "DJ30": "YM=F",
    "DOW": "YM=F",
    "SPX500": "ES=F",
    "SPX": "ES=F",
    "US500": "ES=F",
    "SP500": "ES=F",
    "USOIL": "CL=F",
    "OIL": "CL=F",
    "WTI": "CL=F",
    "EURUSD": "EURUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SPY": "SPY",
    "QQQ": "QQQ",
}


def fetch_live_price(symbol: str) -> dict[str, Any] | None:
    """Return live mid/last for a UTI symbol, or None."""
    yf_sym = SYMBOL_TO_YF.get(symbol.upper(), symbol)
    try:
        import yfinance as yf

        hist = yf.Ticker(yf_sym).history(period="1d", interval="1m")
        if hist is None or hist.empty:
            hist = yf.Ticker(yf_sym).history(period="5d", interval="5m")
        if hist is None or hist.empty:
            return None
        last = float(hist["Close"].iloc[-1])
        return {
            "symbol": symbol.upper(),
            "yf_symbol": yf_sym,
            "price": round(last, 4),
            "asof": str(hist.index[-1]),
            "source": "yfinance",
        }
    except Exception as exc:
        logger.warning("live price fetch failed for %s: %s", symbol, exc)
        return None


def resolve_entry_price(symbol: str, webhook_entry: float | None) -> tuple[float | None, dict[str, Any]]:
    """Prefer live market price; keep webhook entry only if close to live.

    TradingView {{close}} should be near live. Stale demo entries (e.g. 2650 when
    gold is ~4075) are replaced automatically.
    """
    live = fetch_live_price(symbol)
    meta: dict[str, Any] = {"live": live, "webhook_entry": webhook_entry}
    if live and live.get("price"):
        live_px = float(live["price"])
        if webhook_entry and webhook_entry > 0:
            # If webhook is within 5% of live, trust TradingView close
            if abs(webhook_entry - live_px) / live_px <= 0.05:
                meta["chosen"] = "webhook"
                return float(webhook_entry), meta
            meta["chosen"] = "live_overrode_stale_webhook"
            meta["reason"] = f"webhook {webhook_entry} far from live {live_px}"
            return live_px, meta
        meta["chosen"] = "live"
        return live_px, meta
    if webhook_entry and webhook_entry > 0:
        meta["chosen"] = "webhook_no_live"
        return float(webhook_entry), meta
    meta["chosen"] = "none"
    return None, meta


def get_paper_agent_cash() -> tuple[int | None, float]:
    """Return (agent_id, cash) for the configured paper agent."""
    from database import get_db_connection

    agent_token = os.getenv("UTI_PAPER_AGENT_TOKEN", "").strip()
    agent_id_env = os.getenv("UTI_PAPER_AGENT_ID", "").strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if agent_id_env:
            cursor.execute("SELECT id, cash FROM agents WHERE id = ?", (int(agent_id_env),))
        elif agent_token:
            cursor.execute("SELECT id, cash FROM agents WHERE token = ?", (agent_token,))
        else:
            return None, float(os.getenv("UTI_PAPER_STARTING_CASH", "100"))
        row = cursor.fetchone()
        if not row:
            return None, float(os.getenv("UTI_PAPER_STARTING_CASH", "100"))
        return int(row["id"]), float(row["cash"] or 0)
    finally:
        conn.close()
