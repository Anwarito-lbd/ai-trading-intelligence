"""Market scanner for the full UTI watchlist.

Runs TradingAgents + WorldMonitor + MiroFish + Kronos (+ Groq) consensus.
Only Telegram-notifies on high-quality setups — you confirm on TradingView.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from decisions.orchestrator import run_decision_cycle
from uti_agents.telegram_notify import format_setup_alert, send_telegram, telegram_configured

logger = logging.getLogger(__name__)

DEFAULT_WATCHLIST = [
    "XAUUSD",  # gold
    "XAGUSD",  # silver
    "NAS100",  # nasdaq 100
    "US30",    # dow jones
    "SPX500",  # S&P 500
    "USOIL",   # WTI crude
    "EURUSD",  # euro / dollar
]

_scan_lock = threading.Lock()
_scan_thread: threading.Thread | None = None
_scan_stop = threading.Event()
_last_scan: dict[str, Any] = {"status": "idle", "results": []}
_last_alert_key: dict[str, float] = {}


def watchlist() -> list[str]:
    raw = os.getenv("UTI_SCAN_SYMBOLS", "").strip()
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()]
    return list(DEFAULT_WATCHLIST)


def scan_markets(
    *,
    symbols: list[str] | None = None,
    timeframe: str | None = None,
    notify: bool = True,
    paper: bool = False,
) -> dict[str, Any]:
    """Scan watchlist once. Notify Telegram only for good_trade setups."""
    tf = timeframe or os.getenv("UTI_SCAN_TIMEFRAME", "30")
    syms = symbols or watchlist()
    cooldown = float(os.getenv("UTI_ALERT_COOLDOWN_SECONDS", "1800"))  # 30m default
    results: list[dict[str, Any]] = []
    alerts_sent = 0

    for symbol in syms:
        try:
            out = run_decision_cycle(
                symbol=symbol,
                timeframe=tf,
                force=True,
                paper=paper,
            )
            item = {
                "symbol": symbol,
                "timeframe": tf,
                "status": out.get("status"),
                "show_signal": bool(out.get("show_signal")),
                "good_trade": bool(out.get("good_trade")),
                "signal_label": out.get("signal_label") or "NO SIGNAL",
                "quality": (out.get("signal_quality") or {}).get("quality_score"),
                "pip_plan": out.get("pip_plan"),
                "consensus": (out.get("consensus") or {}).get("reason"),
                "providers_used": out.get("providers_used"),
            }
            if out.get("good_trade") and out.get("show_signal") and notify:
                key = f"{symbol}:{tf}:{out.get('signal_label')}"
                now = time.time()
                if now - _last_alert_key.get(key, 0) >= cooldown:
                    if telegram_configured():
                        tg = send_telegram(format_setup_alert(out))
                        item["telegram"] = tg
                        if tg.get("ok"):
                            alerts_sent += 1
                            _last_alert_key[key] = now
                    else:
                        item["telegram"] = {"ok": False, "reason": "telegram_not_configured"}
                else:
                    item["telegram"] = {"ok": False, "reason": "cooldown"}
            results.append(item)
        except Exception as exc:
            logger.exception("scan failed for %s", symbol)
            results.append({"symbol": symbol, "status": "error", "error": str(exc)})

    summary = {
        "status": "ok",
        "timeframe": tf,
        "scanned": len(syms),
        "signals": [r for r in results if r.get("good_trade")],
        "alerts_sent": alerts_sent,
        "telegram_configured": telegram_configured(),
        "paper_fills": paper,
        "results": results,
        "note": "Alerts are notify-only — confirm on TradingView before trading.",
    }
    global _last_scan
    _last_scan = {**summary, "finished_at": time.time()}
    return summary


def _loop() -> None:
    interval = float(os.getenv("UTI_SCAN_INTERVAL_SECONDS", "900"))  # 15 min
    # Delay first scan so Render health checks pass before heavy Kronos/LLM work
    initial_delay = float(os.getenv("UTI_SCAN_INITIAL_DELAY_SECONDS", "45"))
    logger.info(
        "UTI market scanner started interval=%ss delay=%ss symbols=%s",
        interval,
        initial_delay,
        watchlist(),
    )
    if initial_delay > 0 and _scan_stop.wait(initial_delay):
        logger.info("UTI market scanner stopped before first scan")
        return
    while not _scan_stop.is_set():
        try:
            scan_markets(notify=True, paper=False)
        except Exception:
            logger.exception("scanner loop error")
        _scan_stop.wait(interval)
    logger.info("UTI market scanner stopped")


def start_scanner_background() -> dict[str, Any]:
    global _scan_thread
    with _scan_lock:
        if _scan_thread and _scan_thread.is_alive():
            return {"running": True, "already": True}
        _scan_stop.clear()
        _scan_thread = threading.Thread(target=_loop, name="uti-scanner", daemon=True)
        _scan_thread.start()
        return {"running": True, "already": False, "symbols": watchlist()}


def stop_scanner_background() -> dict[str, Any]:
    _scan_stop.set()
    return {"running": False}


def scanner_status() -> dict[str, Any]:
    alive = bool(_scan_thread and _scan_thread.is_alive())
    return {
        "running": alive,
        "symbols": watchlist(),
        "timeframe": os.getenv("UTI_SCAN_TIMEFRAME", "30"),
        "interval_seconds": float(os.getenv("UTI_SCAN_INTERVAL_SECONDS", "900")),
        "telegram_configured": telegram_configured(),
        "last_scan": {
            k: _last_scan.get(k)
            for k in ("status", "scanned", "alerts_sent", "signals", "finished_at", "note")
        },
    }
