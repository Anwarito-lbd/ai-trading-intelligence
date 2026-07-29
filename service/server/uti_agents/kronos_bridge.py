"""Optional Kronos price-forecast analyst (disabled by default)."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def get_kronos_forecast(symbol: str) -> dict[str, Any]:
    enabled = os.getenv("KRONOS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "KRONOS_ENABLED=false",
        }
    try:
        # Placeholder hook — full Kronos inference is opt-in and model-heavy.
        logger.info("Kronos enabled for %s but model runtime not wired in V1; returning neutral", symbol)
        return {
            "disabled": False,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "kronos_runtime_not_loaded",
        }
    except Exception as exc:
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": str(exc),
        }
