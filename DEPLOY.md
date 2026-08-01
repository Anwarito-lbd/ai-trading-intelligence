# Deploy UTI (always-on alerts) — free / cheap hosts

## Why not Vercel?

Vercel is great for **frontends / serverless**. This stack needs:
- a long-running FastAPI process
- a market scanner every ~15 minutes
- optional Kronos / Python ML

→ **Do not host the API on Vercel.**  
Optional: host only `service/frontend` on Vercel pointing at your API URL.

## Best free options

| Host | Fit | Always-on? | Notes |
|------|-----|------------|-------|
| **Render free** | Best simple Docker deploy | ❌ spins down after ~15m idle | Use free cron to wake + scan |
| **Fly.io** free allowance | Docker | Limited | Good if you know Fly |
| **Oracle Cloud free ARM VM** | Best true 24/7 free | ✅ | Free forever VM; you manage it |
| **Railway** | Easy | Trial credits | Not permanently free |
| **Hugging Face Spaces** | Docker/Gradio | Sleeps on free | Possible but awkward for API |

**Recommended free combo:** Render free + [cron-job.org](https://cron-job.org) every 10 minutes → `POST /api/uti/scan/run?notify=true`  
That wakes the service and **queues** a background gold scan (HTTP returns immediately — waiting for the desk caused 502s on free Render). Telegram only on good setups.

Use **Groq** in the cloud (`UTI_LLM_PROVIDER=groq`) — free Ollama is too heavy for free tiers.

## Deploy on Render (free)

1. Push this repo to GitHub (already: `ai-trading-intelligence`).
2. [Render Dashboard](https://dashboard.render.com) → **New** → **Blueprint** → select repo (`render.yaml`).
3. Set env secrets:
   - `GROQ_API_KEY` (free at console.groq.com)
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
   - optional: `FINNHUB_API_KEY`, `NEWS_API_KEY`, `WORLDMONITOR_API_KEY`
4. Deploy. Health: `https://YOUR-APP.onrender.com/health`
5. Create cron-job.org job every 10–15 min:
   ```
   POST https://YOUR-APP.onrender.com/api/uti/scan/run?timeframe=30&notify=true
   ```

Or Docker manually:

```bash
docker build -t uti .
docker run -p 8000:8000 \
  -e GROQ_API_KEY=... \
  -e TELEGRAM_BOT_TOKEN=... \
  -e TELEGRAM_CHAT_ID=... \
  -e UTI_LLM_PROVIDER=groq \
  uti
```

## Extra free news (beyond WorldMonitor)

Already wired in `intel/free_news.py` and merged into every WorldMonitor brief:

| Source | Key needed? | What you get |
|--------|-------------|--------------|
| Google News RSS | No | Symbol search headlines |
| Reuters / CNBC RSS | No | Business feed |
| Yahoo via yfinance | No | Ticker news (already) |
| Finnhub | Free key | Company/market news |
| NewsAPI.org | Free key | Broader articles |
| Alpha Vantage | Free/demo | News sentiment |

Without WorldMonitor Pro you still get: price move + free RSS/headline sentiment → consensus.

## Frontend on Vercel (optional)

```bash
cd service/frontend
# set VITE_API_BASE=https://YOUR-APP.onrender.com/api
npm run build
# deploy dist/ to Vercel static
```

## True always-on (laptop off)

1. Free: Oracle ARM VM or Render+cron (cron keeps it warm).
2. Better: Render **paid starter** (~$7/mo) — no spin-down.
3. Never rely on your laptop.
