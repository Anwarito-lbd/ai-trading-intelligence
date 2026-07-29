"""Ensure a local paper agent exists with the configured starting cash ($100 default)."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEMO_NAME = os.getenv("UTI_DEMO_AGENT_NAME", "DemoTrader")
DEMO_EMAIL = os.getenv("UTI_DEMO_AGENT_EMAIL", "demo@example.com")
DEMO_PASSWORD = os.getenv("UTI_DEMO_AGENT_PASSWORD", "DemoPass123!")


def _starting_cash() -> float:
    return float(os.getenv("UTI_PAPER_STARTING_CASH", "100"))


def ensure_demo_agent() -> dict | None:
    """Create/reset DemoTrader for paper trading. Safe to call on every API startup."""
    if os.getenv("UTI_SEED_DEMO_AGENT", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from database import get_db_connection
        from routes_shared import utc_now_iso_z
        from utils import hash_password

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            starting = _starting_cash()
            reset = os.getenv("UTI_RESET_PAPER_BALANCE", "false").strip().lower() in {
                "1", "true", "yes", "on"
            }
            cursor.execute("SELECT id, name, token, cash FROM agents WHERE TRIM(name) = ?", (DEMO_NAME,))
            row = cursor.fetchone()
            if row:
                agent_id = int(row["id"])
                if reset or float(row["cash"] or 0) < 0:
                    # Clear oversized/broken paper book and restore balance
                    cursor.execute("DELETE FROM positions WHERE agent_id = ?", (agent_id,))
                    cursor.execute(
                        "UPDATE agents SET cash = ?, updated_at = ? WHERE id = ?",
                        (starting, utc_now_iso_z(), agent_id),
                    )
                    conn.commit()
                    logger.info("Reset paper agent %s cash=$%s", DEMO_NAME, starting)
                os.environ.setdefault("UTI_PAPER_AGENT_ID", str(agent_id))
                return {
                    "id": agent_id,
                    "name": row["name"],
                    "token": row["token"],
                    "cash": starting if (reset or float(row["cash"] or 0) < 0) else float(row["cash"]),
                    "created": False,
                }

            import secrets

            token = secrets.token_urlsafe(32)
            now = utc_now_iso_z()
            cursor.execute(
                """
                INSERT INTO agents (name, email, password_hash, token, cash, deposited, points, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    DEMO_NAME,
                    DEMO_EMAIL,
                    hash_password(DEMO_PASSWORD),
                    token,
                    starting,
                    starting,
                    100,
                    now,
                    now,
                ),
            )
            agent_id = cursor.lastrowid
            conn.commit()
            os.environ.setdefault("UTI_PAPER_AGENT_ID", str(agent_id))
            logger.info("Seeded paper agent %s id=%s cash=$%s", DEMO_NAME, agent_id, starting)
            return {
                "id": agent_id,
                "name": DEMO_NAME,
                "token": token,
                "cash": starting,
                "created": True,
            }
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to seed paper agent")
        return None
