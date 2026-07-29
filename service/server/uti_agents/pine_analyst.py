"""Pine Technical Analyst — injects confluence scores into the multi-agent research pack."""

from __future__ import annotations

from typing import Any


def build_pine_technical_report(confluence: dict[str, Any]) -> dict[str, Any]:
    indicators = confluence.get("indicators") or []
    lines = [
        f"Pine confluence for {confluence.get('symbol')} {confluence.get('timeframe')}m",
        f"Technical score: {confluence.get('technical_score')}/100",
        f"Direction: {confluence.get('direction')}",
        f"Votes: buy={confluence.get('buy_count')} sell={confluence.get('sell_count')} "
        f"neutral={confluence.get('neutral_count')} ready={confluence.get('ready')}",
        "Per-indicator:",
    ]
    for row in indicators:
        lines.append(
            f"  - {row.get('name')} [{row.get('indicator_id')}]: "
            f"{row.get('side')} strength={row.get('strength')} weight={row.get('weight')}"
        )
    return {
        "role": "technical_analyst",
        "label": "Pine Technical Analyst",
        "bias": confluence.get("direction") or "NEUTRAL",
        "score": float(confluence.get("technical_score") or 50),
        "summary": "\n".join(lines),
        "indicators": indicators,
    }
