"""Optional Kronos price-forecast analyst.

Kronos source is vendored at packages/kronos (MIT). Runtime inference is gated by
KRONOS_ENABLED and requires torch + model weights — disabled by default.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_KRONOS_ROOT = Path(__file__).resolve().parents[3] / "packages" / "kronos"


def get_kronos_forecast(symbol: str) -> dict[str, Any]:
    enabled = os.getenv("KRONOS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "KRONOS_ENABLED=false",
            "package_present": _KRONOS_ROOT.exists(),
        }

    if not _KRONOS_ROOT.exists():
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "packages/kronos missing — run git submodule update --init",
            "package_present": False,
        }

    try:
        if str(_KRONOS_ROOT) not in sys.path:
            sys.path.append(str(_KRONOS_ROOT))
        # Import probe only — full predict needs OHLCV frames + GPU/CPU weights.
        import importlib

        importlib.import_module("model")
        logger.info("Kronos package import ok for %s; returning neutral until OHLCV wired", symbol)
        return {
            "disabled": False,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "kronos_imported_awaiting_ohlcv_pipeline",
            "package_present": True,
        }
    except Exception as exc:
        logger.warning("Kronos load failed: %s", exc)
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": str(exc),
            "package_present": True,
        }
