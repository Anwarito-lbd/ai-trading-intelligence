"""Consensus gate: stop BUY/SELL when researchers disagree.

Pipeline roles (one desk, one decision):
  1. WorldMonitor        → news / macro / geo
  2. MiroFish swarm      → multi-persona crowd
  3. Kronos              → K-line forecast
  4. Final trader (LLM)  → must respect majority; WAIT on conflict

Pine/TradingView confluence is optional and does NOT vote by default —
you confirm indicators yourself on TV before taking a trade.
"""

from __future__ import annotations

import os
from typing import Any


def _bias_side(bias: str | None) -> str:
    b = (bias or "NEUTRAL").upper()
    if b in {"BUY", "LONG", "BULLISH", "BULL"}:
        return "BULLISH"
    if b in {"SELL", "SHORT", "BEARISH", "BEAR"}:
        return "BEARISH"
    return "NEUTRAL"


def build_consensus(
    *,
    confluence: dict[str, Any],
    intel: dict[str, Any],
    swarm: dict[str, Any] | None,
    kronos: dict[str, Any] | None,
) -> dict[str, Any]:
    votes: list[dict[str, Any]] = []

    pine_ready = bool(confluence.get("ready"))
    pine_dir = str(confluence.get("direction") or "NEUTRAL").upper()
    tech = float(confluence.get("technical_score") or 50)
    # Only count Pine when webhooks are live AND explicitly enabled.
    use_pine_vote = os.getenv("UTI_CONSENSUS_USE_PINE", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if use_pine_vote and pine_ready and pine_dir in {"BUY", "SELL"}:
        votes.append(
            {
                "role": "pine",
                "bias": "BULLISH" if pine_dir == "BUY" else "BEARISH",
                "weight": 1.0,
                "detail": f"confluence {tech}/100",
            }
        )

    macro = _bias_side(str(intel.get("macro_bias")))
    news = float(intel.get("news_score") or 0)
    news_bias = "BULLISH" if news > 0.15 else "BEARISH" if news < -0.15 else "NEUTRAL"
    # WorldMonitor counts as one research vote (macro preferred, else news)
    wm_bias = macro if macro != "NEUTRAL" else news_bias
    votes.append(
        {
            "role": "worldmonitor",
            "bias": wm_bias,
            "weight": 1.2,
            "detail": f"macro={macro} news={news:.3f}",
        }
    )

    swarm_bias = _bias_side((swarm or {}).get("bias"))
    votes.append(
        {
            "role": "mirofish",
            "bias": swarm_bias,
            "weight": 1.0,
            "detail": f"score={(swarm or {}).get('score')}",
        }
    )

    kronos_bias = _bias_side((kronos or {}).get("bias")) if kronos and not kronos.get("disabled") else "NEUTRAL"
    votes.append(
        {
            "role": "kronos",
            "bias": kronos_bias,
            "weight": 1.0,
            "detail": f"chg%={(kronos or {}).get('change_pct')} score={(kronos or {}).get('score')}",
        }
    )

    bull_w = sum(v["weight"] for v in votes if v["bias"] == "BULLISH")
    bear_w = sum(v["weight"] for v in votes if v["bias"] == "BEARISH")
    active = [v for v in votes if v["bias"] != "NEUTRAL" and v["weight"] > 0]

    if bull_w <= 0 and bear_w <= 0:
        majority = "NEUTRAL"
        conflict = False
        action = "WAIT"
        reason = "No directional research votes"
    elif bull_w > 0 and bear_w > 0:
        # Real conflict: both sides present
        ratio = max(bull_w, bear_w) / min(bull_w, bear_w)
        conflict = ratio < 2.5  # need clear dominance
        if conflict:
            majority = "BULLISH" if bull_w >= bear_w else "BEARISH"
            action = "WAIT"
            reason = (
                "Conflict: bull_w={:.1f} vs bear_w={:.1f} ({}) → WAIT".format(
                    bull_w,
                    bear_w,
                    ", ".join(f"{v['role']}={v['bias']}" for v in active),
                )
            )
        else:
            majority = "BULLISH" if bull_w > bear_w else "BEARISH"
            action = "BUY" if majority == "BULLISH" else "SELL"
            reason = f"Clear majority {majority} (bull={bull_w:.1f} bear={bear_w:.1f})"
    else:
        conflict = False
        majority = "BULLISH" if bull_w > bear_w else "BEARISH"
        action = "BUY" if majority == "BULLISH" else "SELL"
        reason = f"Aligned {majority} (bull={bull_w:.1f} bear={bear_w:.1f})"

    # Need at least 2 research agents agreeing before BUY/SELL
    if action in {"BUY", "SELL"}:
        agreeing = sum(1 for v in active if v["bias"] == majority)
        if agreeing < 2:
            action = "WAIT"
            conflict = False
            reason = (
                f"Need ≥2 aligned researchers (got {agreeing}). {reason}"
            )

    return {
        "votes": votes,
        "bull_weight": round(bull_w, 2),
        "bear_weight": round(bear_w, 2),
        "majority": majority,
        "conflict": conflict,
        "action": action,
        "reason": reason,
        "pine_ready": pine_ready,
        "research_mode": True,
    }


def apply_consensus(trader: str, consensus: dict[str, Any]) -> tuple[str, str]:
    """Override LLM/heuristic when it fights consensus."""
    suggested = (trader or "WAIT").upper()
    if suggested not in {"BUY", "SELL", "WAIT"}:
        suggested = "WAIT"
    gated = consensus.get("action") or "WAIT"
    if gated == "WAIT":
        return "WAIT", consensus.get("reason") or "consensus_wait"
    if suggested == "WAIT":
        return "WAIT", "trader_chose_wait"
    if suggested != gated:
        # LLM said opposite of majority → WAIT (do not flip blindly)
        return "WAIT", (
            f"LLM said {suggested} but consensus is {gated}; "
            f"{consensus.get('reason')}"
        )
    return suggested, consensus.get("reason") or "consensus_aligned"
