"""Ensure a local demo agent exists for paper-trading UI login."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEMO_NAME = os.getenv("UTI_DEMO_AGENT_NAME", "DemoTrader")
DEMO_EMAIL = os.getenv("UTI_DEMO_AGENT_EMAIL", "demo@example.com")
DEMO_PASSWORD = os.getenv("UTI_DEMO_AGENT_PASSWORD", "DemoPass123!")


def ensure_demo_agent() -> dict | None:
    """Create DemoTrader if missing. Safe to call on every API startup."""
    if os.getenv("UTI_SEED_DEMO_AGENT", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        from database import get_db_connection
        from routes_shared import utc_now_iso_z
        from utils import hash_password

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, name, token FROM agents WHERE TRIM(name) = ?", (DEMO_NAME,))
            row = cursor.fetchone()
            if row:
                return {"id": row["id"], "name": row["name"], "token": row["token"], "created": False}

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
                    100000.0,
                    0.0,
                    100,
                    now,
                    now,
                ),
            )
            agent_id = cursor.lastrowid
            conn.commit()
            logger.info("Seeded demo agent %s id=%s", DEMO_NAME, agent_id)
            return {"id": agent_id, "name": DEMO_NAME, "token": token, "created": True}
        finally:
            conn.close()
    except Exception:
        logger.exception("Failed to seed demo agent")
        return None
