"""TradingView → Telegram bridge (notify-only).

Pine/TV alerts are NOT merged into the AI desk / confluence.
When an indicator fires, Telegram gets: the alert + current AI status for that symbol.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from decisions import store
from intel.symbols import normalize_symbol, normalize_timeframe
from uti_agents.live_price import fetch_live_price
from uti_agents.telegram_notify import send_telegram, telegram_configured

logger = logging.getLogger(__name__)


def get_ai_snapshot(symbol: str, timeframe: str | None = None) -> dict[str, Any]:
    """Latest AI desk state for a symbol (from stored decisions / last scan). Does not re-run AI."""
    symbol_n = normalize_symbol(symbol or "XAUUSD")
    tf_n = normalize_timeframe(timeframe or os.getenv("UTI_SCAN_TIMEFRAME", "30"))
    decisions = store.list_decisions(limit=20, symbol=symbol_n)
    latest = decisions[0] if decisions else None
    live = fetch_live_price(symbol_n)

    if not latest:
        return {
            "symbol": symbol_n,
            "timeframe": tf_n,
            "available": False,
            "label": "NO RECENT AI SCAN",
            "message": "AI has not scanned this symbol yet (wait for next scanner cycle).",
            "live_price": live,
        }

    quality = latest.get("signal_quality") if isinstance(latest.get("signal_quality"), dict) else {}
    consensus = latest.get("consensus") if isinstance(latest.get("consensus"), dict) else {}
    kronos = latest.get("kronos") if isinstance(latest.get("kronos"), dict) else {}
    swarm = latest.get("swarm") if isinstance(latest.get("swarm"), dict) else {}
    providers = latest.get("providers_used") if isinstance(latest.get("providers_used"), dict) else {}

    return {
        "symbol": symbol_n,
        "timeframe": latest.get("timeframe") or tf_n,
        "available": True,
        "label": latest.get("signal_label") or latest.get("decision") or "NO SIGNAL",
        "good_trade": bool(latest.get("good_trade")),
        "ai_confidence": latest.get("ai_confidence"),
        "quality_score": quality.get("quality_score"),
        "quality_reasons": quality.get("reasons") or [],
        "macro_bias": latest.get("macro_bias"),
        "news_score": latest.get("news_score"),
        "consensus_action": consensus.get("action") if consensus else None,
        "consensus_reason": consensus.get("reason") or latest.get("consensus_reason"),
        "kronos_bias": kronos.get("bias"),
        "kronos_source": kronos.get("source"),
        "swarm_bias": swarm.get("bias"),
        "brain_mode": latest.get("brain_mode"),
        "providers_used": providers,
        "trade_label": latest.get("trade_label"),
        "created_at": latest.get("created_at") or latest.get("received_at"),
        "live_price": live,
        "pip_plan": latest.get("pip_plan") if isinstance(latest.get("pip_plan"), dict) else {},
    }


def format_tv_telegram_message(
    *,
    indicator_id: str,
    indicator_name: str | None,
    side: str,
    symbol: str,
    timeframe: str,
    entry: float | None,
    strength: float | None,
    raw_note: str | None,
    ai: dict[str, Any],
) -> str:
    name = indicator_name or indicator_id
    lines = [
        "📡 TRADINGVIEW ALERT (not merged into AI)",
        f"Indicator: {name}",
        f"Market: {symbol} · TF {timeframe}m",
        f"TV side: {side}",
    ]
    if entry is not None:
        lines.append(f"TV price/entry: {entry}")
    if strength is not None:
        lines.append(f"Strength: {strength}")
    if raw_note:
        note = raw_note.strip()
        if len(note) > 180:
            note = note[:177] + "..."
        lines.append(f"Note: {note}")

    lines.append("")
    lines.append("🤖 CURRENT AI STATUS (same desk, separate from this alert)")
    if not ai.get("available"):
        lines.append(ai.get("message") or "No recent AI scan.")
    else:
        lines.append(f"AI label: {ai.get('label')} · confidence {ai.get('ai_confidence', '—')}")
        lines.append(f"Quality: {ai.get('quality_score', '—')}/100 · good_trade={ai.get('good_trade')}")
        lines.append(
            f"WM macro={ai.get('macro_bias')} news={ai.get('news_score')} · "
            f"Kronos={ai.get('kronos_bias')} ({ai.get('kronos_source')}) · "
            f"Swarm={ai.get('swarm_bias')}"
        )
        if ai.get("consensus_action") or ai.get("consensus_reason"):
            lines.append(
                f"Consensus: {ai.get('consensus_action') or '—'} — "
                f"{(ai.get('consensus_reason') or '')[:120]}"
            )
        live = ai.get("live_price") if isinstance(ai.get("live_price"), dict) else None
        if live and live.get("price"):
            lines.append(f"Live: {live.get('price')} ({live.get('source')})")
        if ai.get("created_at"):
            lines.append(f"AI as-of: {ai.get('created_at')}")

    lines.append("")
    lines.append("You decide on TradingView. AI is context only — not auto-trading this alert.")
    return "\n".join(lines)


def notify_tv_alert(
    *,
    indicator_id: str,
    indicator_name: str | None = None,
    symbol: str,
    timeframe: str | None,
    side: str,
    entry: float | None = None,
    strength: float | None = None,
    raw_note: str | None = None,
) -> dict[str, Any]:
    symbol_n = normalize_symbol(symbol)
    tf_n = normalize_timeframe(timeframe or "30")
    ai = get_ai_snapshot(symbol_n, tf_n)
    text = format_tv_telegram_message(
        indicator_id=indicator_id,
        indicator_name=indicator_name,
        side=(side or "NEUTRAL").upper(),
        symbol=symbol_n,
        timeframe=tf_n,
        entry=entry,
        strength=strength,
        raw_note=raw_note,
        ai=ai,
    )
    if not telegram_configured():
        return {
            "ok": False,
            "reason": "telegram_not_configured",
            "preview": text,
            "ai_snapshot": ai,
        }
    tg = send_telegram(text)
    return {"ok": bool(tg.get("ok")), "telegram": tg, "ai_snapshot": ai, "message": text}
