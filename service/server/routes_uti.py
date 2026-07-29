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
            "paper_starting_cash": os.getenv("UTI_PAPER_STARTING_CASH", "100"),
            "kronos_enabled": os.getenv("KRONOS_ENABLED", "true"),
            "tradingagents_enabled": os.getenv("TRADINGAGENTS_ENABLED", "true"),
            "tradingagents_full_graph": os.getenv("TRADINGAGENTS_FULL_GRAPH", "true"),
            "mirofish_enabled": os.getenv("MIROFISH_ENABLED", "true"),
            "scan_symbols": os.getenv(
                "UTI_SCAN_SYMBOLS",
                "XAUUSD,XAGUSD,NAS100,US30,SPX500,USOIL,EURUSD",
            ),
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

    @app.get("/api/uti/webhooks/setup")
    async def uti_webhook_setup(request: Request, public_base: Optional[str] = None):
        """TradingView Pro webhook URLs + JSON alert bodies for all 5 Pine indicators."""
        secret = os.getenv("UTI_WEBHOOK_SECRET", "dev-webhook-secret")
        base = (public_base or str(request.base_url)).rstrip("/")
        # Prefer X-Forwarded headers when behind ngrok/tunnel
        xf_proto = request.headers.get("x-forwarded-proto")
        xf_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if public_base:
            base = public_base.rstrip("/")
        elif xf_host:
            proto = xf_proto or "https"
            base = f"{proto}://{xf_host}".rstrip("/")

        webhooks = []
        for iid, meta in INDICATORS.items():
            url = f"{base}/api/webhooks/pine/{iid}?secret={secret}"
            message = (
                '{"indicator_id":"'
                + iid
                + '","symbol":"{{ticker}}","timeframe":"{{interval}}",'
                '"side":"BUY","strength":0.85,"entry":{{close}},"sl":0,"tps":[],'
                '"bar_time":"{{timenow}}"}'
            )
            webhooks.append(
                {
                    "indicator_id": iid,
                    "name": meta.get("name") if isinstance(meta, dict) else iid,
                    "webhook_url": url,
                    "alert_message_buy": message,
                    "alert_message_sell": message.replace('"side":"BUY"', '"side":"SELL"'),
                }
            )
        from uti_agents.live_price import fetch_live_price, get_paper_agent_cash

        agent_id, cash = get_paper_agent_cash()
        return {
            "tradingview_pro_required": True,
            "instructions": [
                "1. Expose this API with ngrok: ngrok http 8000",
                "2. Open GET /api/uti/webhooks/setup?public_base=https://YOUR_NGROK_HOST",
                "3. On TradingView Pro: create ONE alert per Pine script (5 total)",
                "4. Paste webhook_url + alert_message into each alert",
                "5. When >=3 indicators agree, the unified agent desk auto-decides and paper-trades",
            ],
            "secret_header_alt": "X-UTI-Secret",
            "webhooks": webhooks,
            "paper_agent_id": agent_id,
            "paper_cash": cash,
            "live_xauusd": fetch_live_price("XAUUSD"),
            "mode": "paper" if os.getenv("UTI_PAPER_ONLY", "true").lower() in {"1", "true", "yes", "on"} else "live",
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
    async def uti_command_center(symbol: Optional[str] = "XAUUSD", timeframe: Optional[str] = "30"):
        from intel.worldmonitor import get_worldmonitor_client
        from uti_agents.live_price import fetch_live_price, get_paper_agent_cash

        symbol_n = normalize_symbol(symbol or "XAUUSD")
        tf_n = normalize_timeframe(timeframe or "30")
        votes = store.list_recent_votes(symbol=symbol_n, timeframe=tf_n, limit=200)
        confluence = get_confluence_engine().score(votes, symbol=symbol_n, timeframe=tf_n)
        decisions = store.list_decisions(limit=20, symbol=symbol_n)
        latest = decisions[0] if decisions else None
        agent_id, cash = get_paper_agent_cash()
        intel = get_worldmonitor_client().fetch_brief(symbol_n)
        live = fetch_live_price(symbol_n)
        return {
            "symbol": symbol_n,
            "timeframe": tf_n,
            "confluence": confluence,
            "latest_decision": latest,
            "decisions": decisions,
            "settings": store.get_settings(),
            "worldmonitor": intel,
            "live_price": live,
            "paper": {
                "agent_id": agent_id,
                "cash": cash,
                "starting_cash": float(os.getenv("UTI_PAPER_STARTING_CASH", "100")),
                "paper_only": os.getenv("UTI_PAPER_ONLY", "true"),
            },
            "webhook_setup": "/api/uti/webhooks/setup",
        }

    @app.get("/api/uti/settings")
    async def uti_get_settings():
        stored = store.get_settings()
        return {
            "stored": stored,
            "env": {
                "llm_provider": os.getenv("UTI_LLM_PROVIDER", "ollama"),
                "deep_model": os.getenv("UTI_DEEP_MODEL", "llama3.2:1b"),
                "quick_model": os.getenv("UTI_QUICK_MODEL", "llama3.2:1b"),
                "max_debate_rounds": os.getenv("UTI_MAX_DEBATE_ROUNDS", "2"),
                "paper_trade_enabled": os.getenv("UTI_PAPER_TRADE_ENABLED", "true"),
                "kill_switch": os.getenv("UTI_KILL_SWITCH", "false"),
                "confluence_min_votes": os.getenv("CONFLUENCE_MIN_VOTES", "3"),
                "confluence_ttl_seconds": os.getenv("CONFLUENCE_TTL_SECONDS", "900"),
                "tradingagents_enabled": os.getenv("TRADINGAGENTS_ENABLED", "true"),
                "tradingagents_full_graph": os.getenv("TRADINGAGENTS_FULL_GRAPH", "false"),
                "kronos_enabled": os.getenv("KRONOS_ENABLED", "true"),
                "mirofish_enabled": os.getenv("MIROFISH_ENABLED", "true"),
                "mirofish_api_base_url": os.getenv("MIROFISH_API_BASE_URL", "http://127.0.0.1:5001"),
                "worldmonitor_configured": bool(os.getenv("WORLDMONITOR_API_KEY", "").strip()),
                "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
                "unified_pipeline": True,
            },
        }

    @app.get("/api/uti/providers")
    async def uti_providers(sample: bool = False):
        """Health of every provider that feeds the unified decision.

        Default is lightweight (no Kronos/news sample) so Render health stays snappy.
        Pass ?sample=true for a full live probe.
        """
        from intel.llm import ollama_health, provider_status, resolve_llm_provider
        from intel.mirofish import get_mirofish_client
        from intel.worldmonitor import get_worldmonitor_client

        wm = get_worldmonitor_client()
        mf = get_mirofish_client()
        out = {
            "unified": True,
            "llm": provider_status(),
            "ollama": ollama_health(),
            "worldmonitor": {
                "configured": wm.configured,
                "has_api_key": bool(wm.api_key),
            },
            "mirofish": mf.health(),
            "kronos": {
                "enabled": os.getenv("KRONOS_ENABLED", "true"),
                "model": os.getenv("KRONOS_MODEL", "NeoQuasar/Kronos-mini"),
            },
            "tradingagents": {
                "enabled": os.getenv("TRADINGAGENTS_ENABLED", "true"),
                "full_graph": os.getenv("TRADINGAGENTS_FULL_GRAPH", "true"),
                "provider": resolve_llm_provider(),
                "prefer_compact_on_groq": os.getenv("UTI_TA_PREFER_COMPACT", "true"),
            },
        }
        if sample:
            from intel.free_market import fetch_free_price
            from intel.free_news import enrich_news
            from uti_agents.kronos_bridge import get_kronos_forecast

            out["free_market"] = {
                "XAUUSD": fetch_free_price("XAUUSD"),
                "EURUSD": fetch_free_price("EURUSD"),
            }
            out["free_news"] = enrich_news("XAUUSD")
            out["worldmonitor"]["sample"] = wm.fetch_brief("XAUUSD")
            out["kronos"] = get_kronos_forecast("XAUUSD")
        return out

    @app.get("/api/uti/scan/status")
    async def uti_scan_status():
        from uti_agents.market_scanner import scanner_status
        from uti_agents.telegram_notify import telegram_configured

        return {**scanner_status(), "telegram_ready": telegram_configured()}

    @app.post("/api/uti/scan/run")
    async def uti_scan_run(
        symbols: Optional[str] = None,
        timeframe: Optional[str] = None,
        notify: bool = True,
    ):
        """One-shot scan. Notify-only on good setups (no auto paper fill)."""
        from uti_agents.market_scanner import scan_markets

        syms = [s.strip().upper() for s in (symbols or "").split(",") if s.strip()] or None
        return scan_markets(
            symbols=syms,
            timeframe=timeframe or "30",
            notify=notify,
            paper=False,
        )

    @app.post("/api/uti/scan/start")
    async def uti_scan_start():
        from uti_agents.market_scanner import start_scanner_background

        return start_scanner_background()

    @app.post("/api/uti/scan/stop")
    async def uti_scan_stop():
        from uti_agents.market_scanner import stop_scanner_background

        return stop_scanner_background()

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
