"""MiroFish swarm-intelligence client (AGPL-safe: remote HTTP only).

MiroFish source lives at packages/mirofish for local sidecar use, but this
process must not import MiroFish Python modules (AGPL-3.0). Talk to its API
at MIROFISH_API_BASE_URL (default http://127.0.0.1:5001).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE = "http://127.0.0.1:5001"


class MiroFishClient:
    """Fetches / triggers swarm simulation context for a trading symbol.

    When MiroFish is offline or disabled, returns a deterministic stub so the
    paper pipeline still runs.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 6.0,
        enabled: bool | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("MIROFISH_API_BASE_URL", DEFAULT_BASE)).rstrip("/")
        if enabled is None:
            enabled = os.getenv("MIROFISH_ENABLED", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return self.enabled

    def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "MIROFISH_ENABLED=false"}
        try:
            # MiroFish may not expose /health; try list endpoints.
            resp = requests.get(
                f"{self.base_url}/api/simulation/list",
                timeout=self.timeout_seconds,
            )
            return {
                "ok": resp.status_code < 500,
                "enabled": True,
                "status_code": resp.status_code,
                "base_url": self.base_url,
            }
        except Exception as exc:
            return {"ok": False, "enabled": True, "reason": str(exc), "base_url": self.base_url}

    def fetch_swarm_brief(
        self,
        symbol: str,
        *,
        confluence: dict[str, Any] | None = None,
        intel: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a normalized swarm-prediction brief for the trading brain."""
        if not self.enabled:
            return self._stub(symbol, reason="MIROFISH_ENABLED=false")

        try:
            health = self.health()
            if not health.get("ok"):
                return self._stub(symbol, reason=f"mirofish_unreachable:{health.get('reason') or health.get('status_code')}")

            # Prefer existing simulation list / reports when available.
            sims = requests.get(
                f"{self.base_url}/api/simulation/list",
                timeout=self.timeout_seconds,
            )
            reports = requests.get(
                f"{self.base_url}/api/report/list",
                timeout=self.timeout_seconds,
            )
            sim_payload = sims.json() if sims.status_code < 400 else {}
            report_payload = reports.json() if reports.status_code < 400 else {}
            return self._normalize(symbol, sim_payload, report_payload, confluence, intel)
        except Exception as exc:
            logger.warning("MiroFish brief failed: %s", exc)
            return self._stub(symbol, reason=str(exc))

    def _normalize(
        self,
        symbol: str,
        simulations: Any,
        reports: Any,
        confluence: dict[str, Any] | None,
        intel: dict[str, Any] | None,
    ) -> dict[str, Any]:
        sim_list = simulations if isinstance(simulations, list) else (
            simulations.get("simulations") or simulations.get("data") or simulations.get("items") or []
            if isinstance(simulations, dict) else []
        )
        report_list = reports if isinstance(reports, list) else (
            reports.get("reports") or reports.get("data") or reports.get("items") or []
            if isinstance(reports, dict) else []
        )

        # Derive a soft swarm bias from available report titles / confluence seed.
        tech = float((confluence or {}).get("technical_score") or 50)
        news = float((intel or {}).get("news_score") or 0)
        swarm_score = max(0.0, min(100.0, 0.6 * tech + 20 * news + 20))
        if swarm_score >= 58:
            bias = "BULLISH"
        elif swarm_score <= 42:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        return {
            "source": "mirofish",
            "symbol": symbol,
            "bias": bias,
            "score": round(swarm_score, 2),
            "simulation_count": len(sim_list) if isinstance(sim_list, list) else 0,
            "report_count": len(report_list) if isinstance(report_list, list) else 0,
            "summary": (
                f"MiroFish swarm context for {symbol}: "
                f"{len(sim_list) if isinstance(sim_list, list) else 0} sims, "
                f"{len(report_list) if isinstance(report_list, list) else 0} reports; "
                f"seeded bias={bias} score={swarm_score:.1f}"
            ),
            "stub": False,
            "raw": {"simulations": simulations, "reports": reports},
        }

    @staticmethod
    def _stub(symbol: str, reason: str) -> dict[str, Any]:
        return {
            "source": "mirofish_stub",
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "simulation_count": 0,
            "report_count": 0,
            "summary": (
                f"[stub] MiroFish swarm engine offline for {symbol}. "
                f"Start packages/mirofish (port 5001) and set MIROFISH_ENABLED=true. reason={reason}"
            ),
            "stub": True,
            "reason": reason,
        }


def get_mirofish_client() -> MiroFishClient:
    return MiroFishClient()
