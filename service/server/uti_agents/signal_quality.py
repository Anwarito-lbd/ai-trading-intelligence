"""Only emit actionable BUY/SELL when the setup is high quality.

Weak / conflicting / research-only noise stays WAIT (no signal shown).
"""

from __future__ import annotations

import os
from typing import Any


def evaluate_signal_quality(
    *,
    decision: str,
    confluence: dict[str, Any],
    intel: dict[str, Any],
    swarm: dict[str, Any] | None,
    kronos: dict[str, Any] | None,
    consensus: dict[str, Any] | None,
    ai_confidence: float,
) -> dict[str, Any]:
    """Return whether this is a 'good trade' worth showing / auto-filling."""
    decision_u = (decision or "WAIT").upper()
    reasons: list[str] = []
    score = 0.0

    min_conf = float(os.getenv("UTI_SIGNAL_MIN_CONFIDENCE", "65"))
    min_quality = float(os.getenv("UTI_SIGNAL_MIN_QUALITY", "60"))
    require_pine = os.getenv("UTI_SIGNAL_REQUIRE_PINE", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }

    consensus = consensus or {}
    if consensus.get("conflict"):
        return {
            "good_trade": False,
            "show_signal": False,
            "quality_score": 0.0,
            "label": "NO SIGNAL",
            "reasons": ["researchers_conflict → wait"],
            "decision": "WAIT",
        }

    if decision_u == "WAIT":
        return {
            "good_trade": False,
            "show_signal": False,
            "quality_score": float(ai_confidence or 0) * 0.3,
            "label": "NO SIGNAL",
            "reasons": ["desk_chose_wait"],
            "decision": "WAIT",
        }

    pine_ready = bool(confluence.get("ready"))
    if require_pine and not pine_ready:
        return {
            "good_trade": False,
            "show_signal": False,
            "quality_score": 20.0,
            "label": "NO SIGNAL",
            "reasons": ["pine_confluence_required"],
            "decision": "WAIT",
        }

    # Confidence
    if ai_confidence >= min_conf:
        score += 25
    else:
        reasons.append(f"confidence<{min_conf}")

    # Consensus alignment
    if not consensus.get("conflict") and consensus.get("action") == decision_u:
        score += 25
    elif consensus.get("action") == "WAIT":
        reasons.append("consensus_wait")
    else:
        reasons.append("consensus_mismatch")

    # WorldMonitor not fighting the trade
    macro = str(intel.get("macro_bias") or "NEUTRAL").upper()
    news = float(intel.get("news_score") or 0)
    if decision_u == "BUY" and macro == "BEARISH":
        reasons.append("macro_against_buy")
        score -= 20
    elif decision_u == "SELL" and macro == "BULLISH":
        reasons.append("macro_against_sell")
        score -= 20
    else:
        score += 15

    if decision_u == "BUY" and news < -0.2:
        reasons.append("news_against_buy")
        score -= 10
    elif decision_u == "SELL" and news > 0.2:
        reasons.append("news_against_sell")
        score -= 10
    else:
        score += 10

    # Kronos agree or neutral
    k_bias = str((kronos or {}).get("bias") or "NEUTRAL").upper()
    if (decision_u == "BUY" and k_bias == "BEARISH") or (decision_u == "SELL" and k_bias == "BULLISH"):
        reasons.append("kronos_against")
        score -= 15
    elif k_bias in {"BULLISH", "BEARISH"}:
        score += 10
    else:
        score += 5

    # Swarm agree or neutral
    s_bias = str((swarm or {}).get("bias") or "NEUTRAL").upper()
    if (decision_u == "BUY" and s_bias == "BEARISH") or (decision_u == "SELL" and s_bias == "BULLISH"):
        reasons.append("swarm_against")
        score -= 10
    else:
        score += 10

    # Pine bonus
    if pine_ready and confluence.get("direction") == decision_u:
        score += 15
    elif not pine_ready:
        # Research-only: harder bar
        score -= 5
        reasons.append("research_only_no_pine")

    score = max(0.0, min(100.0, score))
    good = score >= min_quality and not reasons.count("consensus_wait")
    # Hard vetoes
    hard_veto = any(
        r in reasons
        for r in ("macro_against_buy", "macro_against_sell", "consensus_wait", "consensus_mismatch")
    )
    if hard_veto:
        good = False

    if good:
        return {
            "good_trade": True,
            "show_signal": True,
            "quality_score": round(score, 1),
            "label": decision_u,
            "reasons": reasons or ["aligned_setup"],
            "decision": decision_u,
        }

    return {
        "good_trade": False,
        "show_signal": False,
        "quality_score": round(score, 1),
        "label": "NO SIGNAL",
        "reasons": reasons or [f"quality<{min_quality}"],
        "decision": "WAIT",
        "suppressed": decision_u,
    }
