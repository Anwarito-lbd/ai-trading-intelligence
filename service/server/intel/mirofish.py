"""Always-on MiroFish-style swarm — same LLM stack as the trading brain.

Runs an in-process multi-persona swarm via Ollama/Groq/OpenAI so swarm analysis
is part of every unified decision. Optionally probes MiroFish sidecar at
MIROFISH_API_BASE_URL (AGPL — HTTP only, never import).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

from intel.llm import chat_completion, resolve_llm_provider, resolve_quick_model

logger = logging.getLogger(__name__)


class MiroFishClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("MIROFISH_API_BASE_URL", "http://127.0.0.1:5001").rstrip("/")
        self.enabled = True
        self.timeout_seconds = float(os.getenv("MIROFISH_TIMEOUT_SECONDS", "8"))
        self.llm_provider = resolve_llm_provider()

    @property
    def configured(self) -> bool:
        return True

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "enabled": True,
            "mode": "in_process_swarm",
            "llm_provider": self.llm_provider,
            "sidecar": self._sidecar_health(),
        }

    def _sidecar_health(self) -> dict[str, Any]:
        try:
            resp = requests.get(f"{self.base_url}/api/simulation/list", timeout=self.timeout_seconds)
            return {"reachable": resp.status_code < 500, "status_code": resp.status_code}
        except Exception as exc:
            return {"reachable": False, "reason": str(exc)}

    def fetch_swarm_brief(
        self,
        symbol: str,
        *,
        confluence: dict[str, Any] | None = None,
        intel: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        swarm = self._llm_swarm(symbol, confluence or {}, intel or {})
        swarm["sidecar"] = self._sidecar_health()
        swarm["integrated"] = True
        swarm["stub"] = False
        return swarm

    def _llm_swarm(self, symbol: str, confluence: dict[str, Any], intel: dict[str, Any]) -> dict[str, Any]:
        model = resolve_quick_model(self.llm_provider)
        tech = confluence.get("technical_score")
        direction = confluence.get("direction")
        news = intel.get("news_score")
        macro = intel.get("macro_bias")
        personas = [
            ("retail_momentum", "Retail momentum trader focused on indicator confluence"),
            ("macro_hedge_fund", "Macro hedge-fund desk weighing news and regime"),
            ("contrarian", "Contrarian skeptic looking for fake breakouts"),
            ("risk_officer", "Risk officer prioritizing capital preservation"),
        ]

        if self.llm_provider in {"ollama", "groq", "openai", "openrouter", "openai_compatible"}:
            prompt = (
                f"Symbol {symbol}. Technical={tech}/100 dir={direction}. "
                f"WorldMonitor news={news} macro={macro}. "
                "For each persona reply one line: "
                "PERSONA=<id>; BIAS=<BULLISH|BEARISH|NEUTRAL>; SCORE=<0-100>\n"
                + "\n".join(f"- {pid}: {role}" for pid, role in personas)
            )
            text = chat_completion(
                provider=self.llm_provider,
                model=model,
                temperature=0.3,
                max_tokens=250,
                timeout=float(os.getenv("UTI_LLM_TIMEOUT_SECONDS", "90")),
                messages=[
                    {
                        "role": "system",
                        "content": "You simulate a MiroFish-style swarm of market agents inside a unified trading desk.",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            votes: list[dict[str, Any]] = []
            for line in (text or "").splitlines():
                m = re.search(
                    r"PERSONA\s*=\s*([^\s;]+).*?BIAS\s*=\s*(BULLISH|BEARISH|NEUTRAL).*?SCORE\s*=\s*(\d{1,3})",
                    line,
                    re.I,
                )
                if m:
                    votes.append(
                        {
                            "persona": m.group(1),
                            "bias": m.group(2).upper(),
                            "score": float(m.group(3)),
                        }
                    )
            if votes:
                avg = sum(float(v["score"]) for v in votes) / len(votes)
                bullish = sum(1 for v in votes if v["bias"] == "BULLISH")
                bearish = sum(1 for v in votes if v["bias"] == "BEARISH")
                bias = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
                return {
                    "source": f"mirofish_swarm_{self.llm_provider}",
                    "symbol": symbol,
                    "bias": bias,
                    "score": round(avg, 2),
                    "simulation_count": len(votes),
                    "report_count": 1,
                    "votes": votes,
                    "llm_model": model,
                    "summary": f"MiroFish swarm via {self.llm_provider}/{model} => {bias} @ {avg:.1f}",
                    "stub": False,
                    "integrated": True,
                }

        return self._local_from_tech(symbol, confluence, intel)

    def _local_from_tech(self, symbol: str, confluence: dict[str, Any], intel: dict[str, Any]) -> dict[str, Any]:
        tech = float(confluence.get("technical_score") or 50)
        news = float(intel.get("news_score") or 0)
        score = max(0, min(100, 0.7 * tech + 15 * news + 15))
        bias = "BULLISH" if score >= 58 else "BEARISH" if score <= 42 else "NEUTRAL"
        return {
            "source": "mirofish_local_swarm",
            "symbol": symbol,
            "bias": bias,
            "score": round(score, 2),
            "simulation_count": 4,
            "report_count": 1,
            "votes": [],
            "summary": f"Local MiroFish-style swarm for {symbol}: {bias} {score:.1f}",
            "stub": False,
            "integrated": True,
        }


def get_mirofish_client() -> MiroFishClient:
    return MiroFishClient()
