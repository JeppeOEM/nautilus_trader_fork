---
project_name: 'nautilus_trader_fork'
user_name: 'Mrqdt'
date: '2026-07-01'
sections_completed: ['Technology Stack & Versions']
existing_patterns_found: 22
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

- Python 3.12–3.14, `nautilus_trader` 1.229.0 used strictly as a **library** (never `TradingNode`/`Strategy`/`DataEngine` runtime) in `troll/`
- Dashboard stack: aiohttp (SSE server) + plotly (charts) + redis (pub/sub for live 1s data)
- Deployment: two-image Docker split — `nautilus-trader-base` (rebuilt rarely, core/deps only) + thin `collector.dockerfile` layered on top (bakes in `troll/dydx_collector/`, rebuilds in seconds)
- **Rebuild-order constraint**: rebuild the base image before the thin image whenever `nautilus_trader` core/deps change, or the thin image silently layers onto a stale base
- **Version-pin discipline**: `nautilus_trader` is pinned at 1.229.0 deliberately; bumping requires re-validating the PyO3 dYdX client bindings (precision bugs are version-sensitive)
- **Price/Quantity re-stamping rule**: never use `Price(decimal, precision)` / `Quantity(decimal, precision)` to change a value's precision — it has a real bug that silently corrupts values via an internal float64 round-trip for some decimal/precision combos (e.g. `Price(Decimal("61090.59855"), 16)` → `61090.5985500000026624`). Always use `Decimal.scaleb(new_precision)` + `Price.from_raw()`/`Quantity.from_raw()` instead — exact integer arithmetic, no float, no rounding.

## Critical Implementation Rules

_Documented after discovery phase_
