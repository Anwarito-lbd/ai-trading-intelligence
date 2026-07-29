"""Unified Trading Intelligence API routes (webhooks, confluence, decisions, settings)."""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from confluence.engine import get_confluence_engine
from confluence.indicators import INDICATORS, KNOWN_INDICATOR_IDS
from decisions import store
from decisions.orchestrator import run_decision_cycle
from intel.symbols import normalize_symbol, normalize_timeframe
from routes_shared import utc_now_iso_z
from webhooks.adapters import normalize_payload


def _webhook_secret_ok(provided: str | None) -> bool:
    expected = os.getenv("UTI_WEBHOOK_SECRET", "dev-webhook-secret").strip()
    if not expected:
        return True
    return bool(provided) and provided.strip() == expected


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    deep_model: Optional[str] = None
    quick_model: Optional[str] = None
    max_debate_rounds: Optional[int] = None
    paper_trade_enabled: Optional[bool] = None
    kill_switch: Optional[bool] = None
    confluence_min_votes: Optional[int] = None
    confluence_ttl_seconds: Optional[int] = None


class DecisionRunRequest(BaseModel):
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    force: bool = False


def register_uti_routes(app: FastAPI) -> None:
    @app.get("/api/uti/health")
    async def uti_health():
        from intel.mirofish import get_mirofish_client

        miro = get_mirofish_client().health()
        return {
            "status": "ok",
            "indicators": list(KNOWN_INDICATOR_IDS),
            "paper_only": os.getenv("UTI_PAPER_ONLY", "true"),
            "kronos_enabled": os.getenv("KRONOS_ENABLED", "false"),
            "tradingagents_enabled": os.getenv("TRADINGAGENTS_ENABLED", "false"),
            "mirofish_enabled": os.getenv("MIROFISH_ENABLED", "false"),
            "mirofish": miro,
            "packages": {
                "tradingagents": os.path.isdir(
                    os.path.join(os.path.dirname(__file__), "..", "..", "packages", "tradingagents")
                ),
                "mirofish": os.path.isdir(
                    os.path.join(os.path.dirname(__file__), "..", "..", "packages", "mirofish")
                ),
                "kronos": os.path.isdir(
                    os.path.join(os.path.dirname(__file__), "..", "..", "packages", "kronos")
                ),
            },
        }

    @app.get("/api/uti/indicators")
    async def uti_indicators():
        return {"indicators": list(INDICATORS.values())}

    @app.post("/api/webhooks/pine/{indicator_id}")
    async def pine_webhook(
        indicator_id: str,
        request: Request,
        secret: Optional[str] = None,
        x_uti_secret: Optional[str] = Header(None, alias="X-UTI-Secret"),
    ):
        if indicator_id not in KNOWN_INDICATOR_IDS:
            raise HTTPException(status_code=404, detail=f"Unknown indicator_id: {indicator_id}")
        provided = secret or x_uti_secret or request.query_params.get("secret")
        if not _webhook_secret_ok(provided):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                body: Any = await request.json()
            except Exception:
                body = await request.body()
                body = body.decode("utf-8", errors="replace")
        else:
            raw = await request.body()
            text = raw.decode("utf-8", errors="replace").strip()
            body = text

        received_at = utc_now_iso_z()
        try:
            vote = normalize_payload(indicator_id, body, received_at=received_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if store.vote_exists(vote["dedupe_key"]):
            return {"success": True, "deduped": True, "vote": vote}

        saved = store.insert_pine_vote(vote)

        auto_run = os.getenv("UTI_AUTO_DECIDE_ON_WEBHOOK", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        decision_result = None
        if auto_run:
            decision_result = run_decision_cycle(
                symbol=vote["symbol"],
                timeframe=vote["timeframe"],
                force=False,
            )

        return {
            "success": True,
            "deduped": False,
            "vote": saved,
            "decision_cycle": decision_result,
        }

    @app.get("/api/uti/votes")
    async def uti_votes(symbol: Optional[str] = None, timeframe: Optional[str] = None, limit: int = 100):
        symbol_n = normalize_symbol(symbol) if symbol else None
        tf_n = normalize_timeframe(timeframe) if timeframe else None
        return {"votes": store.list_recent_votes(symbol=symbol_n, timeframe=tf_n, limit=min(limit, 500))}

    @app.get("/api/uti/confluence")
    async def uti_confluence(symbol: Optional[str] = None, timeframe: Optional[str] = None):
        votes = store.list_recent_votes(
            symbol=normalize_symbol(symbol) if symbol else None,
            timeframe=normalize_timeframe(timeframe) if timeframe else None,
            limit=200,
        )
        return get_confluence_engine().score(votes, symbol=symbol, timeframe=timeframe)

    @app.post("/api/uti/decisions/run")
    async def uti_run_decision(data: DecisionRunRequest):
        return run_decision_cycle(symbol=data.symbol, timeframe=data.timeframe, force=data.force)

    @app.get("/api/uti/decisions")
    async def uti_list_decisions(symbol: Optional[str] = None, limit: int = 50):
        return {
            "decisions": store.list_decisions(
                limit=min(limit, 200),
                symbol=normalize_symbol(symbol) if symbol else None,
            )
        }

    @app.get("/api/uti/decisions/{trade_number}")
    async def uti_get_decision(trade_number: int):
        row = store.get_decision(trade_number)
        if not row:
            raise HTTPException(status_code=404, detail="Decision not found")
        return row

    @app.get("/api/uti/command-center")
    async def uti_command_center(symbol: Optional[str] = "XAUUSD", timeframe: Optional[str] = "15"):
        symbol_n = normalize_symbol(symbol or "XAUUSD")
        tf_n = normalize_timeframe(timeframe or "15")
        votes = store.list_recent_votes(symbol=symbol_n, timeframe=tf_n, limit=200)
        confluence = get_confluence_engine().score(votes, symbol=symbol_n, timeframe=tf_n)
        decisions = store.list_decisions(limit=20, symbol=symbol_n)
        latest = decisions[0] if decisions else None
        return {
            "symbol": symbol_n,
            "timeframe": tf_n,
            "confluence": confluence,
            "latest_decision": latest,
            "decisions": decisions,
            "settings": store.get_settings(),
        }

    @app.get("/api/uti/settings")
    async def uti_get_settings():
        stored = store.get_settings()
        return {
            "stored": stored,
            "env": {
                "llm_provider": os.getenv("UTI_LLM_PROVIDER", "heuristic"),
                "deep_model": os.getenv("UTI_DEEP_MODEL", "gpt-5.5"),
                "quick_model": os.getenv("UTI_QUICK_MODEL", "gpt-5.4-mini"),
                "max_debate_rounds": os.getenv("UTI_MAX_DEBATE_ROUNDS", "2"),
                "paper_trade_enabled": os.getenv("UTI_PAPER_TRADE_ENABLED", "true"),
                "kill_switch": os.getenv("UTI_KILL_SWITCH", "false"),
                "confluence_min_votes": os.getenv("CONFLUENCE_MIN_VOTES", "3"),
                "confluence_ttl_seconds": os.getenv("CONFLUENCE_TTL_SECONDS", "900"),
                "tradingagents_enabled": os.getenv("TRADINGAGENTS_ENABLED", "false"),
                "kronos_enabled": os.getenv("KRONOS_ENABLED", "false"),
                "mirofish_enabled": os.getenv("MIROFISH_ENABLED", "false"),
                "mirofish_api_base_url": os.getenv("MIROFISH_API_BASE_URL", "http://127.0.0.1:5001"),
                "worldmonitor_configured": bool(os.getenv("WORLDMONITOR_API_KEY", "").strip()),
            },
        }

    @app.put("/api/uti/settings")
    async def uti_put_settings(data: SettingsUpdate):
        updates: dict[str, str] = {}
        payload = data.model_dump(exclude_none=True)
        for key, value in payload.items():
            updates[key] = str(value).lower() if isinstance(value, bool) else str(value)
            env_map = {
                "llm_provider": "UTI_LLM_PROVIDER",
                "deep_model": "UTI_DEEP_MODEL",
                "quick_model": "UTI_QUICK_MODEL",
                "max_debate_rounds": "UTI_MAX_DEBATE_ROUNDS",
                "paper_trade_enabled": "UTI_PAPER_TRADE_ENABLED",
                "kill_switch": "UTI_KILL_SWITCH",
                "confluence_min_votes": "CONFLUENCE_MIN_VOTES",
                "confluence_ttl_seconds": "CONFLUENCE_TTL_SECONDS",
            }
            env_key = env_map.get(key)
            if env_key:
                os.environ[env_key] = updates[key]
        stored = store.set_settings(updates)
        return {"stored": stored}
