"""Pip-based trade plan (entry / stop / targets) for forex & metals.

Defaults (override with env):
  XAUUSD pip = 0.1   (common gold pip; some brokers use 0.01)
  FX pip     = 0.0001
  JPY pairs  = 0.01
"""

from __future__ import annotations

import os
from typing import Any


def pip_size(symbol: str) -> float:
    sym = (symbol or "").upper()
    env_key = f"UTI_PIP_SIZE_{sym}"
    if os.getenv(env_key):
        return float(os.getenv(env_key, "0.1"))
    if os.getenv("UTI_PIP_SIZE"):
        # Global override
        if sym in {"XAUUSD", "XAGUSD"} or sym.startswith("XAU") or sym.startswith("XAG"):
            return float(os.getenv("UTI_PIP_SIZE", "0.1"))
    if sym in {"XAUUSD"} or sym.startswith("XAU"):
        return float(os.getenv("UTI_PIP_SIZE_XAUUSD", "0.1"))
    if sym in {"XAGUSD"} or sym.startswith("XAG"):
        return float(os.getenv("UTI_PIP_SIZE_XAGUSD", "0.01"))
    if "JPY" in sym:
        return 0.01
    if sym in {"BTCUSD", "ETHUSD"} or sym.endswith("USDT"):
        return float(os.getenv("UTI_PIP_SIZE_CRYPTO", "1.0"))
    return 0.0001


def build_pip_plan(
    *,
    symbol: str,
    decision: str,
    entry: float | None,
    sl: float | None = None,
    tps: list[float] | None = None,
    stop_pips: float | None = None,
    tp1_pips: float | None = None,
    tp2_pips: float | None = None,
) -> dict[str, Any]:
    """Build / normalize SL & TP as prices + pip distances."""
    decision_u = (decision or "WAIT").upper()
    size = pip_size(symbol)
    stop_pips = float(stop_pips if stop_pips is not None else os.getenv("UTI_DEFAULT_STOP_PIPS", "80"))
    tp1_pips = float(tp1_pips if tp1_pips is not None else os.getenv("UTI_DEFAULT_TP1_PIPS", "120"))
    tp2_pips = float(tp2_pips if tp2_pips is not None else os.getenv("UTI_DEFAULT_TP2_PIPS", "200"))

    if not entry or decision_u not in {"BUY", "SELL"}:
        return {
            "symbol": symbol,
            "decision": decision_u,
            "pip_size": size,
            "action": "WAIT",
            "message": "No trade — waiting for aligned researchers",
            "entry": entry,
            "stop_pips": stop_pips,
            "tp1_pips": tp1_pips,
            "tp2_pips": tp2_pips,
        }

    # Prefer provided SL/TP; else synthesize from default pip distances
    if sl and abs(float(sl) - float(entry)) > 0:
        stop_pips = round(abs(float(entry) - float(sl)) / size, 1)
    else:
        if decision_u == "BUY":
            sl = round(float(entry) - stop_pips * size, 4)
        else:
            sl = round(float(entry) + stop_pips * size, 4)

    tp_list = list(tps or [])
    if len(tp_list) < 2:
        if decision_u == "BUY":
            tp_list = [
                round(float(entry) + tp1_pips * size, 4),
                round(float(entry) + tp2_pips * size, 4),
            ]
        else:
            tp_list = [
                round(float(entry) - tp1_pips * size, 4),
                round(float(entry) - tp2_pips * size, 4),
            ]
    else:
        tp1_pips = round(abs(float(tp_list[0]) - float(entry)) / size, 1)
        tp2_pips = round(abs(float(tp_list[1]) - float(entry)) / size, 1) if len(tp_list) > 1 else tp1_pips

    rr = round(tp1_pips / stop_pips, 2) if stop_pips else None
    side_word = "BUY" if decision_u == "BUY" else "SELL"
    message = (
        f"{side_word} {symbol} @ {entry} | "
        f"Stop {stop_pips:g} pips → {sl} | "
        f"TP1 {tp1_pips:g} pips → {tp_list[0]} | "
        f"TP2 {tp2_pips:g} pips → {tp_list[1]} | "
        f"R:R≈{rr} (pip={size})"
    )

    return {
        "symbol": symbol,
        "decision": decision_u,
        "action": side_word,
        "pip_size": size,
        "entry": float(entry),
        "sl": float(sl) if sl is not None else None,
        "tps": [float(x) for x in tp_list[:3]],
        "stop_pips": stop_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips,
        "rr_pips": rr,
        "message": message,
        "instructions": [
            f"1. Enter {side_word} at {entry}",
            f"2. Place stop loss at {sl} ({stop_pips:g} pips)",
            f"3. Take profit 1 at {tp_list[0]} ({tp1_pips:g} pips)",
            f"4. Take profit 2 at {tp_list[1]} ({tp2_pips:g} pips)",
            "5. Desk also paper-fills this plan automatically when risk approves",
        ],
    }
