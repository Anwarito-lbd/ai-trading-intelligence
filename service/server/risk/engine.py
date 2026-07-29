"""Paper-trading risk gate for Unified Trading Intelligence decisions."""

from __future__ import annotations

import os
from typing import Any


class RiskEngine:
    def __init__(self) -> None:
        self.kill_switch = os.getenv("UTI_KILL_SWITCH", "false").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self.paper_only = os.getenv("UTI_PAPER_ONLY", "true").strip().lower() not in {
            "0", "false", "no", "off"
        }
        self.max_risk_pct = float(os.getenv("UTI_MAX_RISK_PCT", "1.0"))
        self.max_daily_loss_pct = float(os.getenv("UTI_MAX_DAILY_LOSS_PCT", "3.0"))
        self.min_rr = float(os.getenv("UTI_MIN_RR", "1.2"))
        self.min_technical = float(os.getenv("UTI_MIN_TECHNICAL_SCORE", "58"))
        self.min_ai_confidence = float(os.getenv("UTI_MIN_AI_CONFIDENCE", "55"))
        self.block_high_geo = os.getenv("UTI_BLOCK_HIGH_GEO_RISK", "true").strip().lower() in {
            "1", "true", "yes", "on"
        }

    def evaluate(
        self,
        *,
        decision: str,
        technical_score: float,
        ai_confidence: float,
        entry: float | None,
        sl: float | None,
        tps: list[float] | None,
        news_score: float,
        geopolitical_risk: str,
        cash: float = 100000.0,
        daily_pnl_pct: float = 0.0,
    ) -> dict[str, Any]:
        reasons: list[str] = []
        approved = True

        if self.kill_switch:
            approved = False
            reasons.append("kill_switch_enabled")

        if not self.paper_only:
            # Live is intentionally blocked in V1/V2.
            approved = False
            reasons.append("live_trading_disabled_until_v4")

        decision_u = (decision or "WAIT").upper()
        if decision_u == "WAIT":
            approved = False
            reasons.append("decision_is_wait")

        if technical_score < self.min_technical and decision_u in {"BUY", "SELL"}:
            # Soft warning — still allow if AI is strong, but record
            reasons.append(f"technical_below_min:{technical_score}")

        if ai_confidence < self.min_ai_confidence:
            approved = False
            reasons.append(f"ai_confidence_below_min:{ai_confidence}")

        if abs(daily_pnl_pct) >= self.max_daily_loss_pct and daily_pnl_pct < 0:
            approved = False
            reasons.append("max_daily_loss_reached")

        geo = (geopolitical_risk or "LOW").upper()
        if self.block_high_geo and geo in {"HIGH", "SEVERE", "CRITICAL"}:
            approved = False
            reasons.append(f"geopolitical_risk:{geo}")

        # News opposed to trade direction
        if decision_u == "BUY" and news_score <= -0.55:
            approved = False
            reasons.append("news_strongly_bearish")
        if decision_u == "SELL" and news_score >= 0.55:
            approved = False
            reasons.append("news_strongly_bullish")

        rr = None
        quantity = 0.0
        if entry and sl and entry != sl:
            risk_per_unit = abs(entry - sl)
            reward = None
            if tps:
                # Use furthest TP in trade direction
                if decision_u == "BUY":
                    best_tp = max(tps)
                    reward = abs(best_tp - entry)
                elif decision_u == "SELL":
                    best_tp = min(tps)
                    reward = abs(entry - best_tp)
            if reward and risk_per_unit > 0:
                rr = round(reward / risk_per_unit, 2)
                if rr < self.min_rr:
                    approved = False
                    reasons.append(f"rr_below_min:{rr}")
            risk_budget = cash * (self.max_risk_pct / 100.0)
            quantity = round(risk_budget / risk_per_unit, 6) if risk_per_unit > 0 else 0.0
            # Cap notionals so paper book stays within cash for long entries
            if entry and entry > 0 and quantity * entry > cash * 0.25:
                quantity = round((cash * 0.25) / entry, 6)
        elif decision_u in {"BUY", "SELL"}:
            reasons.append("missing_entry_or_sl")
            # Still allow paper with default size
            quantity = round((cash * 0.01) / max(entry or 1.0, 1e-6), 6)

        if not reasons and approved:
            reasons.append("ok")

        return {
            "approved": approved and decision_u in {"BUY", "SELL"},
            "status": "APPROVED" if (approved and decision_u in {"BUY", "SELL"}) else "REJECTED",
            "reasons": reasons,
            "quantity": quantity,
            "rr": rr,
            "max_risk_pct": self.max_risk_pct,
            "paper_only": self.paper_only,
            "kill_switch": self.kill_switch,
        }


def get_risk_engine() -> RiskEngine:
    return RiskEngine()
