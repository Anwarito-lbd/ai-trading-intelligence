# Backend layout (Unified Trading Intelligence)

This maps to the AI-Trader `service/server` implementation:

| Planned path | Implementation |
|--------------|----------------|
| `backend/tradingview/webhook` | `service/server/webhooks/` + `routes_uti.py` |
| `backend/confluence/` | `service/server/confluence/` |
| `backend/ai/` | `packages/tradingagents` + `service/server/uti_agents/` |
| `backend/intelligence/` | `service/server/intel/` (WorldMonitor + MiroFish + Kronos) |
| `backend/risk/` | `service/server/risk/` |
| `backend/paper-trading/` | `service/server/decisions/orchestrator.py` |
| `pine/` | `pine_wrappers/` + gitignored `private/pine/` |
| `database/` | SQLite/Postgres via `service/server/database.py` (`uti_*` tables) |

All intelligence providers are **always integrated** into every decision cycle.
