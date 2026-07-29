# TradingView Pro → UTI webhooks

You need **TradingView Pro** (or higher) for webhook alerts.

## 1. Expose the API

TradingView cannot reach `localhost`. Tunnel it:

```bash
ngrok http 8000
# copy the https URL, e.g. https://abc123.ngrok-free.app
```

## 2. Get your 5 webhook URLs

```
GET https://YOUR_NGROK_HOST/api/uti/webhooks/setup?public_base=https://YOUR_NGROK_HOST
```

Or locally:

```
GET http://127.0.0.1:8000/api/uti/webhooks/setup
```

## 3. Create 5 alerts (one per Pine script)

| Indicator | Path |
|-----------|------|
| Triple Confluence | `/api/webhooks/pine/triple_confluence?secret=SECRET` |
| SFX Algo | `/api/webhooks/pine/sfx_algo?secret=SECRET` |
| Smart Trader | `/api/webhooks/pine/smart_trader?secret=SECRET` |
| Swing Volume | `/api/webhooks/pine/swing_volume?secret=SECRET` |
| Money Algorithm | `/api/webhooks/pine/money_algorithm?secret=SECRET` |

For each alert on TradingView:

1. Add the indicator to the chart (e.g. XAUUSD, 15m)
2. Create Alert → condition = that script’s signal
3. **Webhook URL** = the URL from step 2 for that indicator
4. **Message** = JSON using `{{ticker}}`, `{{interval}}`, `{{close}}`, `{{timenow}}`

Example message:

```json
{"indicator_id":"triple_confluence","symbol":"{{ticker}}","timeframe":"{{interval}}","side":"BUY","strength":0.85,"entry":{{close}},"sl":0,"tps":[],"bar_time":"{{timenow}}"}
```

Create a matching SELL alert (or use the script’s built-in `alert()` JSON if it already emits side).

Secret default: `UTI_WEBHOOK_SECRET=dev-webhook-secret` (change in `.env` for production).

## 4. What happens live

```
TradingView alert
  → Pine webhook vote
  → confluence (≥3 agree)
  → WorldMonitor + MiroFish + Kronos + Ollama desk
  → risk gate (sized to your $100 paper cash)
  → paper fill at live market price (GC=F for XAUUSD)
```

## 5. Paper balance

Default paper agent **DemoTrader** starts at **$100** (`UTI_PAPER_STARTING_CASH=100`).

Login: `DemoTrader` / `DemoPass123!`  
Command Center: `/command-center`

This is **paper** capital (`UTI_PAPER_ONLY=true`). Broker live trading is not enabled until V4.
