"""Orchestrator: confluence + intel + AI brain + risk → audited decision (+ optional paper)."""

from __future__ import annotations

import logging
import os
from typing import Any

from uti_agents.kronos_bridge import get_kronos_forecast
from uti_agents.live_price import get_paper_agent_cash, resolve_entry_price
from uti_agents.trading_brain import get_trading_brain
from confluence.engine import get_confluence_engine
from decisions import store
from intel.mirofish import get_mirofish_client
from intel.symbols import normalize_symbol, normalize_timeframe
from intel.worldmonitor import get_worldmonitor_client
from risk.engine import get_risk_engine
from routes_shared import utc_now_iso_z

logger = logging.getLogger(__name__)


def _infer_market(symbol: str) -> str:
    if symbol in {"BTCUSD", "ETHUSD"} or symbol.endswith("USDT"):
        return "crypto"
    if symbol in {"XAUUSD"}:
        return "forex"
    return "us-stock"


def maybe_paper_trade(
    *,
    decision: str,
    risk: dict[str, Any],
    symbol: str,
    entry: float | None,
    quantity: float,
) -> dict[str, Any]:
    """Execute paper trade into AI-Trader positions when enabled and approved."""
    enabled = os.getenv("UTI_PAPER_TRADE_ENABLED", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not enabled:
        return {"status": "disabled"}
    if not risk.get("approved"):
        return {"status": "skipped", "reason": "risk_rejected"}
    if decision not in {"BUY", "SELL"}:
        return {"status": "skipped", "reason": "not_actionable"}

    agent_token = os.getenv("UTI_PAPER_AGENT_TOKEN", "").strip()
    agent_id_env = os.getenv("UTI_PAPER_AGENT_ID", "").strip()
    if not agent_token and not agent_id_env:
        return {"status": "skipped", "reason": "no_paper_agent_configured"}

    try:
        from database import get_db_connection
        from services import _update_position_from_signal as update_position

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if agent_id_env:
                agent_id = int(agent_id_env)
            else:
                cursor.execute("SELECT id, cash FROM agents WHERE token = ?", (agent_token,))
                row = cursor.fetchone()
                if not row:
                    return {"status": "error", "reason": "paper_agent_not_found"}
                agent_id = int(row["id"])

            cursor.execute("SELECT cash FROM agents WHERE id = ?", (agent_id,))
            agent_row = cursor.fetchone()
            if not agent_row:
                return {"status": "error", "reason": "paper_agent_missing"}

            price = float(entry or 0)
            qty = float(quantity or 0)
            if price <= 0 or qty <= 0:
                return {"status": "error", "reason": "invalid_price_or_qty"}

            action = "buy" if decision == "BUY" else "short"
            market = _infer_market(symbol)
            # Forex/metals may not be a first-class market in AI-Trader; map to us-stock for paper book.
            if market == "forex":
                market = "us-stock"

            executed_at = utc_now_iso_z()
            update_position(
                agent_id,
                symbol,
                market,
                action,
                qty,
                price,
                executed_at,
                cursor=cursor,
            )
            trade_value = qty * price
            if action == "buy":
                cursor.execute("UPDATE agents SET cash = cash - ? WHERE id = ?", (trade_value, agent_id))
            else:
                # Short proceeds credited in paper book
                cursor.execute("UPDATE agents SET cash = cash + ? WHERE id = ?", (trade_value, agent_id))
            conn.commit()
            return {
                "status": "filled",
                "agent_id": agent_id,
                "market": market,
                "symbol": symbol,
                "action": action,
                "quantity": qty,
                "price": price,
                "executed_at": executed_at,
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("Paper trade failed")
        return {"status": "error", "reason": str(exc)}


def run_decision_cycle(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    votes = store.list_recent_votes(symbol=normalize_symbol(symbol) if symbol else None, limit=200)
    if timeframe:
        tf = normalize_timeframe(timeframe)
        votes = [v for v in votes if normalize_timeframe(v.get("timeframe")) == tf]

    engine = get_confluence_engine()
    confluence = engine.score(votes, symbol=symbol, timeframe=timeframe)

    if not confluence.get("ready") and not force:
        return {
            "status": "waiting",
            "reason": "confluence_not_ready",
            "confluence": confluence,
            "hint": "No Pine confluence yet. Open Command Center → Force Decide to run AI research (WorldMonitor + MiroFish + Kronos + Ollama) without webhooks.",
        }

    # Research mode (no webhooks): force=true still runs the full AI desk on live price.
    symbol_n = normalize_symbol(symbol or confluence.get("symbol") or "XAUUSD")
    if confluence.get("symbol") in {None, "UNKNOWN"}:
        confluence["symbol"] = symbol_n
    if not confluence.get("timeframe"):
        confluence["timeframe"] = normalize_timeframe(timeframe or "30")
    intel = get_worldmonitor_client().fetch_brief(symbol_n)
    swarm = get_mirofish_client().fetch_swarm_brief(symbol_n, confluence=confluence, intel=intel)
    kronos = get_kronos_forecast(symbol_n)
    brain = get_trading_brain().analyze(
        confluence=confluence,
        intel=intel,
        kronos=kronos,
        swarm=swarm,
    )

    decision = str(brain.get("trader") or "WAIT").upper()
    if decision not in {"BUY", "SELL", "WAIT"}:
        decision = "WAIT"

    webhook_entry = confluence.get("entry")
    entry, price_meta = resolve_entry_price(symbol_n, float(webhook_entry) if webhook_entry else None)
    sl = confluence.get("sl")
    tps = list(confluence.get("tps") or [])
    # If SL/TP came with a stale webhook entry, rebuild around live entry
    if entry and webhook_entry and price_meta.get("chosen", "").startswith("live"):
        sl = None
        tps = []
    # Synthesize SL/TP from live entry when Pine didn't send them
    if entry and not sl:
        sl = round(entry * (0.998 if decision == "BUY" else 1.002), 4)
    if entry and not tps:
        if decision == "BUY":
            tps = [round(entry * 1.002, 4), round(entry * 1.004, 4)]
        elif decision == "SELL":
            tps = [round(entry * 0.998, 4), round(entry * 0.996, 4)]

    agent_id, cash = get_paper_agent_cash()
    risk = get_risk_engine().evaluate(
        decision=decision,
        technical_score=float(confluence.get("technical_score") or 50),
        ai_confidence=float(brain.get("ai_confidence") or 0),
        entry=entry,
        sl=sl,
        tps=tps,
        news_score=float(intel.get("news_score") or 0),
        geopolitical_risk=str(intel.get("geopolitical_risk") or "LOW"),
        cash=cash,
    )

    paper = maybe_paper_trade(
        decision=decision,
        risk=risk,
        symbol=symbol_n,
        entry=entry,
        quantity=float(risk.get("quantity") or 0),
    )
    if isinstance(paper, dict):
        paper["cash_before"] = cash
        paper["price_meta"] = price_meta
        if agent_id:
            paper.setdefault("agent_id", agent_id)

    providers_used = brain.get("providers_used") or {}
    providers_used = {
        **providers_used,
        "pine": bool(confluence.get("ready") or (confluence.get("active_votes") or 0) > 0),
        "worldmonitor": bool(intel) and not intel.get("stub"),
        "mirofish": bool(swarm) and not swarm.get("stub"),
        "kronos": bool(kronos) and not kronos.get("disabled"),
        "llm": brain.get("provider") or providers_used.get("llm"),
        "tradingagents": "tradingagents" in str(brain.get("mode") or ""),
        "research_mode": not bool(confluence.get("ready")),
    }

    record = {
        "symbol": symbol_n,
        "timeframe": confluence.get("timeframe"),
        "decision": decision,
        "technical_score": confluence.get("technical_score"),
        "ai_confidence": brain.get("ai_confidence"),
        "news_score": intel.get("news_score"),
        "macro_bias": intel.get("macro_bias"),
        "geopolitical_risk": intel.get("geopolitical_risk"),
        "pine": confluence,
        "analysts": brain.get("analysts") or {},
        "bull_research": brain.get("bull_research"),
        "bear_research": brain.get("bear_research"),
        "trader": decision,
        "risk": risk,
        "entry": entry,
        "sl": sl,
        "tps": tps,
        "quantity": risk.get("quantity"),
        "rr": risk.get("rr"),
        "paper_status": paper.get("status"),
        "paper_trade": paper,
        "brain_mode": brain.get("mode"),
        "intel": intel,
        "swarm": swarm,
        "kronos": kronos,
        "providers_used": providers_used,
        "unified": True,
        "live_price": (price_meta or {}).get("live"),
        "price_meta": price_meta,
        "paper_cash_before": cash,
        "paper_agent_id": agent_id,
    }
    saved = store.insert_decision(record)
    return {
        "status": "ok",
        "unified": True,
        "providers_used": providers_used,
        "decision": saved,
        "brain": brain,
        "intel": intel,
        "swarm": swarm,
        "kronos": kronos,
        "confluence": confluence,
    }
