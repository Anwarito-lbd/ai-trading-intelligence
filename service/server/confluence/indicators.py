"""Indicator registry for the five Pine signal sources."""

from __future__ import annotations

INDICATORS: dict[str, dict] = {
    "triple_confluence": {
        "id": "triple_confluence",
        "name": "Triple Confluence Navigator",
        "weight": 1.2,
    },
    "sfx_algo": {
        "id": "sfx_algo",
        "name": "Flux Charts SFX Algo",
        "weight": 1.1,
    },
    "smart_trader": {
        "id": "smart_trader",
        "name": "Smart Trader EP03",
        "weight": 0.8,
    },
    "swing_volume": {
        "id": "swing_volume",
        "name": "Swing Volume Profile Pro",
        "weight": 0.9,
    },
    "money_algorithm": {
        "id": "money_algorithm",
        "name": "Money Algorithm",
        "weight": 1.0,
    },
}

KNOWN_INDICATOR_IDS = set(INDICATORS.keys())
