"""Always-on Kronos K-line forecast analyst.

Uses packages/kronos (MIT) + Kronos-mini weights from Hugging Face when available.
Falls back to a live yfinance trend proxy so the Kronos role is never skipped.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from uti_agents.ticker_map import to_yfinance

logger = logging.getLogger(__name__)

_KRONOS_ROOT = Path(__file__).resolve().parents[3] / "packages" / "kronos"

_predictor = None
_predictor_error: str | None = None
_load_lock = threading.Lock()


def _load_predictor():
    global _predictor, _predictor_error
    if _predictor is not None:
        return _predictor
    with _load_lock:
        if _predictor is not None:
            return _predictor
        if _predictor_error and os.getenv("KRONOS_RETRY_LOAD", "false").strip().lower() not in {
            "1", "true", "yes", "on"
        }:
            return None
        if not _KRONOS_ROOT.exists():
            _predictor_error = "packages/kronos missing"
            return None
        try:
            if str(_KRONOS_ROOT) not in sys.path:
                sys.path.insert(0, str(_KRONOS_ROOT))
            import torch  # noqa: F401 — required by Kronos-mini
            from model import Kronos, KronosPredictor, KronosTokenizer

            tok_id = os.getenv("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-2k")
            model_id = os.getenv("KRONOS_MODEL", "NeoQuasar/Kronos-mini")
            device = os.getenv("KRONOS_DEVICE", "cpu")
            tokenizer = KronosTokenizer.from_pretrained(tok_id)
            model = Kronos.from_pretrained(model_id)
            _predictor = KronosPredictor(model, tokenizer, max_context=512, device=device)
            _predictor_error = None
            logger.info("Kronos predictor ready model=%s device=%s", model_id, device)
            return _predictor
        except Exception as exc:
            _predictor_error = str(exc)
            logger.warning("Kronos model load failed: %s", exc)
            return None


def _yf_ohlcv(symbol: str, lookback: int | None = None):
    """Build regular OHLCV + timestamps (daily preferred — fewer gaps on free hosts)."""
    import pandas as pd
    import yfinance as yf

    lookback = int(lookback or os.getenv("KRONOS_LOOKBACK", "64"))
    pred_len = int(os.getenv("KRONOS_PRED_LEN", "8"))
    yf_sym = to_yfinance(symbol)

    # Daily bars are more stable for Kronos-mini on Render (hourly gaps → tensor mismatch)
    hist = yf.Ticker(yf_sym).history(period="1y", interval="1d")
    freq = "D"
    if hist is None or hist.empty or len(hist) < 30:
        hist = yf.Ticker(yf_sym).history(period="60d", interval="1h")
        freq = "h"
    if hist is None or hist.empty or len(hist) < 30:
        return None, None, None, None

    hist = hist.tail(lookback).copy()
    hist = hist.rename(columns={c: c.lower() for c in hist.columns})
    for col in ("open", "high", "low", "close", "volume"):
        if col not in hist.columns:
            return None, None, None, None
    hist = hist.dropna(subset=["open", "high", "low", "close"])
    if len(hist) < 30:
        return None, None, None, None

    hist["amount"] = hist["close"] * hist["volume"].fillna(0)
    x_df = hist[["open", "high", "low", "close", "volume", "amount"]].astype(float).reset_index(drop=True)

    # Rebuild *regular* timestamps so Kronos stamp tensors always align with rows
    last_ts = hist.index[-1]
    if getattr(last_ts, "tz", None) is not None:
        last_ts = last_ts.tz_localize(None)
    x_timestamp = pd.Series(
        pd.date_range(end=pd.Timestamp(last_ts), periods=len(x_df), freq=freq)
    )
    y_timestamp = pd.Series(
        pd.date_range(start=pd.Timestamp(last_ts), periods=pred_len + 1, freq=freq)[1:]
    )
    return x_df, x_timestamp, y_timestamp, float(x_df["close"].iloc[-1])


def _bias_from_change(pct: float) -> tuple[str, float]:
    score = max(0.0, min(100.0, 50.0 + pct * 8.0))
    if pct > 0.15:
        return "BULLISH", score
    if pct < -0.15:
        return "BEARISH", score
    return "NEUTRAL", score


def _proxy_from_closes(symbol: str, x_df, last_close: float, reason: str) -> dict[str, Any]:
    closes = x_df["close"].astype(float)
    pct = float((closes.iloc[-1] / closes.iloc[0] - 1.0) * 100.0) if len(closes) > 1 else 0.0
    bias, score = _bias_from_change(pct / 3.0)
    return {
        "disabled": False,
        "symbol": symbol,
        "bias": bias,
        "score": round(score, 2),
        "last_close": round(last_close, 4),
        "change_pct": round(pct, 4),
        "reason": reason,
        "source": "kronos_yfinance_proxy",
        "package_present": True,
        "integrated": True,
        "stub": False,
    }


def get_kronos_forecast(symbol: str) -> dict[str, Any]:
    """Always-on forecast. Prefer real Kronos-mini; else yfinance trend proxy."""
    enabled = os.getenv("KRONOS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {
            "disabled": True,
            "symbol": symbol,
            "bias": "NEUTRAL",
            "score": 50.0,
            "reason": "KRONOS_ENABLED=false",
            "package_present": _KRONOS_ROOT.exists(),
            "integrated": False,
        }

    x_df = None
    last_close = None
    try:
        x_df, x_ts, y_ts, last_close = _yf_ohlcv(symbol)
        if x_df is not None and last_close:
            predictor = _load_predictor()
            if predictor is not None:
                try:
                    pred_df = predictor.predict(
                        df=x_df,
                        x_timestamp=x_ts,
                        y_timestamp=y_ts,
                        pred_len=len(y_ts),
                        T=1.0,
                        top_p=0.9,
                        sample_count=1,
                        verbose=False,
                    )
                    pred_close = float(pred_df["close"].iloc[-1])
                    pct = ((pred_close - last_close) / last_close) * 100.0
                    bias, score = _bias_from_change(pct)
                    return {
                        "disabled": False,
                        "symbol": symbol,
                        "bias": bias,
                        "score": round(score, 2),
                        "last_close": round(last_close, 4),
                        "pred_close": round(pred_close, 4),
                        "change_pct": round(pct, 4),
                        "reason": "kronos_mini_forecast",
                        "source": "kronos_mini",
                        "package_present": True,
                        "integrated": True,
                        "stub": False,
                    }
                except Exception as pred_exc:
                    logger.warning("Kronos predict failed for %s: %s — using proxy", symbol, pred_exc)
                    return _proxy_from_closes(
                        symbol,
                        x_df,
                        last_close,
                        f"kronos_proxy_after_predict_error ({pred_exc})",
                    )
            return _proxy_from_closes(
                symbol,
                x_df,
                last_close,
                f"kronos_proxy_yfinance ({_predictor_error})",
            )
    except Exception as exc:
        logger.warning("Kronos forecast failed for %s: %s", symbol, exc)
        if x_df is not None and last_close:
            return _proxy_from_closes(symbol, x_df, float(last_close), f"kronos_proxy_after_error ({exc})")

    return {
        "disabled": False,
        "symbol": symbol,
        "bias": "NEUTRAL",
        "score": 50.0,
        "reason": _predictor_error or "kronos_neutral_fallback",
        "source": "kronos_fallback",
        "package_present": _KRONOS_ROOT.exists(),
        "integrated": True,
        "stub": False,
    }
