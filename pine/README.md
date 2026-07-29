# Pine webhook wrappers

Keep each TradingView indicator as a **separate** chart/alert.
Do **not** merge the five scripts into one Pine file.

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
  "entry": 3350.2,
  "sl": 3343.7,
  "tps": [3357.0, 3364.5],
  "bar_time": "{{timenow}}"
}
```

See each `*.alert.txt` file for TradingView alert message templates.
