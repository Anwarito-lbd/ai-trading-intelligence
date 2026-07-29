# Pine webhook wrappers

Keep each TradingView indicator as a **separate** chart/alert.
Do **not** merge the five scripts into one Pine file.

**TradingView Pro setup (webhooks):** see [`pine_wrappers/TRADINGVIEW_SETUP.md`](../pine_wrappers/TRADINGVIEW_SETUP.md)  
API helper: `GET /api/uti/webhooks/setup`

Original third-party scripts live in gitignored `private/pine/`.
These wrappers document the **alert message** each chart should send to:

`POST /api/webhooks/pine/{indicator_id}?secret=YOUR_WEBHOOK_SECRET`

## Indicator IDs

| ID | Source |
|----|--------|
| `triple_confluence` | Triple Confluence Navigator |
| `sfx_algo` | Flux Charts SFX Algo 2.2.1 |
| `smart_trader` | Smart Trader EP03 |
| `swing_volume` | Swing Volume Profile Pro |
| `money_algorithm` | Money Algorithm |

## Canonical JSON (preferred)

```json
{
  "indicator_id": "triple_confluence",
  "symbol": "XAUUSD",
  "timeframe": "15",
  "side": "BUY",
  "strength": 0.8,
  "entry": 4075.2,
  "sl": 4067.0,
  "tps": [4083.0, 4091.0],
  "bar_time": "{{timenow}}"
}
```

Use TradingView `{{close}}` so entry is the real chart price. The server also
cross-checks against live GC=F and overrides stale demo prices.

See each `*.alert.txt` file for TradingView alert message templates.
