# Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 25). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 25.3".

## Story

As the bot operator,
I want the trading runtime wrapped as one context whose aggregates make the paper/real split a type and whose only view of Nautilus is a strategy-scoped reader,
So that a config key or a list reorder can never promote a bot to real money, and the TUI's status and history contracts stay exactly as they are.

## Acceptance Criteria

1. **Given** `live_paper/{config,node,strategy,bot_status,trade_history,fills_store,venues}.py`
**When** the story ships
**Then** `platform/bots/` holds `domain/` (`Bot` with `BotId == order_id_tag`, the bounded `Incident` list and heartbeat state; `FillLedger` with per-fill realized PnL and the day/week/month/all buckets of AD-10; `PaperFleet` and `ExecBot` as distinct aggregate types), `application/` (`supervise` — status heartbeat and `bots:control` handling; `history` — the refresh cycle), `infrastructure/` (`nautilus_host.py` building the one `TradingNode` per process with one data + Sandbox exec client per venue from the `VENUES` table; `cache_reader.py` filtering `cache.positions_open/closed(strategy_id=...)`; `fills_store.py`; `redis.py`; `config.py` with the two loaders, `load_paper_config` still rejecting a `mode` key and `ExecConfig.mode` still validated against `environment`) and `strategies/` (`DummyStrategy`, framework code); no module outside `bots/infrastructure/nautilus_host.py` imports `TradingNode` (asserted by `test_boundaries.py`)

2. **Given** AD-10/AD-11's wire contracts
**When** the story ships
**Then** `bots:status`, `bots:control`, `bots:history:*` and `bots:incidents:*` payloads are byte-identical (replay tests against recorded messages), `bot_tui` needs no change, `docker-compose.yml`'s `live-paper` service runs `python -m bots` with the same env and mounts (`./data/live_paper:/app/live_paper/data` keeps the container path), and `live_paper.dockerfile` `COPY`s `bots`, `kernel`, `observability` (proven by `test_images.py`)

3. **Given** MR2 and MR4
**When** the story is merged
**Then** `live_paper` is a pure re-export shim package with `REMOVE_AFTER = "26-1-..."`, the `test-live-paper` Makefile target runs `bots/tests` (the two host-dependent `test_node.py` tests stay deselected there with the reason recorded), `docs/BOT_OPERATIONS.md`, `live_paper/README.md` (moved to `bots/README.md`) and `platform/CLAUDE.md`'s `live_paper` exception clause cite `bots/`

## Tasks / Subtasks

- [ ] Task 1 — `platform/bots/` (AC: #1)
  - [ ] `domain/bot.py` (`Bot`: `BotId`, mode label, running flag, bounded `Incident` list (`_MAX_INCIDENTS = 50`, transitions from `bot_status.py:81-131`), heartbeat state), `domain/fill_ledger.py` (`FillLedger`: per-fill realized PnL, win-rate, day/week/month/all buckets from `trade_history.py:199-241` + `fills_store` query semantics), `domain/config.py` (`PaperFleet(BotConfig list, venue pools)`, `ExecBot(ExecConfig)`; `BotConfig`, `VenuePaperConfig` as value objects).
  - [ ] `application/supervise.py` (`build_status`, heartbeat loop, control loop from `bot_status.py`), `application/history.py` (refresh cycle from `trade_history.py`). `infrastructure/nautilus_host.py` (`build_node`, `VenueSpec`/`VENUES` from `venues.py`, `_paper_venue_clients`), `infrastructure/cache_reader.py` (strategy-scoped reads used by both supervise and history), `infrastructure/fills_store.py`, `infrastructure/redis.py`, `infrastructure/config.py` (`load_paper_config`, `load_real_money_config`, `resolve_config` unchanged in behaviour). `strategies/dummy.py` (`DummyStrategy`, `DummyStrategyConfig`). `bots/__main__.py` = `node.py:main`.
  - [ ] Boundary: only `bots/infrastructure/nautilus_host.py` imports `TradingNode`/`TradingNodeConfig`.
- [ ] Task 2 — wire contracts (AC: #2)
  - [ ] Record `bots:status`, `bots:history:*`, `bots:incidents:*` payloads from `live_paper/tests` fixtures; replay tests assert identity. Compose `live-paper` `command: python3 -m bots` (check the current command form), same env/mounts; `live_paper.dockerfile` `COPY platform/bots ./bots` + `kernel` + `observability` (`test_images.py`).
- [ ] Task 3 — shims, lists, docs (AC: #3)
  - [ ] `live_paper/` shim package (`REMOVE_AFTER = "26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place"`); Makefile `test-live-paper` → `bots/tests` (deselect the two host-dependent `test_node.py` tests with the reason in a comment; `REDIS_URL=redis://127.0.0.1:16379` note from memory); `docs/BOT_OPERATIONS.md`, `bots/README.md`, `bots/DEPLOY_CHECKLIST.md`, `platform/CLAUDE.md` `live_paper` exception clause → `bots/`.

## Dev Notes

Bots' wire contracts (AD-10/AD-11) are frozen; this is a package reshape plus type-level modelling of the paper/non-paper split (`ExecConfig.mode` stays a validated value inside `ExecBot`, Known limit per AD-D15). Host test gotchas from memory: `REDIS_URL=redis://127.0.0.1:16379`, deselect the two host-dependent `test_node.py` tests, never `pkill -f pytest` from a tool call.

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** Frozen for the whole migration: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`), the SQLite/TOML store schemas, compose service names, env vars, the `platform/data/` bind mounts. A replay/fixture test proving a payload or file is byte-identical before and after the move is the standard evidence.
- **Shims.** The old import path stays as a pure re-export: `from <new> import <names>` + `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER = "<story key>"`. It defines nothing (a copied class body would register a second Arrow class and break `is` dispatch). Update every in-repo caller in the same story; a `DeprecationWarning` in the test run is a failure (TEST-04). `platform/tests/test_namespace.py` asserts `old.X is new.X`.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` (from 23.1) must pass.
- **Layering (AD-D2):** `domain/` imports only stdlib, `kernel/` and `nautilus_trader.model`/`core` types — no I/O, asyncio, Redis, SQLite, Parquet or the Nautilus runtime; `application/` holds `typing.Protocol` ports, services and the asyncio loops; `infrastructure/` implements ports and is imported only by the composition root. No module-level mutable runtime state (AD-D10). Every aggregate/port docstring names the invariant it protects (DESIGN-01).
- **Parent spine:** when this story resolves one of its Deferred items, strike it there with a `[amended <date>: Story <n>]` note (MR14).
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07 (no silent skips; `observability.error_ledger.record`), TEST-01..04 (real Nautilus objects, no mocks of internals, warnings are failures), READ-03 (type hints, mypy), SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01 (never touch `nautilus_trader/` or `crates/`).
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: spine "Structural Seed". Working dir `platform/` (renamed from `troll/` on 2026-09-21; historical docs cite `troll/`). Durable stores under `platform/data/`, never under a package.
- `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix (contexts are top-level packages with `platform/` on `sys.path`).

### References

- Spine sections: AD-D15, AD-10, AD-11 (inherited)
- Review findings that shaped this story: reviews/review-rubric.md H1
- Code: `live_paper/{config,node,strategy,bot_status,trade_history,fills_store,venues}.py`, `docker-compose.yml:227-285`, `live_paper.dockerfile`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
