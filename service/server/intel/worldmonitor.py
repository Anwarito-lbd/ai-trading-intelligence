"""WorldMonitor remote intelligence client (AGPL-safe: HTTP/SDK only, no source vendoring)."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.worldmonitor.app"


class WorldMonitorClient:
    """Fetches news/macro briefs from WorldMonitor's public API when configured.

    Without WORLDMONITOR_API_KEY the client returns a deterministic stub brief so
    local paper-trading demos still work.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("WORLDMONITOR_API_KEY", "")).strip()
        self.base_url = (base_url or os.getenv("WORLDMONITOR_API_BASE_URL", DEFAULT_BASE)).rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "AI-Trader-Intel/1.0"}
        if self.api_key:
            headers["X-WorldMonitor-Key"] = self.api_key
        return headers

    def fetch_brief(self, symbol: str, query: str | None = None) -> dict[str, Any]:
        """Return a normalized intel brief for the orchestrator."""
        if not self.configured:
            return self._stub_brief(symbol, reason="WORLDMONITOR_API_KEY not set")

        try:
            # Prefer finance-oriented endpoints when available; fall back gracefully.
            url = f"{self.base_url}/v1/briefs"
            params = {"symbol": symbol, "q": query or symbol, "limit": 5}
            resp = requests.get(url, headers=self._headers(), params=params, timeout=self.timeout_seconds)
            if resp.status_code >= 400:
                logger.warning("WorldMonitor brief HTTP %s: %s", resp.status_code, resp.text[:200])
                return self._stub_brief(symbol, reason=f"http_{resp.status_code}")
            data = resp.json()
            return self._normalize_payload(symbol, data)
        except Exception as exc:
            logger.warning("WorldMonitor brief failed: %s", exc)
            return self._stub_brief(symbol, reason=str(exc))

    def _normalize_payload(self, symbol: str, data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            news_score = float(data.get("news_score", data.get("sentiment", 0.0)) or 0.0)
            macro_bias = str(data.get("macro_bias", data.get("macro", "NEUTRAL"))).upper()
            headlines = data.get("headlines") or data.get("items") or []
            if isinstance(headlines, list):
                headline_texts = [str(h.get("title") if isinstance(h, dict) else h) for h in headlines[:5]]
            else:
                headline_texts = []
            return {
                "source": "worldmonitor",
                "symbol": symbol,
                "news_score": max(-1.0, min(1.0, news_score)),
                "macro_bias": macro_bias if macro_bias in {"BULLISH", "BEARISH", "NEUTRAL"} else "NEUTRAL",
                "geopolitical_risk": str(data.get("geopolitical_risk", "LOW")),
                "headlines": headline_texts,
                "raw": data,
                "stub": False,
            }
        return self._stub_brief(symbol, reason="unexpected_payload")

    @staticmethod
    def _stub_brief(symbol: str, reason: str) -> dict[str, Any]:
        # Mildly bullish stub so demos show non-zero news context without claiming live intel.
        return {
            "source": "worldmonitor_stub",
            "symbol": symbol,
            "news_score": 0.15,
            "macro_bias": "NEUTRAL",
            "geopolitical_risk": "LOW",
            "headlines": [
                f"[stub] No live WorldMonitor key — placeholder brief for {symbol}",
                f"[stub] reason={reason}",
            ],
            "raw": None,
            "stub": True,
            "reason": reason,
        }


def get_worldmonitor_client() -> WorldMonitorClient:
    return WorldMonitorClient()
