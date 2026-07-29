"""Unified multi-agent trading brain.

Always combines WorldMonitor intel + MiroFish swarm + Kronos into one decision.
Runs TradingAgents LangGraph when TRADINGAGENTS_ENABLED + TRADINGAGENTS_FULL_GRAPH.

LLM refinement (final trader vote) supports:
  UTI_LLM_PROVIDER=ollama|groq|openai|openrouter|openai_compatible
Ollama uses OLLAMA_BASE_URL (default http://127.0.0.1:11434/v1) — no key needed.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from intel.llm import (
    chat_completion,
    llm_endpoint,
    resolve_deep_model,
    resolve_llm_provider,
    resolve_quick_model,
)
from uti_agents.consensus import apply_consensus, build_consensus
from uti_agents.pine_analyst import build_pine_technical_report
from uti_agents.ticker_map import to_yfinance

logger = logging.getLogger(__name__)

_PACKAGES_ROOT = Path(__file__).resolve().parents[3] / "packages" / "tradingagents"
_LLM_REFINE_PROVIDERS = {
    "groq",
    "gemini",
    "cerebras",
    "huggingface",
    "ollama",
    "openai",
    "openrouter",
    "openai_compatible",
}
_ta_graph = None
_ta_graph_key: str | None = None
_ta_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _parse_trader_text(text: str) -> str:
    upper = (text or "").upper()
    first = upper.split("\n")[0] if upper else ""
    if "WAIT" in first and "BUY" not in first and "SELL" not in first:
        return "WAIT"
    if re.search(r"\bSELL\b|\bSHORT\b", first):
        return "SELL"
    if re.search(r"\bBUY\b|\bLONG\b", first):
        return "BUY"
    if re.search(r"\bSELL\b|\bSHORT\b", upper):
        return "SELL"
    if re.search(r"\bBUY\b|\bLONG\b", upper):
        return "BUY"
    if re.search(r"\bWAIT\b|\bHOLD\b|\bFLAT\b", upper):
        return "WAIT"
    return "WAIT"


class TradingBrain:
    def __init__(self) -> None:
        self.use_tradingagents = os.getenv("TRADINGAGENTS_ENABLED", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.llm_provider = resolve_llm_provider()
        self.deep_model = resolve_deep_model(self.llm_provider)
        self.quick_model = resolve_quick_model(self.llm_provider)
        self.max_debate_rounds = int(os.getenv("UTI_MAX_DEBATE_ROUNDS", "2"))

    def analyze(
        self,
        *,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None = None,
        swarm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pine_report = build_pine_technical_report(confluence)

        # Prefer full TradingAgents graph when enabled + reachable LLM
        result: dict[str, Any] | None = None
        if self.use_tradingagents and self.llm_provider in _LLM_REFINE_PROVIDERS:
            result = self._try_tradingagents(confluence, intel, kronos, swarm, pine_report)
            if result is None:
                logger.warning("TradingAgents unavailable; using unified heuristic+LLM desk")

        if result is None:
            result = self._heuristic(confluence, intel, kronos, pine_report, swarm)
            if self.llm_provider in _LLM_REFINE_PROVIDERS:
                refined = self._refine_with_llm(result, confluence, intel, swarm, kronos)
                if refined is not None:
                    result = refined
        return self._gate_with_consensus(result, confluence, intel, swarm, kronos)

    def _gate_with_consensus(
        self,
        result: dict[str, Any],
        confluence: dict[str, Any],
        intel: dict[str, Any],
        swarm: dict[str, Any] | None,
        kronos: dict[str, Any] | None,
    ) -> dict[str, Any]:
        consensus = build_consensus(
            confluence=confluence, intel=intel, swarm=swarm, kronos=kronos
        )
        before = str(result.get("trader") or "WAIT").upper()
        after, why = apply_consensus(before, consensus)
        out = dict(result)
        out["trader"] = after
        out["consensus"] = consensus
        out["consensus_override"] = before != after
        out["consensus_reason"] = why
        if before != after:
            out["notes"] = f"{out.get('notes') or ''} | gated {before}→{after}: {why}".strip(" |")
            # Lower confidence when we force WAIT on conflict
            if after == "WAIT":
                out["ai_confidence"] = min(float(out.get("ai_confidence") or 50), 48.0)
        out.setdefault("providers_used", {})
        out["providers_used"]["pine"] = bool(confluence.get("ready") or (confluence.get("active_votes") or 0) > 0)
        return out

    def _refine_with_llm(
        self,
        heuristic: dict[str, Any],
        confluence: dict[str, Any],
        intel: dict[str, Any],
        swarm: dict[str, Any] | None,
        kronos: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if llm_endpoint(self.llm_provider) is None:
            logger.info("LLM provider %s not reachable; keeping heuristic", self.llm_provider)
            return None
        prompt = (
            f"Symbol: {confluence.get('symbol')} TF: {confluence.get('timeframe')}\n"
            f"Pine technical: {confluence.get('technical_score')}/100 dir={confluence.get('direction')} "
            f"ready={confluence.get('ready')} (MISSING pine = ignore technical edge)\n"
            f"WorldMonitor: news={intel.get('news_score')} macro={intel.get('macro_bias')} "
            f"geo={intel.get('geopolitical_risk')} source={intel.get('source')}\n"
            f"MiroFish swarm: bias={(swarm or {}).get('bias')} score={(swarm or {}).get('score')}\n"
            f"Kronos: bias={(kronos or {}).get('bias')} score={(kronos or {}).get('score')} "
            f"chg%={(kronos or {}).get('change_pct')}\n"
            f"Desk heuristic: trader={heuristic.get('trader')} "
            f"bull={heuristic.get('bull_research')} bear={heuristic.get('bear_research')}\n"
            "RULES:\n"
            "1) If WorldMonitor/macro/news is BULLISH and Kronos is BEARISH (or reverse), choose WAIT.\n"
            "2) Do not SELL when macro is BULLISH unless Pine confluence is ready SELL.\n"
            "3) Do not BUY when macro is BEARISH unless Pine confluence is ready BUY.\n"
            "4) Prefer WAIT over a weak trade.\n"
            "Return exactly one line: DECISION=<BUY|SELL|WAIT>; CONFIDENCE=<0-100>; REASON=<short>"
        )
        content = chat_completion(
            provider=self.llm_provider,
            model=self.quick_model,
            temperature=0.1,
            max_tokens=120,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the final trader on a unified desk. "
                        "Conflicts between WorldMonitor and Kronos must become WAIT, not a forced trade."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        if not content:
            return None
        decision = _parse_trader_text(content)
        conf_match = re.search(r"CONFIDENCE\s*=\s*(\d{1,3})", content or "", re.I)
        confidence = float(conf_match.group(1)) if conf_match else float(heuristic.get("ai_confidence") or 60)
        confidence = _clamp(confidence)
        out = dict(heuristic)
        out["mode"] = f"unified+{self.llm_provider}"
        out["trader"] = decision
        out["ai_confidence"] = confidence
        out["llm_raw"] = content
        out["llm_model"] = self.quick_model
        out["notes"] = (
            f"Unified desk (Pine+WM+MiroFish+Kronos) + {self.llm_provider}/{self.quick_model}"
        )
        out["providers_used"] = {
            "pine": bool(confluence.get("ready") or (confluence.get("active_votes") or 0) > 0),
            "worldmonitor": True,
            "mirofish": True,
            "kronos": bool(kronos and not kronos.get("disabled")),
            "llm": self.llm_provider,
            "tradingagents": False,
        }
        return out

    def _ta_analysts(self) -> tuple[str, ...]:
        raw = os.getenv("UTI_TA_ANALYSTS", "market,news").strip()
        parts = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
        allowed = {"market", "social", "news", "fundamentals"}
        selected = tuple(p for p in parts if p in allowed)
        return selected or ("market", "news")

    def _get_tradingagents_graph(self):
        """Reuse one LangGraph instance (LLM clients are expensive to rebuild)."""
        global _ta_graph, _ta_graph_key
        if _PACKAGES_ROOT.exists() and str(_PACKAGES_ROOT) not in sys.path:
            sys.path.append(str(_PACKAGES_ROOT))
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        provider = self.llm_provider if self.llm_provider != "heuristic" else "ollama"
        analysts = self._ta_analysts()
        key = f"{provider}|{self.deep_model}|{self.quick_model}|{self.max_debate_rounds}|{','.join(analysts)}"
        if _ta_graph is not None and _ta_graph_key == key:
            return _ta_graph, provider

        config = DEFAULT_CONFIG.copy()
        config["llm_provider"] = provider
        # Groq free TPM is tight — keep both roles on the quick model by default
        deep = self.deep_model
        if provider == "groq" and os.getenv("UTI_TA_GROQ_DEEP_AS_QUICK", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            deep = self.quick_model
        config["deep_think_llm"] = deep
        config["quick_think_llm"] = self.quick_model
        config["max_debate_rounds"] = max(1, min(self.max_debate_rounds, 2))
        config["max_risk_discuss_rounds"] = 1
        config["max_recur_limit"] = int(os.getenv("UTI_TA_MAX_RECUR", "20"))
        config["news_article_limit"] = int(os.getenv("UTI_TA_NEWS_LIMIT", "3"))
        config["global_news_article_limit"] = int(os.getenv("UTI_TA_GLOBAL_NEWS_LIMIT", "2"))
        config["global_news_lookback_days"] = int(os.getenv("UTI_TA_NEWS_LOOKBACK_DAYS", "2"))
        # Futures/FX: stick to yfinance. Leave macro on fred only if key present;
        # otherwise omit so tool routing does not claim a broken yfinance macro vendor.
        vendors = {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        }
        if os.getenv("FRED_API_KEY", "").strip():
            vendors["macro_data"] = "fred"
        config["data_vendors"] = {**config.get("data_vendors", {}), **vendors}
        if provider == "ollama":
            base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
            if not base.endswith("/v1"):
                base = base + "/v1"
            config["backend_url"] = base
        elif provider == "groq":
            config["backend_url"] = "https://api.groq.com/openai/v1"
            if not os.getenv("GROQ_API_KEY", "").strip():
                raise RuntimeError("GROQ_API_KEY missing for TradingAgents")
        elif provider == "gemini":
            config["backend_url"] = (
                "https://generativelanguage.googleapis.com/v1beta/openai"
            )
            # TradingAgents groq/openai clients read GROQ/OPENAI keys — map Gemini key
            gem = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
            if gem and not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = gem
            config["llm_provider"] = "openai"  # OpenAI-compatible client
            provider = "gemini"
        elif provider == "cerebras":
            config["backend_url"] = "https://api.cerebras.ai/v1"
            if os.getenv("CEREBRAS_API_KEY") and not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = os.environ["CEREBRAS_API_KEY"]
            config["llm_provider"] = "openai"
        elif provider == "openrouter":
            config["backend_url"] = "https://openrouter.ai/api/v1"
        elif provider == "huggingface":
            config["backend_url"] = os.getenv(
                "UTI_HF_BASE_URL", "https://router.huggingface.co/v1"
            ).rstrip("/")
            tok = (
                os.getenv("HF_TOKEN")
                or os.getenv("HUGGINGFACE_HUB_TOKEN")
                or os.getenv("HUGGINGFACE_API_KEY")
                or ""
            )
            if tok and not os.getenv("OPENAI_API_KEY"):
                os.environ["OPENAI_API_KEY"] = tok
            config["llm_provider"] = "openai"

        _ta_graph = TradingAgentsGraph(
            selected_analysts=analysts,
            debug=False,
            config=config,
        )
        _ta_graph_key = key
        logger.info(
            "TradingAgents graph ready provider=%s analysts=%s models=%s/%s",
            provider,
            analysts,
            self.deep_model,
            self.quick_model,
        )
        return _ta_graph, provider

    def _pack_ta_result(
        self,
        *,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        swarm: dict[str, Any] | None,
        pine_report: dict[str, Any],
        provider: str,
        trader: str,
        decision: Any,
        mode: str,
        notes: str,
        analysts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = self._heuristic(confluence, intel, kronos, pine_report, swarm)
        if analysts:
            base["analysts"] = {**(base.get("analysts") or {}), **analysts}
        base.update(
            {
                "mode": mode,
                "provider": provider,
                "models": {"deep": self.deep_model, "quick": self.quick_model},
                "trader": trader,
                "ai_confidence": _clamp(float(base.get("ai_confidence") or 70) + 5),
                "raw_decision": decision,
                "pine_report": pine_report,
                "notes": notes,
                "providers_used": {
                    "pine": bool(confluence.get("ready")),
                    "worldmonitor": True,
                    "mirofish": True,
                    "kronos": bool(kronos and not kronos.get("disabled")),
                    "llm": provider,
                    "tradingagents": True,
                },
            }
        )
        return base

    def _tradingagents_compact_desk(
        self,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        swarm: dict[str, Any] | None,
        pine_report: dict[str, Any],
        ticker: str,
    ) -> dict[str, Any] | None:
        """TradingAgents-style multi-agent desk with tiny prompts (Groq-safe).

        Uses the same role cast as TradingAgents (market/news/bull/bear/trader)
        but feeds short WM/MiroFish/Kronos context instead of huge tool dumps.
        """
        if llm_endpoint(self.llm_provider) is None:
            return None
        headlines = list(intel.get("headlines") or [])[:4]
        prompt = (
            f"TradingAgents desk for {confluence.get('symbol')} ({ticker}) "
            f"TF={confluence.get('timeframe')}.\n"
            f"Market brief: last_price_move related news_score={intel.get('news_score')} "
            f"macro={intel.get('macro_bias')} geo={intel.get('geopolitical_risk')}\n"
            f"Headlines: {' | '.join(headlines) if headlines else 'n/a'}\n"
            f"MiroFish swarm: {(swarm or {}).get('bias')} score={(swarm or {}).get('score')}\n"
            f"Kronos forecast: {(kronos or {}).get('bias')} chg%={(kronos or {}).get('change_pct')} "
            f"source={(kronos or {}).get('source')}\n"
            f"Pine ready={confluence.get('ready')} (ignore if false).\n"
            "Roles to answer in order, one short line each:\n"
            "MARKET: <BULLISH|BEARISH|NEUTRAL> <reason>\n"
            "NEWS: <BULLISH|BEARISH|NEUTRAL> <reason>\n"
            "BULL: <one sentence>\n"
            "BEAR: <one sentence>\n"
            "TRADER: DECISION=<BUY|SELL|WAIT>; CONFIDENCE=<0-100>; REASON=<short>\n"
            "If WorldMonitor and Kronos conflict, TRADER must WAIT."
        )
        content = chat_completion(
            provider=self.llm_provider,
            model=self.quick_model,
            temperature=0.1,
            max_tokens=280,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the TradingAgents multi-agent trading desk "
                        "(market, news, bull, bear, trader). Be concise."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        if not content:
            return None
        trader = _parse_trader_text(content)
        conf_match = re.search(r"CONFIDENCE\s*=\s*(\d{1,3})", content or "", re.I)
        confidence = float(conf_match.group(1)) if conf_match else 68.0
        analysts = {
            "market": next((ln for ln in content.splitlines() if ln.upper().startswith("MARKET:")), ""),
            "news": next((ln for ln in content.splitlines() if ln.upper().startswith("NEWS:")), ""),
            "bull": next((ln for ln in content.splitlines() if ln.upper().startswith("BULL:")), ""),
            "bear": next((ln for ln in content.splitlines() if ln.upper().startswith("BEAR:")), ""),
        }
        out = self._pack_ta_result(
            confluence=confluence,
            intel=intel,
            kronos=kronos,
            swarm=swarm,
            pine_report=pine_report,
            provider=self.llm_provider,
            trader=trader,
            decision=content,
            mode=f"tradingagents+compact+{self.llm_provider}",
            notes=f"TradingAgents compact desk ({ticker}) via {self.llm_provider}/{self.quick_model}",
            analysts=analysts,
        )
        out["ai_confidence"] = _clamp(confidence)
        out["tradingagents_engine"] = "compact"
        return out

    def _try_tradingagents(
        self,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        swarm: dict[str, Any] | None,
        pine_report: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run TradingAgents (LangGraph when possible, compact desk otherwise)."""
        if os.getenv("TRADINGAGENTS_FULL_GRAPH", "true").strip().lower() not in {
            "1", "true", "yes", "on"
        }:
            return None

        symbol = str(confluence.get("symbol") or "SPY")
        ticker = to_yfinance(symbol)
        from datetime import datetime, timezone

        trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_ttl = float(os.getenv("UTI_TA_CACHE_SECONDS", "1200"))  # 20m
        cache_key = f"{ticker}:{trade_date}:{','.join(self._ta_analysts())}"
        cached = _ta_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < cache_ttl:
            return self._pack_ta_result(
                confluence=confluence,
                intel=intel,
                kronos=kronos,
                swarm=swarm,
                pine_report=pine_report,
                provider=str(cached[1].get("provider") or self.llm_provider),
                trader=str(cached[1].get("trader") or "WAIT"),
                decision=cached[1].get("raw_decision"),
                mode=f"tradingagents+cached+{cached[1].get('provider') or self.llm_provider}",
                notes=f"TradingAgents cached({ticker}) + WM + MiroFish + Kronos",
            )

        prefer_compact = os.getenv("UTI_TA_PREFER_COMPACT", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }
        # Groq free tier cannot swallow LangGraph tool dumps (~90k tokens). Prefer compact.
        if prefer_compact and self.llm_provider == "groq":
            compact = self._tradingagents_compact_desk(
                confluence, intel, kronos, swarm, pine_report, ticker
            )
            if compact is not None:
                _ta_cache[cache_key] = (
                    time.time(),
                    {
                        "raw_decision": compact.get("raw_decision"),
                        "trader": compact.get("trader"),
                        "provider": self.llm_provider,
                    },
                )
                return compact

        try:
            graph, provider = self._get_tradingagents_graph()
            _, decision = graph.propagate(ticker, trade_date)
            trader = _parse_trader_text(str(decision))
            packed = self._pack_ta_result(
                confluence=confluence,
                intel=intel,
                kronos=kronos,
                swarm=swarm,
                pine_report=pine_report,
                provider=provider,
                trader=trader,
                decision=decision,
                mode=f"tradingagents+langgraph+{provider}",
                notes=(
                    f"TradingAgents LangGraph({ticker}) + WorldMonitor + MiroFish + Kronos "
                    f"via {provider}/{self.quick_model}"
                ),
            )
            packed["tradingagents_engine"] = "langgraph"
            _ta_cache[cache_key] = (
                time.time(),
                {"raw_decision": decision, "trader": trader, "provider": provider},
            )
            return packed
        except Exception as exc:
            logger.warning("TradingAgents LangGraph failed: %s — trying compact desk", exc)
            compact = self._tradingagents_compact_desk(
                confluence, intel, kronos, swarm, pine_report, ticker
            )
            if compact is not None:
                _ta_cache[cache_key] = (
                    time.time(),
                    {
                        "raw_decision": compact.get("raw_decision"),
                        "trader": compact.get("trader"),
                        "provider": self.llm_provider,
                    },
                )
            return compact

    def _heuristic(
        self,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        pine_report: dict[str, Any],
        swarm: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        tech = float(confluence.get("technical_score") or 50)
        news = float(intel.get("news_score") or 0.0)
        macro = str(intel.get("macro_bias") or "NEUTRAL").upper()
        geo = str(intel.get("geopolitical_risk") or "LOW").upper()

        news_score = _clamp(50 + news * 40)
        sentiment_score = _clamp(50 + news * 35)
        macro_score = 70.0 if macro == "BULLISH" else 30.0 if macro == "BEARISH" else 50.0
        if geo in {"HIGH", "SEVERE", "CRITICAL"}:
            macro_score = _clamp(macro_score - 15)

        kronos_bias = "NEUTRAL"
        kronos_score = 50.0
        if kronos and not kronos.get("disabled"):
            kronos_bias = str(kronos.get("bias") or "NEUTRAL").upper()
            kronos_score = float(kronos.get("score") or 50)

        swarm_bias = str((swarm or {}).get("bias") or "NEUTRAL").upper()
        swarm_score = float((swarm or {}).get("score") or 50.0)

        bull = (
            0.40 * tech
            + 0.15 * news_score
            + 0.10 * sentiment_score
            + 0.10 * macro_score
            + 0.10 * kronos_score
            + 0.15 * swarm_score
        )
        bear = 100 - bull
        for _ in range(max(1, self.max_debate_rounds)):
            if tech >= 60:
                bull += 1.5
                bear -= 1.0
            if tech <= 40:
                bear += 1.5
                bull -= 1.0
            if news > 0.2:
                bull += 1.0
            if news < -0.2:
                bear += 1.0
            if swarm_bias == "BULLISH":
                bull += 1.0
            if swarm_bias == "BEARISH":
                bear += 1.0
            if kronos_bias == "BULLISH":
                bull += 0.8
            if kronos_bias == "BEARISH":
                bear += 0.8
        bull = _clamp(bull)
        bear = _clamp(bear)

        if bull >= bear + 8 and tech >= 55:
            trader = "BUY"
        elif bear >= bull + 8 and tech <= 45:
            trader = "SELL"
        elif confluence.get("ready") and confluence.get("direction") in {"BUY", "SELL"} and abs(tech - 50) >= 8:
            trader = confluence["direction"]
        else:
            trader = "WAIT"

        ai_confidence = _clamp(abs(bull - bear) + abs(tech - 50) * 0.5)

        return {
            "mode": "unified_heuristic",
            "provider": self.llm_provider,
            "models": {"deep": self.deep_model, "quick": self.quick_model},
            "debate_rounds": self.max_debate_rounds,
            "pine_report": pine_report,
            "analysts": {
                "technical": {
                    "bias": "BULLISH" if tech >= 55 else "BEARISH" if tech <= 45 else "NEUTRAL",
                    "score": round(tech, 2),
                    "source": "pine_confluence",
                },
                "news": {
                    "bias": "BULLISH" if news_score >= 55 else "BEARISH" if news_score <= 45 else "NEUTRAL",
                    "score": round(news_score, 2),
                    "source": intel.get("source") or "worldmonitor",
                },
                "sentiment": {
                    "bias": "BULLISH" if sentiment_score >= 55 else "BEARISH" if sentiment_score <= 45 else "NEUTRAL",
                    "score": round(sentiment_score, 2),
                    "source": intel.get("source") or "worldmonitor",
                },
                "macro": {
                    "bias": "BULLISH" if macro_score >= 55 else "BEARISH" if macro_score <= 45 else "NEUTRAL",
                    "score": round(macro_score, 2),
                    "source": intel.get("source") or "worldmonitor",
                },
                "kronos": {
                    "bias": kronos_bias,
                    "score": round(kronos_score, 2),
                    "enabled": bool(kronos and not kronos.get("disabled")),
                    "source": (kronos or {}).get("source"),
                },
                "swarm": {
                    "bias": swarm_bias,
                    "score": round(swarm_score, 2),
                    "enabled": bool(swarm and not swarm.get("stub")),
                    "source": (swarm or {}).get("source"),
                },
            },
            "bull_research": round(bull, 2),
            "bear_research": round(bear, 2),
            "trader": trader,
            "ai_confidence": round(ai_confidence, 2),
            "notes": "Unified multi-agent desk (Pine + WorldMonitor + MiroFish + Kronos)",
            "swarm": swarm,
            "providers_used": {
                "pine": True,
                "worldmonitor": True,
                "mirofish": True,
                "kronos": bool(kronos and not kronos.get("disabled")),
                "llm": self.llm_provider,
                "tradingagents": False,
            },
        }


def get_trading_brain() -> TradingBrain:
    return TradingBrain()
