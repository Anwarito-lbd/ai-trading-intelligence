"""Confluence scoring: fuse per-indicator votes into a technical score 0-100."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from confluence.indicators import INDICATORS, KNOWN_INDICATOR_IDS
from intel.symbols import normalize_symbol, normalize_timeframe


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def side_to_score(side: str) -> float:
    side_u = (side or "").strip().upper()
    if side_u in {"BUY", "LONG", "BULL", "BULLISH"}:
        return 1.0
    if side_u in {"SELL", "SHORT", "BEAR", "BEARISH"}:
        return -1.0
    return 0.0


def normalize_side(side: str) -> str:
    score = side_to_score(side)
    if score > 0:
        return "BUY"
    if score < 0:
        return "SELL"
    return "NEUTRAL"


class ConfluenceEngine:
    def __init__(
        self,
        ttl_seconds: int | None = None,
        min_votes: int | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds if ttl_seconds is not None else int(
            os.getenv("CONFLUENCE_TTL_SECONDS", "900")
        )
        self.min_votes = min_votes if min_votes is not None else int(
            os.getenv("CONFLUENCE_MIN_VOTES", "3")
        )

    def filter_fresh(self, votes: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(timezone.utc)
        fresh: list[dict[str, Any]] = []
        for vote in votes:
            received = _parse_iso(vote.get("received_at")) or _parse_iso(vote.get("bar_time"))
            if received is None:
                fresh.append(vote)
                continue
            age = (now - received).total_seconds()
            if age <= self.ttl_seconds:
                fresh.append(vote)
        return fresh

    def latest_per_indicator(self, votes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for vote in votes:
            indicator_id = vote.get("indicator_id")
            if indicator_id not in KNOWN_INDICATOR_IDS:
                continue
            prev = latest.get(indicator_id)
            if prev is None:
                latest[indicator_id] = vote
                continue
            prev_t = _parse_iso(prev.get("received_at")) or _parse_iso(prev.get("bar_time"))
            cur_t = _parse_iso(vote.get("received_at")) or _parse_iso(vote.get("bar_time"))
            if cur_t and (prev_t is None or cur_t >= prev_t):
                latest[indicator_id] = vote
        return latest

    def score(
        self,
        votes: list[dict[str, Any]],
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        fresh = self.filter_fresh(votes)
        if symbol:
            symbol_n = normalize_symbol(symbol)
            fresh = [v for v in fresh if normalize_symbol(v.get("symbol")) == symbol_n]
        else:
            symbol_n = normalize_symbol(fresh[0]["symbol"]) if fresh else "UNKNOWN"
        if timeframe:
            tf_n = normalize_timeframe(timeframe)
            fresh = [v for v in fresh if normalize_timeframe(v.get("timeframe")) == tf_n]
        else:
            tf_n = normalize_timeframe(fresh[0].get("timeframe")) if fresh else "30"

        latest = self.latest_per_indicator(fresh)
        rows: list[dict[str, Any]] = []
        weighted = 0.0
        weight_sum = 0.0
        buy_count = 0
        sell_count = 0
        neutral_count = 0

        for indicator_id, meta in INDICATORS.items():
            vote = latest.get(indicator_id)
            weight = float(meta["weight"])
            if vote is None:
                rows.append(
                    {
                        "indicator_id": indicator_id,
                        "name": meta["name"],
                        "side": "MISSING",
                        "strength": 0.0,
                        "weight": weight,
                        "fresh": False,
                    }
                )
                continue
            side = normalize_side(str(vote.get("side") or "NEUTRAL"))
            strength = float(vote.get("strength") or 0.7)
            strength = max(0.0, min(1.0, strength))
            signed = side_to_score(side) * strength
            weighted += signed * weight
            weight_sum += weight
            if side == "BUY":
                buy_count += 1
            elif side == "SELL":
                sell_count += 1
            else:
                neutral_count += 1
            rows.append(
                {
                    "indicator_id": indicator_id,
                    "name": meta["name"],
                    "side": side,
                    "strength": strength,
                    "weight": weight,
                    "fresh": True,
                    "entry": vote.get("entry"),
                    "sl": vote.get("sl"),
                    "tps": vote.get("tps") or [],
                    "bar_time": vote.get("bar_time"),
                    "received_at": vote.get("received_at"),
                }
            )

        if weight_sum <= 0:
            technical_score = 50.0
            direction = "NEUTRAL"
        else:
            # Map [-1, 1] → [0, 100]
            technical_score = round(50.0 + 50.0 * (weighted / weight_sum), 2)
            if buy_count > sell_count and buy_count >= self.min_votes:
                direction = "BUY"
            elif sell_count > buy_count and sell_count >= self.min_votes:
                direction = "SELL"
            elif technical_score >= 60:
                direction = "BUY"
            elif technical_score <= 40:
                direction = "SELL"
            else:
                direction = "NEUTRAL"

        active = buy_count + sell_count + neutral_count
        ready = active >= self.min_votes and direction in {"BUY", "SELL"}

        # Aggregate levels from agreeing indicators
        entries = [r["entry"] for r in rows if r.get("fresh") and r.get("side") == direction and r.get("entry")]
        stops = [r["sl"] for r in rows if r.get("fresh") and r.get("side") == direction and r.get("sl")]
        tps: list[float] = []
        for r in rows:
            if r.get("fresh") and r.get("side") == direction:
                for tp in r.get("tps") or []:
                    try:
                        tps.append(float(tp))
                    except Exception:
                        pass

        return {
            "symbol": symbol_n,
            "timeframe": tf_n,
            "technical_score": technical_score,
            "direction": direction,
            "ready": ready,
            "min_votes": self.min_votes,
            "active_votes": active,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "neutral_count": neutral_count,
            "indicators": rows,
            "entry": float(sum(entries) / len(entries)) if entries else None,
            "sl": float(sum(stops) / len(stops)) if stops else None,
            "tps": sorted(set(round(t, 4) for t in tps))[:3],
            "ttl_seconds": self.ttl_seconds,
        }


def get_confluence_engine() -> ConfluenceEngine:
    return ConfluenceEngine()
