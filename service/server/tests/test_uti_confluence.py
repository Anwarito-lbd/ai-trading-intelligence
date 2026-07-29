"""Tests for Unified Trading Intelligence confluence + webhook adapters."""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

from confluence.engine import ConfluenceEngine
from intel.symbols import normalize_symbol, normalize_timeframe
from webhooks.adapters import normalize_payload, parse_text_alert


class SymbolTests(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(normalize_symbol("GOLD"), "XAUUSD")
        self.assertEqual(normalize_symbol("OANDA:XAUUSD"), "XAUUSD")
        self.assertEqual(normalize_timeframe("15m"), "15")


class AdapterTests(unittest.TestCase):
    def test_triple_confluence_json(self):
        vote = normalize_payload(
            "triple_confluence",
            {
                "action": "long",
                "ticker": "XAUUSD",
                "tf": "15",
                "entry": "3350.2",
                "sl": "3343.7",
                "tp1": "3357",
                "tp2": "3364.5",
            },
            received_at="2026-07-28T14:30:00Z",
        )
        self.assertEqual(vote["side"], "BUY")
        self.assertEqual(vote["symbol"], "XAUUSD")
        self.assertEqual(vote["entry"], 3350.2)
        self.assertEqual(len(vote["tps"]), 2)

    def test_money_algorithm_text(self):
        vote = normalize_payload(
            "money_algorithm",
            "Strong Buy Signal Alert !!!\nXAUUSD",
            received_at="2026-07-28T14:30:00Z",
        )
        self.assertEqual(vote["side"], "BUY")
        self.assertGreaterEqual(vote["strength"], 0.8)

    def test_parse_text_prefers_first_side(self):
        parsed = parse_text_alert("Sell Signal then maybe buy later")
        self.assertEqual(parsed["side"], "SELL")


class ConfluenceTests(unittest.TestCase):
    def test_score_ready_with_three_buys(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        votes = [
            {
                "indicator_id": "triple_confluence",
                "symbol": "XAUUSD",
                "timeframe": "15",
                "side": "BUY",
                "strength": 0.9,
                "entry": 3350.0,
                "sl": 3343.0,
                "tps": [3357.0],
                "received_at": now,
                "bar_time": now,
            },
            {
                "indicator_id": "sfx_algo",
                "symbol": "XAUUSD",
                "timeframe": "15",
                "side": "BUY",
                "strength": 0.8,
                "entry": 3351.0,
                "sl": 3344.0,
                "tps": [3360.0],
                "received_at": now,
                "bar_time": now,
            },
            {
                "indicator_id": "money_algorithm",
                "symbol": "XAUUSD",
                "timeframe": "15",
                "side": "BUY",
                "strength": 0.85,
                "entry": 3350.5,
                "sl": 3343.5,
                "tps": [3364.0],
                "received_at": now,
                "bar_time": now,
            },
            {
                "indicator_id": "swing_volume",
                "symbol": "XAUUSD",
                "timeframe": "15",
                "side": "NEUTRAL",
                "strength": 0.5,
                "received_at": now,
                "bar_time": now,
            },
        ]
        result = ConfluenceEngine(min_votes=3).score(votes, symbol="XAUUSD", timeframe="15")
        self.assertEqual(result["direction"], "BUY")
        self.assertTrue(result["ready"])
        self.assertGreaterEqual(result["technical_score"], 60)

    def test_ttl_filters_stale(self):
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        votes = [
            {
                "indicator_id": "triple_confluence",
                "symbol": "XAUUSD",
                "timeframe": "15",
                "side": "BUY",
                "strength": 1.0,
                "received_at": stale,
                "bar_time": stale,
            }
        ]
        result = ConfluenceEngine(ttl_seconds=60, min_votes=1).score(votes)
        self.assertEqual(result["active_votes"], 0)


if __name__ == "__main__":
    unittest.main()
