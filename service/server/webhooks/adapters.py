"""Normalize heterogeneous TradingView / Pine alert payloads into canonical votes."""

from __future__ import annotations

import json
import re
from typing import Any

from confluence.engine import normalize_side
from confluence.indicators import KNOWN_INDICATOR_IDS
from intel.symbols import normalize_symbol, normalize_timeframe

TEXT_BUY = re.compile(r"\b(buy|long|bull(?:ish)?|go\s*long)\b", re.I)
TEXT_SELL = re.compile(r"\b(sell|short|bear(?:ish)?|go\s*short)\b", re.I)
TEXT_STRONG = re.compile(r"\bstrong\b", re.I)


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _extract_tps(payload: dict[str, Any]) -> list[float]:
    tps: list[float] = []
    if isinstance(payload.get("tps"), list):
        for item in payload["tps"]:
            f = _coerce_float(item)
            if f is not None:
                tps.append(f)
    for key in ("tp1", "tp2", "tp3", "tp4", "takeProfitPrice1", "takeProfitPrice2", "takeProfitPrice3"):
        f = _coerce_float(payload.get(key))
        if f is not None:
            tps.append(f)
    return tps[:4]


def _side_from_action(action: str | None) -> str | None:
    if not action:
        return None
    a = action.strip().lower()
    if a in {"buy", "long", "entrylong", "openlong"}:
        return "BUY"
    if a in {"sell", "short", "entryshort", "openshort"}:
        return "SELL"
    if a in {"closelong", "closeshort", "close", "flat"}:
        return "NEUTRAL"
    if "buy" in a or "long" in a:
        return "BUY"
    if "sell" in a or "short" in a:
        return "SELL"
    return None


def parse_text_alert(text: str) -> dict[str, Any]:
    side = "NEUTRAL"
    if TEXT_BUY.search(text) and not TEXT_SELL.search(text):
        side = "BUY"
    elif TEXT_SELL.search(text) and not TEXT_BUY.search(text):
        side = "SELL"
    elif TEXT_BUY.search(text) and TEXT_SELL.search(text):
        # Both present — prefer the first match order in string
        buy_at = TEXT_BUY.search(text).start()
        sell_at = TEXT_SELL.search(text).start()
        side = "BUY" if buy_at < sell_at else "SELL"
    strength = 0.9 if TEXT_STRONG.search(text) else 0.7
    return {"side": side, "strength": strength, "raw_text": text}


def normalize_payload(
    indicator_id: str,
    body: Any,
    *,
    received_at: str,
) -> dict[str, Any]:
    if indicator_id not in KNOWN_INDICATOR_IDS:
        raise ValueError(f"Unknown indicator_id: {indicator_id}")

    payload: dict[str, Any]
    if isinstance(body, str):
        text = body.strip()
        if text.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                parsed = parse_text_alert(text)
                payload = {"side": parsed["side"], "strength": parsed["strength"], "raw_text": text}
        else:
            parsed = parse_text_alert(text)
            payload = {"side": parsed["side"], "strength": parsed["strength"], "raw_text": text}
    elif isinstance(body, dict):
        payload = dict(body)
    else:
        raise ValueError("Webhook body must be JSON object or text")

    # Triple Confluence / generic action fields
    side = payload.get("side") or _side_from_action(payload.get("action") or payload.get("direction"))
    if not side and payload.get("entrySide"):
        side = _side_from_action(str(payload.get("entrySide")))
    if not side and payload.get("raw_text"):
        side = parse_text_alert(str(payload["raw_text"]))["side"]
    if not side:
        # Smart Trader pressure meta
        pressure = str((payload.get("meta") or {}).get("pressure") or payload.get("pressure") or "").upper()
        if pressure in {"BUYING", "BUY", "BULLISH"}:
            side = "BUY"
        elif pressure in {"SELLING", "SELL", "BEARISH"}:
            side = "SELL"
        else:
            side = "NEUTRAL"

    side = normalize_side(str(side))
    strength = _coerce_float(payload.get("strength"))
    if strength is None:
        strength = 0.9 if "strong" in str(payload.get("raw_text") or "").lower() else 0.75

    symbol = normalize_symbol(
        payload.get("symbol")
        or payload.get("ticker")
        or payload.get("pair")
        or payload.get("sym")
    )
    timeframe = normalize_timeframe(payload.get("timeframe") or payload.get("tf") or payload.get("interval"))
    entry = _coerce_float(payload.get("entry") or payload.get("price") or payload.get("entryLimitPrice"))
    sl = _coerce_float(payload.get("sl") or payload.get("stop") or payload.get("stopLoss") or payload.get("stop_loss"))
    tps = _extract_tps(payload)
    bar_time = payload.get("bar_time") or payload.get("time") or payload.get("executed_at") or received_at

    dedupe_key = (
        f"{indicator_id}|{symbol}|{timeframe}|{side}|{bar_time}|{entry or ''}"
    )

    return {
        "indicator_id": indicator_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "side": side,
        "strength": max(0.0, min(1.0, float(strength))),
        "entry": entry,
        "sl": sl,
        "tps": tps,
        "bar_time": bar_time,
        "received_at": received_at,
        "dedupe_key": dedupe_key,
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
        "raw": payload,
    }
