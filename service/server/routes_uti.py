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
                "XAUUSD",
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
        """TradingView webhook URLs.

        Primary path (recommended): TV indicator → Telegram + current AI status
        (Pine is NOT merged into the AI desk).

        Legacy path: /api/webhooks/pine/{id} still exists but auto-decide is off by default.
        """
        secret = os.getenv("UTI_WEBHOOK_SECRET", "dev-webhook-secret")
        base = (public_base or str(request.base_url)).rstrip("/")
        xf_proto = request.headers.get("x-forwarded-proto")
        xf_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if public_base:
            base = public_base.rstrip("/")
        elif xf_host:
            proto = xf_proto or "https"
            base = f"{proto}://{xf_host}".rstrip("/")

        markets = [
            s.strip()
            for s in os.getenv(
                "UTI_SCAN_SYMBOLS",
                "XAUUSD",
            ).split(",")
            if s.strip()
        ]

        tv_telegram_webhooks = []
        for iid, meta in INDICATORS.items():
            url = f"{base}/api/webhooks/tv-telegram/{iid}?secret={secret}"
            message = (
                '{"indicator_id":"'
                + iid
                + '","symbol":"{{ticker}}","timeframe":"{{interval}}",'
                '"side":"BUY","strength":0.85,"entry":{{close}},'
                '"bar_time":"{{timenow}}","note":"{{strategy.order.comment}}"}'
            )
            tv_telegram_webhooks.append(
                {
                    "indicator_id": iid,
                    "name": meta.get("name") if isinstance(meta, dict) else iid,
                    "webhook_url": url,
                    "alert_message_buy": message,
                    "alert_message_sell": message.replace('"side":"BUY"', '"side":"SELL"'),
                }
            )

        # One shared URL for any custom indicator name in the JSON body
        shared_url = f"{base}/api/webhooks/tv-telegram?secret={secret}"
        shared_message = (
            '{"indicator_id":"my_indicator","symbol":"{{ticker}}","timeframe":"{{interval}}",'
            '"side":"BUY","strength":0.85,"entry":{{close}},"bar_time":"{{timenow}}"}'
        )

        legacy_pine = []
        for iid, meta in INDICATORS.items():
            url = f"{base}/api/webhooks/pine/{iid}?secret={secret}"
            message = (
                '{"indicator_id":"'
                + iid
                + '","symbol":"{{ticker}}","timeframe":"{{interval}}",'
                '"side":"BUY","strength":0.85,"entry":{{close}},"sl":0,"tps":[],'
                '"bar_time":"{{timenow}}"}'
            )
            legacy_pine.append(
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
            "scan_markets": markets,
            "mode": "tv_telegram_separate_from_ai",
            "instructions": [
                "FLOW: TradingView signal → UTI desk strengthens your decision → Telegram",
                "TV indicator is NOT merged into AI votes — it is the trigger; AI is the second opinion.",
                "Desk = WorldMonitor + MiroFish + Kronos + TradingAgents + Gemini Search grounding",
                "1. Create TradingView alert → webhook /api/webhooks/tv-telegram?secret=...",
                "2. Paste JSON alert message (BUY/SELL template)",
                "3. When indicator fires, Telegram gets TV side + STRENGTHENS/CONFLICTS/CAUTION",
                "4. Background scanner keeps AI status fresh for each market",
            ],
            "secret_header_alt": "X-UTI-Secret",
            "tv_telegram": {
                "shared_webhook_url": shared_url,
                "shared_alert_message_buy": shared_message,
                "shared_alert_message_sell": shared_message.replace('"side":"BUY"', '"side":"SELL"'),
                "per_indicator": tv_telegram_webhooks,
            },
            "legacy_pine_confluence_webhooks": legacy_pine,
            "legacy_note": (
                "Legacy /api/webhooks/pine/* stores votes for confluence. "
                "Auto-decide is OFF by default (UTI_AUTO_DECIDE_ON_WEBHOOK=false)."
            ),
            "paper_agent_id": agent_id,
            "paper_cash": cash,
            "live_xauusd": fetch_live_price("XAUUSD"),
        }

    @app.get("/api/uti/indicators")
    async def uti_indicators():
        return {"indicators": list(INDICATORS.values())}

    async def _parse_webhook_body(request: Request) -> Any:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                return await request.json()
            except Exception:
                raw = await request.body()
                return raw.decode("utf-8", errors="replace")
        raw = await request.body()
        return raw.decode("utf-8", errors="replace").strip()

    @app.post("/api/webhooks/tv-telegram")
    @app.post("/api/webhooks/tv-telegram/{indicator_id}")
    async def tv_telegram_webhook(
        request: Request,
        indicator_id: Optional[str] = None,
        secret: Optional[str] = None,
        x_uti_secret: Optional[str] = Header(None, alias="X-UTI-Secret"),
    ):
        """TradingView → Telegram only. Does NOT merge Pine into the AI desk."""
        from confluence.indicators import INDICATORS
        from uti_agents.tv_telegram import notify_tv_alert
        from webhooks.adapters import normalize_payload, parse_text_alert

        provided = secret or x_uti_secret or request.query_params.get("secret")
        if not _webhook_secret_ok(provided):
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

        body = await _parse_webhook_body(request)
        # Resolve indicator id from path or body
        body_dict = body if isinstance(body, dict) else {}
        iid = (
            indicator_id
            or (body_dict.get("indicator_id") if isinstance(body_dict, dict) else None)
            or "tradingview"
        )
        iid = str(iid).strip() or "tradingview"

        # Soft-normalize without requiring known indicator registry
        received_at = utc_now_iso_z()
        try:
            if iid in KNOWN_INDICATOR_IDS:
                vote = normalize_payload(iid, body, received_at=received_at)
            else:
                # Free-form indicator name
                if isinstance(body, str):
                    parsed = parse_text_alert(body)
                    payload = {
                        "side": parsed["side"],
                        "strength": parsed["strength"],
                        "raw_text": body,
                        "symbol": "XAUUSD",
                    }
                else:
                    payload = dict(body_dict)
                # Temporarily map through a known id normalizer by injecting fields
                fake = {
                    **payload,
                    "indicator_id": "triple_confluence",
                }
                vote = normalize_payload("triple_confluence", fake, received_at=received_at)
                vote["indicator_id"] = iid
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        from uti_agents.market_scanner import symbol_in_watchlist, watchlist

        if not symbol_in_watchlist(vote["symbol"]):
            return {
                "success": True,
                "ignored": True,
                "reason": "symbol_not_in_watchlist",
                "symbol": vote["symbol"],
                "watchlist": watchlist(),
                "merged_into_ai": False,
                "path": "tv_telegram_only",
            }

        name = None
        if iid in INDICATORS and isinstance(INDICATORS[iid], dict):
            name = INDICATORS[iid].get("name")

        raw_note = None
        raw = vote.get("raw") if isinstance(vote.get("raw"), dict) else {}
        raw_note = (
            (raw.get("note") if isinstance(raw, dict) else None)
            or (raw.get("raw_text") if isinstance(raw, dict) else None)
            or (body if isinstance(body, str) else None)
        )

        result = notify_tv_alert(
            indicator_id=iid,
            indicator_name=name,
            symbol=vote["symbol"],
            timeframe=vote["timeframe"],
            side=vote["side"],
            entry=vote.get("entry"),
            strength=vote.get("strength"),
            raw_note=str(raw_note) if raw_note else None,
        )
        return {
            "success": True,
            "merged_into_ai": False,
            "path": "tv_telegram_only",
            "indicator_id": iid,
            "symbol": vote["symbol"],
            "side": vote["side"],
            "telegram": result,
        }

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

        body = await _parse_webhook_body(request)

        received_at = utc_now_iso_z()
        try:
            vote = normalize_payload(indicator_id, body, received_at=received_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if store.vote_exists(vote["dedupe_key"]):
            return {"success": True, "deduped": True, "vote": vote}

        saved = store.insert_pine_vote(vote)

        # Default OFF — user checks TV separately; use /api/webhooks/tv-telegram for alerts
        auto_run = os.getenv("UTI_AUTO_DECIDE_ON_WEBHOOK", "false").strip().lower() in {
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
            "merged_into_ai": bool(auto_run),
            "decision_cycle": decision_result,
            "hint": "Prefer /api/webhooks/tv-telegram/{indicator_id} for Telegram + AI status without merging.",
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
