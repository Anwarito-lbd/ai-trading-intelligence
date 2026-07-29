"""Telegram alerts for high-quality UTI setups (notify-only; you decide)."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)


def telegram_configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip())


def send_telegram(text: str, *, disable_preview: bool = True) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"ok": False, "reason": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"}
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": disable_preview,
            },
            timeout=20,
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or not data.get("ok"):
            logger.warning("Telegram send failed: %s %s", resp.status_code, data)
            return {"ok": False, "status_code": resp.status_code, "response": data}
        return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}
    except Exception as exc:
        logger.warning("Telegram error: %s", exc)
        return {"ok": False, "reason": str(exc)}


def format_setup_alert(result: dict[str, Any]) -> str:
    """Human message: signal + pips — user confirms on TradingView."""
    dec = result.get("decision") or {}
    pip = result.get("pip_plan") or dec.get("pip_plan") or {}
    quality = result.get("signal_quality") or dec.get("signal_quality") or {}
    live = result.get("live_price") or dec.get("live_price") or {}
    symbol = dec.get("symbol") or result.get("confluence", {}).get("symbol") or "?"
    tf = dec.get("timeframe") or "30"
    side = (result.get("signal_label") or dec.get("decision") or "?").upper()
    price = pip.get("entry") or (live.get("price") if isinstance(live, dict) else None)

    lines = [
        f"UTI GOOD SETUP · {symbol} {tf}M",
        f"Signal: {side}",
        f"Quality: {quality.get('quality_score', '—')}/100",
    ]
    if price:
        lines.append(f"Live/entry: {price}")
    if pip.get("message"):
        lines.append(pip["message"])
    elif pip.get("stop_pips") is not None:
        lines.append(
            f"Stop {pip.get('stop_pips')} pips · TP1 {pip.get('tp1_pips')} · TP2 {pip.get('tp2_pips')}"
        )
    intel = result.get("intel") or {}
    lines.append(
        f"WM: {intel.get('macro_bias')} news={intel.get('news_score')} · "
        f"Kronos: {(result.get('kronos') or {}).get('bias')} · "
        f"Swarm: {(result.get('swarm') or {}).get('bias')}"
    )
    lines.append("")
    lines.append("Compare with your TradingView indicators, then decide yourself.")
    lines.append("(Paper auto-fill OFF for scanner alerts)")
    return "\n".join(lines)
