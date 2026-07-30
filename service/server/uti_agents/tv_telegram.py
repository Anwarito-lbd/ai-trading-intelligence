"""TradingView signal → strengthen with UTI desk (notify-only).

Flow:
  TV indicator alert
    → Gemini Search grounding (live ticker news)
    → WorldMonitor / MiroFish / Kronos / TradingAgents snapshot
    → Reinforcement verdict (STRENGTHENS / CONFLICTS / CAUTION)
    → Telegram

Pine/TV is NOT merged into confluence votes. You still decide.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from decisions import store
from intel.gemini_grounding import (
    decision_matrix_line,
    fetch_grounded_market_brief,
    grounding_enabled,
)
from intel.symbols import normalize_symbol, normalize_timeframe
from uti_agents.live_price import fetch_live_price
from uti_agents.telegram_notify import send_telegram, telegram_configured

logger = logging.getLogger(__name__)


def get_ai_snapshot(symbol: str, timeframe: str | None = None) -> dict[str, Any]:
    """Latest AI desk state for a symbol (from stored decisions / last scan)."""
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
    grounded = latest.get("gemini_grounding") if isinstance(latest.get("gemini_grounding"), dict) else {}

    return {
        "symbol": symbol_n,
        "timeframe": latest.get("timeframe") or tf_n,
        "available": True,
        "label": latest.get("signal_label") or latest.get("decision") or "NO SIGNAL",
        "raw_decision": latest.get("raw_decision") or latest.get("trader"),
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
        "gemini_grounding": grounded,
        "trade_label": latest.get("trade_label"),
        "created_at": latest.get("created_at") or latest.get("received_at"),
        "live_price": live,
        "pip_plan": latest.get("pip_plan") if isinstance(latest.get("pip_plan"), dict) else {},
    }


def _side_to_bias(side: str) -> str:
    s = (side or "").upper()
    if s in {"BUY", "LONG", "BULLISH"}:
        return "BULLISH"
    if s in {"SELL", "SHORT", "BEARISH"}:
        return "BEARISH"
    return "NEUTRAL"


def strengthen_decision(
    *,
    tv_side: str,
    ai: dict[str, Any],
    grounded: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare TV signal vs integrated desk → reinforcement verdict."""
    tv_bias = _side_to_bias(tv_side)
    votes: list[tuple[str, str]] = []

    if ai.get("available"):
        for role, key in (
            ("consensus", "consensus_action"),
            ("macro", "macro_bias"),
            ("kronos", "kronos_bias"),
            ("swarm", "swarm_bias"),
            ("ai_label", "label"),
        ):
            votes.append((role, _side_to_bias(str(ai.get(key) or ""))))
    if grounded and grounded.get("ok"):
        votes.append(("gemini_search", _side_to_bias(str(grounded.get("bias") or ""))))

    agree = [r for r, b in votes if b == tv_bias and b != "NEUTRAL"]
    conflict = [r for r, b in votes if b != "NEUTRAL" and b != tv_bias and tv_bias != "NEUTRAL"]
    neutral = [r for r, b in votes if b == "NEUTRAL"]

    if tv_bias == "NEUTRAL":
        verdict = "CAUTION"
        advice = "TV side unclear — wait for a clean BUY/SELL from your indicator."
    elif len(agree) >= 2 and len(conflict) == 0:
        verdict = "STRENGTHENS"
        advice = f"Desk agrees with TV {tv_side}: {', '.join(agree)}."
    elif len(agree) >= 1 and len(conflict) == 0:
        verdict = "STRENGTHENS"
        advice = f"Partial support for TV {tv_side}: {', '.join(agree)}. Others neutral."
    elif len(conflict) >= 2:
        verdict = "CONFLICTS"
        advice = f"Desk fights TV {tv_side}: conflict from {', '.join(conflict)}. Prefer WAIT."
    elif len(conflict) >= 1 and len(agree) >= 1:
        verdict = "CAUTION"
        advice = (
            f"Mixed: agrees={', '.join(agree) or '—'} conflicts={', '.join(conflict)}. "
            "Size down or skip."
        )
    elif len(conflict) >= 1:
        verdict = "CONFLICTS"
        advice = f"At least one researcher fights TV {tv_side}: {', '.join(conflict)}."
    else:
        verdict = "CAUTION"
        advice = "Desk mostly neutral — TV signal stands alone; be selective."

    conf = float(ai.get("ai_confidence") or 0) if ai.get("available") else 0.0
    g_score = float((grounded or {}).get("news_score") or 0)
    strength_score = min(
        100.0,
        max(
            0.0,
            40.0
            + 12.0 * len(agree)
            - 15.0 * len(conflict)
            + (10.0 if conf >= 65 else 0.0)
            + (8.0 if (tv_bias == "BULLISH" and g_score > 0.15) or (tv_bias == "BEARISH" and g_score < -0.15) else 0.0),
        ),
    )

    return {
        "tv_bias": tv_bias,
        "verdict": verdict,
        "advice": advice,
        "agree": agree,
        "conflict": conflict,
        "neutral": neutral,
        "strength_score": round(strength_score, 1),
        "votes": [{"role": r, "bias": b} for r, b in votes],
    }


def maybe_refresh_ai(symbol: str, timeframe: str) -> dict[str, Any]:
    """Optionally re-run the desk when a TV alert arrives (default: use last scan)."""
    refresh = os.getenv("UTI_TV_REFRESH_AI", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not refresh:
        return get_ai_snapshot(symbol, timeframe)
    try:
        from decisions.orchestrator import run_decision_cycle

        out = run_decision_cycle(symbol=symbol, timeframe=timeframe, force=True, paper=False)
        # Prefer freshly stored snapshot
        snap = get_ai_snapshot(symbol, timeframe)
        snap["refreshed"] = True
        snap["refresh_status"] = out.get("status")
        return snap
    except Exception as exc:
        logger.warning("TV AI refresh failed: %s", exc)
        snap = get_ai_snapshot(symbol, timeframe)
        snap["refresh_error"] = str(exc)
        return snap


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
    grounded: dict[str, Any] | None,
    reinforcement: dict[str, Any],
) -> str:
    name = indicator_name or indicator_id
    verdict = reinforcement.get("verdict") or "CAUTION"
    lines = [
        f"[ Trading Signal Received ] {symbol}",
        f"TV indicator: {name}",
        f"TV side: {side} · TF {timeframe}m",
    ]
    if entry is not None:
        lines.append(f"TV entry/price: {entry}")
    if strength is not None:
        lines.append(f"TV strength: {strength}")
    if raw_note:
        note = raw_note.strip()
        if len(note) > 160:
            note = note[:157] + "..."
        lines.append(f"Note: {note}")

    lines.append("")
    lines.append("[ AI Decision Matrix ] — strengthen / challenge your TV call")
    lines.append(
        f"Verdict: {verdict} · support_score={reinforcement.get('strength_score')}/100"
    )
    lines.append(str(reinforcement.get("advice") or ""))

    lines.append("")
    lines.append("[ Integrated desk ]")
    if not ai.get("available"):
        lines.append(ai.get("message") or "No recent AI scan yet.")
    else:
        lines.append(
            f"AI: {ai.get('label')} · conf {ai.get('ai_confidence', '—')} · "
            f"quality {ai.get('quality_score', '—')}/100"
        )
        lines.append(
            f"WM={ai.get('macro_bias')} news={ai.get('news_score')} · "
            f"Kronos={ai.get('kronos_bias')} · MiroFish={ai.get('swarm_bias')} · "
            f"Consensus={ai.get('consensus_action')}"
        )
        if ai.get("consensus_reason"):
            lines.append(f"Consensus note: {str(ai.get('consensus_reason'))[:140]}")
        pu = ai.get("providers_used") or {}
        if pu:
            on = [k for k, v in pu.items() if v]
            lines.append(f"Providers: {', '.join(on) or '—'}")

    lines.append("")
    lines.append("[ Gemini Search grounding ]")
    lines.append(decision_matrix_line(grounded))
    if grounded and grounded.get("sources"):
        tops = grounded["sources"][:2]
        for s in tops:
            title = (s.get("title") or "source")[:60]
            lines.append(f"  · {title}")

    live = ai.get("live_price") if isinstance(ai.get("live_price"), dict) else None
    if live and live.get("price"):
        lines.append("")
        lines.append(f"Live: {live.get('price')} ({live.get('source')})")

    lines.append("")
    lines.append("You execute / skip on TradingView. System does not auto-trade this alert.")
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
    side_u = (side or "NEUTRAL").upper()

    ai = maybe_refresh_ai(symbol_n, tf_n)

    grounded: dict[str, Any] | None = None
    if grounding_enabled():
        try:
            grounded = fetch_grounded_market_brief(symbol_n)
        except Exception as exc:
            logger.warning("grounding on TV alert failed: %s", exc)
            grounded = {"enabled": True, "ok": False, "reason": str(exc)}

    reinforcement = strengthen_decision(tv_side=side_u, ai=ai, grounded=grounded)
    text = format_tv_telegram_message(
        indicator_id=indicator_id,
        indicator_name=indicator_name,
        side=side_u,
        symbol=symbol_n,
        timeframe=tf_n,
        entry=entry,
        strength=strength,
        raw_note=raw_note,
        ai=ai,
        grounded=grounded,
        reinforcement=reinforcement,
    )

    if not telegram_configured():
        return {
            "ok": False,
            "reason": "telegram_not_configured",
            "preview": text,
            "ai_snapshot": ai,
            "gemini_grounding": grounded,
            "reinforcement": reinforcement,
        }

    tg = send_telegram(text)
    return {
        "ok": bool(tg.get("ok")),
        "telegram": tg,
        "ai_snapshot": ai,
        "gemini_grounding": grounded,
        "reinforcement": reinforcement,
        "message": text,
        "merged_into_ai": False,
    }
