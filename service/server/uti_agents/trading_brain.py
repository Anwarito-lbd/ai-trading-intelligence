"""Multi-agent trading brain.

Uses TradingAgents (vendored under packages/tradingagents) when enabled and
importable; otherwise runs a local heuristic multi-agent simulator that mirrors
the same roles (technical/news/sentiment/macro → bull/bear → trader → risk prep).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from uti_agents.pine_analyst import build_pine_technical_report

logger = logging.getLogger(__name__)

_PACKAGES_ROOT = Path(__file__).resolve().parents[3] / "packages" / "tradingagents"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


class TradingBrain:
    def __init__(self) -> None:
        self.use_tradingagents = os.getenv("TRADINGAGENTS_ENABLED", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.llm_provider = os.getenv("UTI_LLM_PROVIDER", os.getenv("TRADINGAGENTS_LLM_PROVIDER", "heuristic"))
        self.deep_model = os.getenv("UTI_DEEP_MODEL", os.getenv("TRADINGAGENTS_DEEP_THINK_LLM", "gpt-5.5"))
        self.quick_model = os.getenv("UTI_QUICK_MODEL", os.getenv("TRADINGAGENTS_QUICK_THINK_LLM", "gpt-5.4-mini"))
        self.max_debate_rounds = int(os.getenv("UTI_MAX_DEBATE_ROUNDS", "2"))

    def analyze(
        self,
        *,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pine_report = build_pine_technical_report(confluence)
        if self.use_tradingagents:
            ta_result = self._try_tradingagents(confluence, intel)
            if ta_result is not None:
                ta_result["pine_report"] = pine_report
                ta_result["mode"] = "tradingagents"
                return ta_result
            logger.warning("TradingAgents enabled but unavailable; falling back to heuristic brain")

        return self._heuristic(confluence, intel, kronos, pine_report)

    def _try_tradingagents(self, confluence: dict[str, Any], intel: dict[str, Any]) -> dict[str, Any] | None:
        if _PACKAGES_ROOT.exists() and str(_PACKAGES_ROOT) not in sys.path:
            sys.path.append(str(_PACKAGES_ROOT))
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
        except Exception as exc:
            logger.info("TradingAgents import failed: %s", exc)
            return None

        try:
            config = DEFAULT_CONFIG.copy()
            config["llm_provider"] = self.llm_provider if self.llm_provider != "heuristic" else "openai"
            config["deep_think_llm"] = self.deep_model
            config["quick_think_llm"] = self.quick_model
            config["max_debate_rounds"] = self.max_debate_rounds
            graph = TradingAgentsGraph(debug=False, config=config)
            symbol = confluence.get("symbol") or "SPY"
            # TradingAgents expects equity-style tickers; map metals/crypto conservatively.
            ticker = {"XAUUSD": "GC=F", "BTCUSD": "BTC-USD", "ETHUSD": "ETH-USD"}.get(symbol, symbol)
            from datetime import datetime, timezone

            trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _, decision = graph.propagate(ticker, trade_date)
            decision_text = str(decision).upper()
            if "BUY" in decision_text or "LONG" in decision_text:
                trader = "BUY"
            elif "SELL" in decision_text or "SHORT" in decision_text:
                trader = "SELL"
            else:
                trader = "WAIT"
            return {
                "mode": "tradingagents",
                "provider": config["llm_provider"],
                "models": {"deep": self.deep_model, "quick": self.quick_model},
                "analysts": {
                    "technical": {"bias": confluence.get("direction"), "score": confluence.get("technical_score")},
                    "news": {"bias": intel.get("macro_bias"), "score": 50 + 40 * float(intel.get("news_score") or 0)},
                },
                "bull_research": 70.0,
                "bear_research": 40.0,
                "trader": trader,
                "ai_confidence": 75.0,
                "raw_decision": decision,
                "notes": f"TradingAgents decision for {ticker} on {trade_date}",
            }
        except Exception as exc:
            logger.warning("TradingAgents propagate failed: %s", exc)
            return None

    def _heuristic(
        self,
        confluence: dict[str, Any],
        intel: dict[str, Any],
        kronos: dict[str, Any] | None,
        pine_report: dict[str, Any],
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

        # Bull / bear debate scores
        bull = 0.45 * tech + 0.2 * news_score + 0.15 * sentiment_score + 0.1 * macro_score + 0.1 * kronos_score
        bear = 100 - bull
        # Debate rounds nudge
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

        technical_bias = "BULLISH" if tech >= 55 else "BEARISH" if tech <= 45 else "NEUTRAL"
        news_bias = "BULLISH" if news_score >= 55 else "BEARISH" if news_score <= 45 else "NEUTRAL"
        sentiment_bias = "BULLISH" if sentiment_score >= 55 else "BEARISH" if sentiment_score <= 45 else "NEUTRAL"
        macro_bias = "BULLISH" if macro_score >= 55 else "BEARISH" if macro_score <= 45 else "NEUTRAL"

        return {
            "mode": "heuristic",
            "provider": self.llm_provider,
            "models": {"deep": self.deep_model, "quick": self.quick_model},
            "debate_rounds": self.max_debate_rounds,
            "pine_report": pine_report,
            "analysts": {
                "technical": {"bias": technical_bias, "score": round(tech, 2)},
                "news": {"bias": news_bias, "score": round(news_score, 2)},
                "sentiment": {"bias": sentiment_bias, "score": round(sentiment_score, 2)},
                "macro": {"bias": macro_bias, "score": round(macro_score, 2)},
                "kronos": {"bias": kronos_bias, "score": round(kronos_score, 2), "enabled": bool(kronos and not kronos.get("disabled"))},
            },
            "bull_research": round(bull, 2),
            "bear_research": round(bear, 2),
            "trader": trader,
            "ai_confidence": round(ai_confidence, 2),
            "notes": "Local multi-agent heuristic (TradingAgents graph optional via TRADINGAGENTS_ENABLED)",
        }


def get_trading_brain() -> TradingBrain:
    return TradingBrain()
