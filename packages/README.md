# Vendored dependencies

| Path | Upstream | License | How we use it |
|------|----------|---------|---------------|
| `packages/tradingagents` | [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | Apache-2.0 | Optional LangGraph multi-agent brain (`TRADINGAGENTS_ENABLED=true`) |
| WorldMonitor | [koala73/worldmonitor](https://github.com/koala73/worldmonitor) | **AGPL-3.0** | **Not vendored.** Remote REST/SDK only via `WORLDMONITOR_API_KEY` |
| Kronos | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | MIT | Optional (`KRONOS_ENABLED`); not loaded by default |

TradingAgents is a git submodule. After clone:

```bash
git submodule update --init --recursive
```

Installing the full TradingAgents Python package is optional for V1 — the app ships a heuristic multi-agent brain that mirrors the same roles for paper demos without LLM keys.
