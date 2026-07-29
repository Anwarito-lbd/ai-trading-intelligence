"""Symbol / timeframe normalization helpers."""

from __future__ import annotations

SYMBOL_ALIASES = {
    "XAU": "XAUUSD",
    "GOLD": "XAUUSD",
    "XAUUSD": "XAUUSD",
    "XAU/USD": "XAUUSD",
    "BTC": "BTCUSD",
    "BTCUSDT": "BTCUSD",
    "BTCUSD": "BTCUSD",
    "ETH": "ETHUSD",
    "ETHUSDT": "ETHUSD",
    "ETHUSD": "ETHUSD",
}

TIMEFRAME_ALIASES = {
    "1": "1",
    "1m": "1",
    "3": "3",
    "5": "5",
    "5m": "5",
    "15": "15",
    "15m": "15",
    "30": "30",
    "30m": "30",
    "60": "60",
    "1h": "60",
    "240": "240",
    "4h": "240",
    "d": "D",
    "1d": "D",
    "day": "D",
}


def normalize_symbol(raw: str | None) -> str:
    if not raw:
        return "UNKNOWN"
    cleaned = str(raw).strip().upper().replace(" ", "")
    # Strip exchange prefixes like BINANCE: or OANDA:
    if ":" in cleaned:
        cleaned = cleaned.split(":")[-1]
    return SYMBOL_ALIASES.get(cleaned, cleaned)


def normalize_timeframe(raw: str | None) -> str:
    if not raw:
        return "15"
    cleaned = str(raw).strip().lower()
    return TIMEFRAME_ALIASES.get(cleaned, str(raw).strip())
