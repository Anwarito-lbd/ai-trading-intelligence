"""Gemini Google Search grounding for live ticker / macro news.

Uses the native generateContent API with tools=[{google_search:{}}].
This is separate from OpenAI-compatible chat — grounding requires the native tool.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BIAS_RE = re.compile(
    r"BIAS\s*[:=]\s*(BULLISH|BEARISH|NEUTRAL)",
    re.I,
)
_SCORE_RE = re.compile(
    r"(?:NEWS[_ ]?SCORE|SCORE)\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    re.I,
)
_SUMMARY_RE = re.compile(
    r"SUMMARY\s*[:=]\s*(.+)",
    re.I,
)


def grounding_enabled() -> bool:
    flag = os.getenv("UTI_GEMINI_SEARCH_GROUNDING", "true").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        return False
    return bool(
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _api_key() -> str:
    return (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("GOOGLE_API_KEY", "").strip()
    )


def _model() -> str:
    return os.getenv(
        "UTI_GEMINI_GROUNDING_MODEL",
        os.getenv("UTI_GEMINI_QUICK_MODEL", "gemini-2.5-flash"),
    )


def _symbol_query(symbol: str) -> str:
    s = (symbol or "").upper()
    mapping = {
        "XAUUSD": "gold price XAUUSD Fed rates",
        "XAGUSD": "silver price XAGUSD",
        "NAS100": "Nasdaq 100 NDX stock market",
        "US30": "Dow Jones DJIA",
        "SPX500": "S&P 500 SPX",
        "USOIL": "WTI crude oil price",
        "EURUSD": "EURUSD euro dollar ECB Fed",
    }
    return mapping.get(s, f"{s} market news")


def gemini_grounded_generate(
    prompt: str,
    *,
    max_output_tokens: int = 400,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """Call Gemini with Google Search grounding. Returns text + citations."""
    key = _api_key()
    if not key:
        return {"ok": False, "reason": "GEMINI_API_KEY missing", "text": "", "sources": []}

    model = _model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    try:
        resp = requests.post(
            url,
            params={"key": key},
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=float(os.getenv("UTI_GEMINI_GROUNDING_TIMEOUT", "45")),
        )
        if resp.status_code >= 400:
            logger.warning("Gemini grounding HTTP %s: %s", resp.status_code, resp.text[:300])
            return {
                "ok": False,
                "reason": f"http_{resp.status_code}",
                "text": "",
                "sources": [],
                "raw_error": resp.text[:300],
            }
        data = resp.json() or {}
        cands = data.get("candidates") or []
        if not cands:
            return {"ok": False, "reason": "no_candidates", "text": "", "sources": []}
        cand0 = cands[0] or {}
        parts = ((cand0.get("content") or {}).get("parts") or [])
        text = "".join(str(p.get("text") or "") for p in parts).strip()
        gm = cand0.get("groundingMetadata") or {}
        sources: list[dict[str, str]] = []
        for chunk in gm.get("groundingChunks") or []:
            web = (chunk or {}).get("web") or {}
            title = str(web.get("title") or "").strip()
            uri = str(web.get("uri") or "").strip()
            if title or uri:
                sources.append({"title": title, "uri": uri})
        return {
            "ok": bool(text),
            "text": text,
            "sources": sources[:8],
            "web_search_queries": list(gm.get("webSearchQueries") or [])[:6],
            "model": model,
            "grounded": bool(sources or gm.get("webSearchQueries")),
        }
    except Exception as exc:
        logger.warning("Gemini grounding failed: %s", exc)
        return {"ok": False, "reason": str(exc), "text": "", "sources": []}


def fetch_grounded_market_brief(symbol: str) -> dict[str, Any]:
    """Live grounded news/macro brief for one symbol (decision-matrix input)."""
    if not grounding_enabled():
        return {
            "enabled": False,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "news_score": 0.0,
            "summary": "",
            "sources": [],
            "integrated": False,
        }

    query_hint = _symbol_query(symbol)
    prompt = (
        f"You are a trading news desk. Use Google Search for LIVE info about {symbol} "
        f"(search hint: {query_hint}).\n"
        "Return EXACTLY these lines:\n"
        "BIAS=<BULLISH|BEARISH|NEUTRAL>\n"
        "NEWS_SCORE=<-1.0 to 1.0>\n"
        "SUMMARY=<one sentence on momentum + macro>\n"
        "CATALYSTS=<up to 3 short bullets separated by | >\n"
        "Focus on the last 24-48h. If unclear, BIAS=NEUTRAL and NEWS_SCORE=0."
    )
    raw = gemini_grounded_generate(prompt, max_output_tokens=350, temperature=0.1)
    text = raw.get("text") or ""
    bias = "NEUTRAL"
    news_score = 0.0
    summary = ""
    catalysts: list[str] = []

    m = _BIAS_RE.search(text)
    if m:
        bias = m.group(1).upper()
    m = _SCORE_RE.search(text)
    if m:
        try:
            news_score = max(-1.0, min(1.0, float(m.group(1))))
        except Exception:
            news_score = 0.0
    m = _SUMMARY_RE.search(text)
    if m:
        summary = m.group(1).strip()
    for line in text.splitlines():
        if line.upper().startswith("CATALYSTS"):
            part = line.split("=", 1)[-1] if "=" in line else line
            catalysts = [c.strip(" -•") for c in part.replace("CATALYSTS", "").split("|") if c.strip()]
            break

    if not summary and text:
        for line in text.splitlines():
            u = line.strip().upper()
            if not line.strip():
                continue
            if u.startswith(("BIAS", "NEWS_SCORE", "NEWS SCORE", "SCORE", "CATALYSTS", "SUMMARY")):
                continue
            summary = line.strip()[:240]
            break
        if not summary:
            summary = text[:240]

    # Soft infer if model skipped the template
    if bias == "NEUTRAL" and abs(news_score) < 1e-9 and text:
        low = text.lower()
        if any(w in low for w in ("rally", "surge", "bullish", "higher", "bid", "rate cut")):
            bias, news_score = "BULLISH", 0.25
        elif any(w in low for w in ("selloff", "drop", "bearish", "lower", "hawkish", "hike")):
            bias, news_score = "BEARISH", -0.25

    return {
        "enabled": True,
        "symbol": (symbol or "").upper(),
        "bias": bias,
        "news_score": round(news_score, 4),
        "summary": summary,
        "catalysts": catalysts[:3],
        "sources": raw.get("sources") or [],
        "web_search_queries": raw.get("web_search_queries") or [],
        "model": raw.get("model"),
        "grounded": bool(raw.get("grounded")),
        "ok": bool(raw.get("ok")),
        "reason": raw.get("reason"),
        "raw_text": text[:800],
        "integrated": True,
        "source": "gemini_google_search_grounding",
    }


def decision_matrix_line(grounded: dict[str, Any] | None) -> str:
    g = grounded or {}
    if not g.get("enabled"):
        return "Grounding: off"
    if not g.get("ok"):
        return f"Grounding: unavailable ({g.get('reason')})"
    cats = " | ".join(g.get("catalysts") or [])
    return (
        f"Grounding: {g.get('bias')} score={g.get('news_score')} — {g.get('summary')}"
        + (f" | {cats}" if cats else "")
    )
