# Vendored / sidecar dependencies

| Path | Upstream | License | How we use it |
|------|----------|---------|---------------|
| `packages/tradingagents` | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | Multi-agent brain roles inside the **unified** UTI decision cycle. `TRADINGAGENTS_FULL_GRAPH=true` runs real LangGraph; default folds TA roles via heuristic+LLM (Ollama/Groq). |
| `packages/kronos` | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | MIT | K-line forecast analyst in the **same** decision (`KRONOS_ENABLED=true`). |
| `packages/mirofish` | [666ghj/MiroFish](https://github.com/666ghj/MiroFish) | **AGPL-3.0** | Optional HTTP sidecar on `:5001`. In-process MiroFish-style swarm always runs via the shared LLM. |
| `sidecars/worldmonitor` | [koala73/worldmonitor](https://github.com/koala73/worldmonitor) | **AGPL-3.0** | **Gitignored clone** for local `npm run dev` / CLI. Runtime intel uses remote REST/MCP/SDK (`WORLDMONITOR_API_KEY`) — never import AGPL source into the app process. |

After clone:

```bash
git submodule update --init --recursive
```

## They work together (one pipeline)

`run_decision_cycle` always runs:

1. **Pine confluence** (5 separate webhook indicators)
2. **WorldMonitor** brief (SDK / MCP / live free fallback)
3. **MiroFish** swarm (in-process via Ollama/Groq; sidecar if up)
4. **Kronos** forecast (Kronos-mini or yfinance proxy)
5. **TradingAgents-style desk** → one BUY/SELL/WAIT + paper fill

Response includes `unified: true` and `providers_used: { pine, worldmonitor, mirofish, kronos, llm, tradingagents }`.

## Local LLM (Ollama)

Host install (recommended in Cloud Agents):

```bash
ollama serve
ollama pull llama3.2:1b
# .env
UTI_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
UTI_QUICK_MODEL=llama3.2:1b
```

Or TradingAgents' Docker profile:

```bash
cd packages/tradingagents
cp .env.example .env
docker compose --profile ollama run --rm tradingagents-ollama
```

## WorldMonitor sidecar (AGPL — not vendored)

```bash
git clone https://github.com/koala73/worldmonitor.git sidecars/worldmonitor
cd sidecars/worldmonitor && npm install && npm run dev
npx worldmonitor tools   # list MCP tools (no key)
# Set WORLDMONITOR_API_KEY for authenticated tools/call
```

## AGPL boundary

Do **not** import MiroFish or WorldMonitor modules into the AI-Trader process.
Call remote APIs / sidecars over HTTP so this MIT/Apache app stays license-clean.
