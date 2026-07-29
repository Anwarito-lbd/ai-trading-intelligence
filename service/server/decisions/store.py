"""Persist and load Unified Trading Intelligence decision audit records."""

from __future__ import annotations

import json
from typing import Any

from database import get_db_connection
from routes_shared import utc_now_iso_z


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def next_trade_number(cursor) -> int:
    cursor.execute("SELECT COALESCE(MAX(trade_number), 0) + 1 AS n FROM uti_decisions")
    row = cursor.fetchone()
    return int(row["n"] if isinstance(row, dict) else row[0])


def insert_pine_vote(vote: dict[str, Any]) -> dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO uti_pine_votes (
                indicator_id, symbol, timeframe, side, strength,
                entry, sl, tps_json, bar_time, received_at, dedupe_key, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vote["indicator_id"],
                vote["symbol"],
                vote["timeframe"],
                vote["side"],
                vote["strength"],
                vote.get("entry"),
                vote.get("sl"),
                _dumps(vote.get("tps") or []),
                vote.get("bar_time"),
                vote.get("received_at") or utc_now_iso_z(),
                vote.get("dedupe_key"),
                _dumps(vote.get("raw") or {}),
            ),
        )
        vote_id = cursor.lastrowid
        conn.commit()
        return {"id": vote_id, **vote}
    finally:
        conn.close()


def vote_exists(dedupe_key: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM uti_pine_votes WHERE dedupe_key = ? LIMIT 1", (dedupe_key,))
        return cursor.fetchone() is not None
    finally:
        conn.close()


def list_recent_votes(
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        sql = "SELECT * FROM uti_pine_votes"
        params: list[Any] = []
        clauses: list[str] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe:
            clauses.append("timeframe = ?")
            params.append(timeframe)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY received_at DESC LIMIT ?"
        params.append(limit)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["tps"] = _loads(item.pop("tps_json", None), [])
            item["raw"] = _loads(item.pop("raw_json", None), {})
            out.append(item)
        return out
    finally:
        conn.close()


def insert_decision(record: dict[str, Any]) -> dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        trade_number = next_trade_number(cursor)
        now = utc_now_iso_z()
        cursor.execute(
            """
            INSERT INTO uti_decisions (
                trade_number, symbol, timeframe, decision, technical_score, ai_confidence,
                news_score, macro_bias, geopolitical_risk,
                pine_json, analysts_json, bull_research, bear_research,
                trader, risk_json, entry, sl, tps_json, quantity, rr,
                paper_status, paper_trade_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade_number,
                record["symbol"],
                record["timeframe"],
                record["decision"],
                record.get("technical_score"),
                record.get("ai_confidence"),
                record.get("news_score"),
                record.get("macro_bias"),
                record.get("geopolitical_risk"),
                _dumps(record.get("pine") or {}),
                _dumps(record.get("analysts") or {}),
                record.get("bull_research"),
                record.get("bear_research"),
                record.get("trader"),
                _dumps(record.get("risk") or {}),
                record.get("entry"),
                record.get("sl"),
                _dumps(record.get("tps") or []),
                record.get("quantity"),
                record.get("rr"),
                record.get("paper_status") or "none",
                _dumps(record.get("paper_trade") or {}),
                now,
            ),
        )
        decision_id = cursor.lastrowid
        conn.commit()
        return serialize_decision_row(
            {
                "id": decision_id,
                "trade_number": trade_number,
                "created_at": now,
                **record,
            }
        )
    finally:
        conn.close()


def serialize_decision_row(row: dict[str, Any]) -> dict[str, Any]:
    trade_number = int(row.get("trade_number") or 0)
    paper = row.get("paper_trade") if isinstance(row.get("paper_trade"), dict) else _loads(row.get("paper_trade_json"), {})
    pip_plan = row.get("pip_plan") if isinstance(row.get("pip_plan"), dict) else (paper or {}).get("pip_plan")
    quality = row.get("signal_quality") if isinstance(row.get("signal_quality"), dict) else (paper or {}).get("signal_quality")
    show_signal = row.get("show_signal")
    if show_signal is None and quality is not None:
        show_signal = bool(quality.get("show_signal"))
    decision = row.get("decision")
    signal_label = row.get("signal_label") or (quality or {}).get("label") or decision
    return {
        "id": row.get("id"),
        "trade_number": trade_number,
        "trade_label": f"TRADE #{trade_number:06d}",
        "symbol": row.get("symbol"),
        "timeframe": row.get("timeframe"),
        "decision": decision,
        "signal_label": signal_label,
        "show_signal": bool(show_signal) if show_signal is not None else (str(decision).upper() in {"BUY", "SELL"}),
        "good_trade": bool(row.get("good_trade") if row.get("good_trade") is not None else (quality or {}).get("good_trade")),
        "signal_quality": quality,
        "raw_decision": row.get("raw_decision"),
        "technical_score": row.get("technical_score"),
        "ai_confidence": row.get("ai_confidence"),
        "news_score": row.get("news_score"),
        "macro_bias": row.get("macro_bias"),
        "geopolitical_risk": row.get("geopolitical_risk"),
        "pine": row.get("pine") if isinstance(row.get("pine"), dict) else _loads(row.get("pine_json"), {}),
        "analysts": row.get("analysts") if isinstance(row.get("analysts"), dict) else _loads(row.get("analysts_json"), {}),
        "bull_research": row.get("bull_research"),
        "bear_research": row.get("bear_research"),
        "trader": row.get("trader"),
        "risk": row.get("risk") if isinstance(row.get("risk"), dict) else _loads(row.get("risk_json"), {}),
        "entry": row.get("entry"),
        "sl": row.get("sl"),
        "tps": row.get("tps") if isinstance(row.get("tps"), list) else _loads(row.get("tps_json"), []),
        "quantity": row.get("quantity"),
        "rr": row.get("rr"),
        "pip_plan": pip_plan,
        "consensus": row.get("consensus"),
        "consensus_reason": row.get("consensus_reason"),
        "paper_status": row.get("paper_status"),
        "paper_trade": paper,
        "how_it_works": row.get("how_it_works"),
        "created_at": row.get("created_at"),
    }


def list_decisions(limit: int = 50, symbol: str | None = None) -> list[dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if symbol:
            cursor.execute(
                "SELECT * FROM uti_decisions WHERE symbol = ? ORDER BY trade_number DESC LIMIT ?",
                (symbol, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM uti_decisions ORDER BY trade_number DESC LIMIT ?",
                (limit,),
            )
        return [serialize_decision_row(dict(row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def get_decision(trade_number: int) -> dict[str, Any] | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM uti_decisions WHERE trade_number = ?", (trade_number,))
        row = cursor.fetchone()
        return serialize_decision_row(dict(row)) if row else None
    finally:
        conn.close()


def get_settings() -> dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT key, value FROM uti_settings")
        rows = cursor.fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def set_settings(updates: dict[str, str]) -> dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        now = utc_now_iso_z()
        for key, value in updates.items():
            cursor.execute("SELECT key FROM uti_settings WHERE key = ?", (key,))
            if cursor.fetchone():
                cursor.execute(
                    "UPDATE uti_settings SET value = ?, updated_at = ? WHERE key = ?",
                    (str(value), now, key),
                )
            else:
                cursor.execute(
                    "INSERT INTO uti_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, str(value), now),
                )
        conn.commit()
        return get_settings()
    finally:
        conn.close()
