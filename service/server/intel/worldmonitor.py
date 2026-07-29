"""Always-on WorldMonitor intelligence (SDK + free fallbacks).

Uses worldmonitor-sdk when WORLDMONITOR_API_KEY is set. Without a key, builds a
live brief from public/free market data (yfinance + Alpha Vantage) so the
pipeline never skips this analyst.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

SYMBOL_TO_YF = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "SILVER": "SI=F",
    "NAS100": "NQ=F",
    "NASDAQ": "NQ=F",
    "US100": "NQ=F",
    "US30": "YM=F",
    "SPX500": "ES=F",
    "SPX": "ES=F",
    "USOIL": "CL=F",
    "OIL": "CL=F",
    "WTI": "CL=F",
    "EURUSD": "EURUSD=X",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}


class WorldMonitorClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("WORLDMONITOR_API_KEY", os.getenv("WM_API_KEY", "")).strip()
        self.base_url = os.getenv("WORLDMONITOR_API_BASE_URL", "https://api.worldmonitor.app").rstrip("/")
        self.enabled = True  # always integrated

    @property
    def configured(self) -> bool:
        return True

    def fetch_brief(self, symbol: str, query: str | None = None) -> dict[str, Any]:
        # Always part of the unified pipeline — try SDK → MCP → live free fallback.
        sdk_brief = self._try_sdk(symbol)
        if sdk_brief is not None:
            return self._merge_free_news(symbol, sdk_brief)
        mcp_brief = self._try_mcp(symbol)
        if mcp_brief is not None:
            return self._merge_free_news(symbol, mcp_brief)
        return self._merge_free_news(symbol, self._live_fallback(symbol))

    def _merge_free_news(self, symbol: str, brief: dict[str, Any]) -> dict[str, Any]:
        """Layer free RSS/Finnhub/NewsAPI on top of WorldMonitor / fallback."""
        try:
            from intel.free_news import enrich_news

            extra = enrich_news(symbol, existing=list(brief.get("headlines") or []))
            headlines = extra.get("headlines") or brief.get("headlines") or []
            # Blend scores: keep WM/price signal, nudge with headline sentiment
            base = float(brief.get("news_score") or 0)
            blended = max(-1.0, min(1.0, 0.55 * base + 0.45 * float(extra.get("news_score") or 0)))
            macro = brief.get("macro_bias") or "NEUTRAL"
            if macro == "NEUTRAL":
                macro = extra.get("macro_bias") or macro
            out = dict(brief)
            out["headlines"] = headlines[:12]
            out["news_score"] = round(blended, 4)
            out["macro_bias"] = macro
            out["free_news"] = {"sources": extra.get("sources"), "count": extra.get("count")}
            out["stub"] = False
            out["integrated"] = True
            return out
        except Exception as exc:
            logger.info("free news merge skipped: %s", exc)
            return brief

    def _try_mcp(self, symbol: str) -> dict[str, Any] | None:
        """Best-effort MCP tools/call against worldmonitor.app/mcp (keyed only)."""
        # Unauthenticated MCP always 401 and burns startup time on Render — skip without a key.
        if not self.api_key:
            return None
        mcp_url = os.getenv("WORLDMONITOR_MCP_URL", "https://worldmonitor.app/mcp").rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-WorldMonitor-Key": self.api_key,
        }
        yf_sym = SYMBOL_TO_YF.get(symbol, symbol)
        try:
            # Streamable HTTP JSON-RPC style call
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "get_market_data", "arguments": {"symbols": [yf_sym, symbol], "limit": 5}},
            }
            resp = requests.post(mcp_url, headers=headers, json=payload, timeout=8)
            if resp.status_code >= 400:
                logger.info("WM MCP HTTP %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            # Soft-parse; if auth required, fall through
            if isinstance(data, dict) and data.get("error"):
                logger.info("WM MCP error: %s", data.get("error"))
                return None
            headlines = [f"WorldMonitor MCP market snapshot for {symbol} ({yf_sym})"]
            news_score = 0.0
            text = str(data).lower()
            if "bull" in text or "up" in text or "gain" in text:
                news_score = 0.2
            elif "bear" in text or "down" in text or "drop" in text:
                news_score = -0.2
            return {
                "source": "worldmonitor_mcp",
                "symbol": symbol,
                "news_score": news_score,
                "macro_bias": "BULLISH" if news_score > 0.1 else "BEARISH" if news_score < -0.1 else "NEUTRAL",
                "geopolitical_risk": "LOW",
                "headlines": headlines,
                "raw": data,
                "stub": False,
                "integrated": True,
            }
        except Exception as exc:
            logger.info("WM MCP skipped: %s", exc)
            return None

    def _try_sdk(self, symbol: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None
        try:
            from worldmonitor_sdk import Client

            client = Client(api_key=self.api_key)
            yf_sym = SYMBOL_TO_YF.get(symbol, symbol)
            market = None
            news = None
            try:
                market = client.call_tool("get_market_data", symbols=[yf_sym, symbol])
            except Exception as exc:
                logger.warning("WM get_market_data failed: %s", exc)
            try:
                news = client.call_tool("get_news_intelligence", query=symbol, limit=5)
            except Exception as exc:
                logger.warning("WM get_news_intelligence failed: %s", exc)
            try:
                macro = client.call_tool("get_country_macro", country="US")
            except Exception:
                macro = None

            news_score = 0.0
            headlines: list[str] = []
            if isinstance(news, dict):
                items = news.get("items") or news.get("articles") or news.get("clusters") or []
                if isinstance(items, list):
                    for item in items[:5]:
                        if isinstance(item, dict):
                            title = item.get("title") or item.get("headline") or str(item)
                            headlines.append(str(title))
                            sent = item.get("sentiment") or item.get("score")
                            if isinstance(sent, (int, float)):
                                news_score += float(sent)
                        else:
                            headlines.append(str(item))
                if headlines:
                    news_score = max(-1.0, min(1.0, news_score / max(len(headlines), 1)))

            macro_bias = "NEUTRAL"
            if isinstance(macro, dict):
                text = str(macro).lower()
                if "hawkish" in text or "expansion" in text or "growth" in text:
                    macro_bias = "BULLISH"
                elif "recession" in text or "contraction" in text or "dovish" in text:
                    macro_bias = "BEARISH"

            return {
                "source": "worldmonitor_sdk",
                "symbol": symbol,
                "news_score": news_score,
                "macro_bias": macro_bias,
                "geopolitical_risk": "LOW",
                "headlines": headlines or [f"WorldMonitor live brief for {symbol}"],
                "market": market,
                "raw": {"news": news, "macro": macro, "market": market},
                "stub": False,
                "integrated": True,
            }
        except Exception as exc:
            logger.warning("WorldMonitor SDK failed: %s", exc)
            return None

    def _live_fallback(self, symbol: str) -> dict[str, Any]:
        """Always-on free intel so WorldMonitor role is never skipped."""
        yf_sym = SYMBOL_TO_YF.get(symbol, symbol)
        headlines: list[str] = []
        news_score = 0.0
        price_change_pct = 0.0
        try:
            import yfinance as yf

            ticker = yf.Ticker(yf_sym)
            hist = ticker.history(period="5d")
            if hist is not None and len(hist) >= 2:
                last = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                price_change_pct = ((last - prev) / prev) * 100 if prev else 0.0
                headlines.append(f"{yf_sym} daily change {price_change_pct:+.2f}% (last={last:.2f})")
                news_score = max(-1.0, min(1.0, price_change_pct / 3.0))
            # News if available
            try:
                for item in (ticker.news or [])[:5]:
                    title = item.get("title") or item.get("content", {}).get("title")
                    if title:
                        headlines.append(str(title))
            except Exception:
                pass
        except Exception as exc:
            headlines.append(f"yfinance fallback limited: {exc}")

        # Alpha Vantage news sentiment if key present
        av_key = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
        if av_key and av_key != "demo":
            try:
                resp = requests.get(
                    os.getenv("ALPHA_VANTAGE_BASE_URL", "https://www.alphavantage.co/query"),
                    params={"function": "NEWS_SENTIMENT", "tickers": yf_sym, "apikey": av_key, "limit": 5},
                    timeout=8,
                )
                data = resp.json()
                feed = data.get("feed") or []
                scores = []
                for article in feed[:5]:
                    title = article.get("title")
                    if title:
                        headlines.append(str(title))
                    try:
                        scores.append(float(article.get("overall_sentiment_score") or 0))
                    except Exception:
                        pass
                if scores:
                    news_score = max(-1.0, min(1.0, sum(scores) / len(scores)))
            except Exception as exc:
                logger.info("AV news fallback skipped: %s", exc)

        macro_bias = "BULLISH" if news_score > 0.15 else "BEARISH" if news_score < -0.15 else "NEUTRAL"
        if not headlines:
            headlines = [
                f"WorldMonitor Pro key not set — using live free market intel for {symbol}",
                "Set WORLDMONITOR_API_KEY for full geopolitical/news MCP tools",
            ]
        return {
            "source": "worldmonitor_live_fallback",
            "symbol": symbol,
            "news_score": round(news_score, 4),
            "macro_bias": macro_bias,
            "geopolitical_risk": "LOW",
            "headlines": headlines[:8],
            "price_change_pct": price_change_pct,
            "raw": None,
            "stub": False,
            "integrated": True,
            "needs_worldmonitor_key": not bool(self.api_key),
        }


def get_worldmonitor_client() -> WorldMonitorClient:
    return WorldMonitorClient()
