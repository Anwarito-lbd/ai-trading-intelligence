# Vendored / sidecar dependencies

| Path | Upstream | License | How we use it |
|------|----------|---------|---------------|
| `packages/tradingagents` | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | Optional LangGraph multi-agent brain (`TRADINGAGENTS_ENABLED=true`) |
| `packages/kronos` | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | MIT | Optional K-line forecast (`KRONOS_ENABLED=true`) |
| `packages/mirofish` | [666ghj/MiroFish](https://github.com/666ghj/MiroFish) | **AGPL-3.0** | Sidecar swarm simulator only — run separately on `:5001`; our app talks HTTP (`MIROFISH_ENABLED=true`) |
| WorldMonitor | [koala73/worldmonitor](https://github.com/koala73/worldmonitor) | **AGPL-3.0** | **Not vendored.** Remote REST/SDK via `WORLDMONITOR_API_KEY` |

After clone:

```bash
git submodule update --init --recursive
```

## AGPL boundary

Do **not** import MiroFish or WorldMonitor Python/TS modules into the AI-Trader process.
Run them as separate services (or call their public APIs) so this MIT/Apache app stays license-clean.

### MiroFish sidecar (optional)

```bash
cd packages/mirofish
cp .env.example .env   # add LLM_API_KEY + ZEP_API_KEY
npm run setup:all
npm run backend        # http://127.0.0.1:5001
```

Then set in AI-Trader `.env`:

```
MIROFISH_ENABLED=true
MIROFISH_API_BASE_URL=http://127.0.0.1:5001
```

V1 still works with stubs when MiroFish / WorldMonitor / Kronos are offline.
