"""Unified multi-agent trading brain.

Always combines Pine confluence + WorldMonitor intel + MiroFish swarm + Kronos
into one decision. Optional TradingAgents graph when TRADINGAGENTS_ENABLED=true.

LLM refinement (final trader vote) supports:
  UTI_LLM_PROVIDER=ollama|groq|openai|openrouter|openai_compatible
Ollama uses OLLAMA_BASE_URL (default http://127.0.0.1:11434/v1) — no key needed.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from intel.llm import (
    chat_completion,
    llm_endpoint,
    resolve_deep_model,
    resolve_llm_provider,
    resolve_quick_model,
)
from uti_agents.pine_analyst import build_pine_technical_report

logger = logging.getLogger(__name__)

_PACKAGES_ROOT = Path(__file__).resolve().parents[3] / "packages" / "tradingagents"
_LLM_REFINE_PROVIDERS = {"groq", "ollama", "openai", "openrouter", "openai_compatible"}


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
        if self.use_tradingagents and self.llm_provider in _LLM_REFINE_PROVIDERS:
            ta_result = self._try_tradingagents(confluence, intel, kronos, swarm, pine_report)
            if ta_result is not None:
                return ta_result
            logger.warning("TradingAgents unavailable; using unified heuristic+LLM desk")

        result = self._heuristic(confluence, intel, kronos, pine_report, swarm)
        if self.llm_provider in _LLM_REFINE_PROVIDERS:
            refined = self._refine_with_llm(result, confluence, intel, swarm, kronos)
            if refined is not None:
                return refined
        return result

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
            f"ready={confluence.get('ready')} votes={confluence.get('vote_count')}\n"
            f"WorldMonitor: news={intel.get('news_score')} macro={intel.get('macro_bias')} "
            f"geo={intel.get('geopolitical_risk')} source={intel.get('source')}\n"
            f"MiroFish swarm: bias={(swarm or {}).get('bias')} score={(swarm or {}).get('score')} "
            f"source={(swarm or {}).get('source')}\n"
            f"Kronos: bias={(kronos or {}).get('bias')} score={(kronos or {}).get('score')} "
            f"chg%={(kronos or {}).get('change_pct')} source={(kronos or {}).get('source')}\n"
            f"Desk heuristic: trader={heuristic.get('trader')} "
            f"bull={heuristic.get('bull_research')} bear={heuristic.get('bear_research')}\n"
            "You are the final trader combining ALL of the above into one paper decision.\n"
            "Return exactly one line: DECISION=<BUY|SELL|WAIT>; CONFIDENCE=<0-100>; REASON=<short>"
        )
        content = chat_completion(
            provider=self.llm_provider,
            model=self.quick_model,
            temperature=0.2,
            max_tokens=120,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Unified trading desk final trader. Weigh Pine + WorldMonitor + "
                        "MiroFish + Kronos together. Prefer WAIT when evidence conflicts."
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
            "pine": True,
            "worldmonitor": True,
            "mirofish": True,
            "kronos": bool(kronos and not kronos.get("disabled")),
            "llm": self.llm_provider,
            "tradingagents": False,
        }
        return out

    def _try_tradingagents(
        self,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        swarm: dict[str, Any] | None,
        pine_report: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run TradingAgents graph, then fold UTI intel into the same record."""
        if _PACKAGES_ROOT.exists() and str(_PACKAGES_ROOT) not in sys.path:
            sys.path.append(str(_PACKAGES_ROOT))
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
        except Exception as exc:
            logger.info("TradingAgents import failed: %s", exc)
            return None

        # Skip full graph for local tiny models / when explicitly using refine-only
        if os.getenv("TRADINGAGENTS_FULL_GRAPH", "false").strip().lower() not in {
            "1", "true", "yes", "on"
        }:
            # Default: fold TradingAgents *roles* via heuristic+LLM (faster, same pipeline).
            # Set TRADINGAGENTS_FULL_GRAPH=true to run the real LangGraph (slow on CPU Ollama).
            return None

        try:
            config = DEFAULT_CONFIG.copy()
            provider = self.llm_provider if self.llm_provider != "heuristic" else "ollama"
            config["llm_provider"] = provider
            config["deep_think_llm"] = self.deep_model
            config["quick_think_llm"] = self.quick_model
            config["max_debate_rounds"] = self.max_debate_rounds
            if provider == "ollama":
                base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
                if not base.endswith("/v1"):
                    base = base + "/v1"
                config["backend_url"] = base
            elif provider == "groq":
                config["backend_url"] = "https://api.groq.com/openai/v1"

            graph = TradingAgentsGraph(debug=False, config=config)
            symbol = confluence.get("symbol") or "SPY"
            ticker = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}.get(symbol, symbol)
            from datetime import datetime, timezone

            trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _, decision = graph.propagate(ticker, trade_date)
            trader = _parse_trader_text(str(decision))

            # Fold WorldMonitor / MiroFish / Kronos into the same payload
            base = self._heuristic(confluence, intel, kronos, pine_report, swarm)
            base.update(
                {
                    "mode": f"tradingagents+unified+{provider}",
                    "provider": provider,
                    "models": {"deep": self.deep_model, "quick": self.quick_model},
                    "trader": trader,
                    "ai_confidence": _clamp(float(base.get("ai_confidence") or 70) + 5),
                    "raw_decision": decision,
                    "pine_report": pine_report,
                    "notes": (
                        f"TradingAgents({ticker}) + WorldMonitor + MiroFish + Kronos "
                        f"via {provider}/{self.quick_model}"
                    ),
                    "providers_used": {
                        "pine": True,
                        "worldmonitor": True,
                        "mirofish": True,
                        "kronos": bool(kronos and not kronos.get("disabled")),
                        "llm": provider,
                        "tradingagents": True,
                    },
                }
            )
            return base
        except Exception as exc:
            logger.warning("TradingAgents propagate failed: %s", exc)
            return None

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
