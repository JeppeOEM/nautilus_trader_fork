---
baseline_commit: 8d0b73032cb88e528957ebd755c1802e93752b49
---

# Story 4.6: Durable bot trade/position history via Nautilus Cache

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the builder,
I want `live_paper`'s trade and position history to survive a restart and be queryable by something other than the running process itself,
so that both the web dashboard and the TUI can show real trade history without either one reimplementing it or reaching into Nautilus's internals directly.

## Acceptance Criteria

1. Given `troll/live_paper/node.py`'s `TradingNodeConfig`, when this story is implemented, then it constructs `CacheConfig(database=DatabaseConfig(type="redis", ...))` pointed at the existing Redis instance, so orders/positions/fills persist beyond the process's lifetime instead of defaulting to in-memory-only (FR27).
2. Given `live_paper` is the sole writer of this Cache-backed history, when any other module needs trade/PnL history, then it never reaches into the Redis-backed Cache's internal keys/msgpack encoding directly — `live_paper` exposes its own read surface over `cache.orders_closed()`/`cache.positions_closed()`, the same boundary discipline as the existing `bots:status`/`bots:control` channels.
3. Given the new read surface, when it is queried, then it returns individual fills (timestamp, side, price, qty, realized PnL) and can compute/return PnL aggregated by day for a given bot.
4. Given the read surface is unreachable (e.g. persistence not yet enabled elsewhere, or a query times out), when a caller queries it, then it fails in a way callers can detect and handle gracefully (e.g. a clear error/timeout), not a silent empty result indistinguishable from "no trades yet".

**Architecture correction to AC2 (binding — see References):** the epics.md text for AC2 also mentions `cache.position_snapshots()`. Architecture AD-10 (updated 2026-07-24, verified against nautilus_trader 1.229.0 source: `cache/cache.pyx`, `execution/config.py`, `execution/engine.pyx`) explicitly overrides this: **do not call `cache.position_snapshots()`.** It returns empty unless `ExecEngineConfig(snapshot_positions=True)` is also set, and even then only fires on NETTING-mode reopen/flip — not `DummyStrategy`'s simple open→close shape. `cache.orders_closed()`/`cache.positions_closed()` alone are sufficient: closed `Position` objects' `.events()`/`.realized_pnl` already carry full fill history. Do not add `ExecEngineConfig(snapshot_positions=True)` for this story.

**Second, superseding correction to AC2 (binding — found and resolved mid-implementation, see Dev Agent Record):** the guidance above (both the original epics.md text and the first AD-10 correction) still assumed history would be *read back out of Cache* on demand. That assumption doesn't hold either: `cache.positions_closed()` returns closed `Position` objects, but under `OmsType.NETTING` a position's ID is fixed as `{instrument_id}-{strategy_id}` for a strategy's whole lifetime — reopening a position overwrites that same `PositionId` in `Cache._positions` and discards the previous closed entry from `_index_positions_closed` (`cache.pyx`'s `_reopen_position`/`add_position`). A strategy that keeps trading the same instrument can therefore end with `cache.positions_closed()` reporting 0 or 1 closed positions no matter how many round trips it actually completed — reproduced empirically in this story's own test (`test_trade_history.py::test_fills_store_keeps_every_round_trip_that_cache_positions_closed_loses`). Neither `cache.orders_closed()` nor `cache.positions_closed()` is used by the implementation actually shipped: **fills are recorded event-sourced**, via a `strategy.msgbus.subscribe("events.order.{strategy_id}", ...)` handler that writes each `OrderFilled` to a shared SQLite store (`fills_store.py`) the instant it happens, filtering for `OrderFilled` and reading `Position.is_closed`/`.realized_pnl` off `strategy.cache.position(fill.position_id)` at that moment (confirmed via `engine.pyx` that Cache is already updated before this topic publishes). `compute_history()` then queries SQLite directly — no Cache reconstruction logic of any kind. See Task 2 below and the Dev Agent Record for the full decision trail (a `bmad-brainstorming` session, `_bmad-output/brainstorming/brainstorm-positions-closed-history-loss-2026-09-02/`).

## Tasks / Subtasks

- [x] **Task 1 — Enable Redis-backed Cache (AC1)**
  - [x] `build_node()` constructs `CacheConfig(database=DatabaseConfig(type="redis", host=..., port=...))`, host/port parsed from `_REDIS_URL` via `urlparse` (`node.py:114-120`) — not a second hardcoded literal.
  - [x] Every other `CacheConfig` field left at its default — no purge config added.
  - [x] `test_node.py::test_build_node_configures_redis_backed_cache` asserts the resulting `TradingNodeConfig.cache` shape.

- [x] **Task 2 — Read-surface module: `troll/live_paper/trade_history.py` + `troll/live_paper/fills_store.py` (AC2, AC3, AC4)** — **redesigned mid-implementation; see the second AC2 correction above.** What actually shipped:
  - [x] `fills_store.py` (new): shared SQLite store across every bot (`bot_id` column, one file — not per-bot, per explicit user direction during the brainstorm), plain `sqlite3`/no ORM, mirroring `ranking_engine/metrics_store.py`'s style exactly. `write_fill(...)` appends one row per fill (never rewritten). `recent_trades(bot_id, db_path, cutoff_ns, limit)` and `pnl_by_day(bot_id, db_path, cutoff_ns)` do the window filtering, 500-cap, and UTC-day bucketing **in SQL** (`ts / _NS_PER_DAY` integer division for the day bucket — no `datetime` parsing needed at all), not in Python.
  - [x] `trade_history.py` (rewritten): `subscribe(strategy, bot_id, db_path)` — `strategy.msgbus.subscribe("events.order.{strategy_id}", handler)`, filtering for `OrderFilled` in `_on_order_event()`. Verified directly against `execution/engine.pyx` that `Cache` is already updated (position closed/`realized_pnl` set) before this topic publishes, so `_closing_realized_pnl()` can safely read `strategy.cache.position(fill.position_id)` at handler time — same one-closing-fill-per-position `DummyStrategy` simplification as originally planned, just computed at write time instead of by replaying `Position.events()` after the fact.
  - [x] `compute_history(bot_id, range_name, now_ns, db_path) -> dict` is now thin glue over `fills_store`'s two query functions — no `Cache`/`strategy_id` parameter at all.
  - [x] AC4 (bad-cycle-must-not-overwrite-good-data) and the `_HISTORY_REFRESH_SECONDS = 30.0` Redis-republish timer are unchanged from the original plan — only the *source* `compute_history()` reads from changed (SQLite instead of Cache).
  - [x] "On each fill" refresh: the **write** to `fills_store` is on-fill (via `subscribe()`), same as AD-10 wanted — this fully closes the gap the original plan had deliberately left open (timer-only). The Redis **republish** (what bot_tui/dashboard actually read) is still on the 30s timer, not on every fill — unchanged scope cut, now doubly justified since durability no longer depends on it at all.

- [x] **Task 3 — Wire into `node.py` (AC1/AC2 integration)**
  - [x] `build_node()` schedules `trade_history.run(strategy, bot_id=config.bot_id, redis_url=_REDIS_URL, db_path=_FILLS_DB_PATH)` alongside `bot_status.run()`.
  - [x] `_FILLS_DB_PATH` (new, `node.py`) — `FILLS_DB_PATH` env override, default `_MODULE_DIR / "data" / "fills.db"`, mirroring `ranking_engine`'s `METRICS_DB_PATH` convention.
  - [x] `docker-compose.yml` **does change**, superseding the original "no compose changes" note: the `live-paper` service gains `FILLS_DB_PATH` env var and a `./live_paper/data:/app/live_paper/data` **directory** bind mount (not the bare file — SQLite WAL mode's `-wal`/`-shm` sidecars can't share a single-file mount, same documented gotcha as the `metrics.db` mounts above it in the same compose file). `/troll/live_paper/data/` added to `.gitignore`. Redis itself is unaffected — AD-10's "no new service/container" holds.

- [x] **Task 4 — Tests**
  - [x] New `troll/live_paper/tests/test_fills_store.py` (6 tests): pure-SQL logic against a `tmp_path` sqlite file, no Nautilus needed — round-trip, bot_id filtering, cutoff filtering, 500-cap ordering, day-bucket PnL sums, and the AD-10 sum-invariant (`sum(trades.realized_pnl) == sum(pnl_series.pnl)`).
  - [x] New `troll/live_paper/tests/test_trade_history.py` (4 tests), real `BacktestEngine` + real `DummyStrategy`, same harness precedent as `test_bot_status.py` but with a **deliberately oscillating** (triangle-wave) price path instead of a monotonic ramp, tuned (`trend_buy_threshold=0.58`, `trend_sell_threshold=0.48`, 120s run) to drive multiple full open→close→reopen round trips on one instrument — this is what actually proves the fix. The headline test, `test_fills_store_keeps_every_round_trip_that_cache_positions_closed_loses`, asserts `cache.positions_closed()` returns **0** (reproducing the bug directly, in a real backtest) while `fills_store` has all **7** fills across the run, 3 of them carrying non-`None` `realized_pnl`. Two more tests cover the AD-10 sum-invariant via `compute_history(range="all")` and window-filtering (`range="day"` with a far-future `now_ns` returns empty while `range="all"` doesn't). A fourth is a trivial non-`OrderFilled`-event-is-ignored check. The 500-cap itself is already covered in `test_fills_store.py` (no need to duplicate via a slow 500-fill backtest run).
  - [x] `test_node.py` already covered Task 1 (Redis Cache config) — untouched further by this task.
  - [x] Full regression, both locally and via the documented Docker command (`docker compose --profile live-paper run --rm --no-deps -e HOME=/tmp -e USER=collector live-paper python3 -m pytest live_paper/tests -q`, after `docker compose --profile live-paper build live-paper` to pick up the new files): **45 passed** (34 baseline + 6 + 4 + 1 net from Task 1's earlier addition), zero regressions. `ruff format --check` and `ruff check` clean except one `S608` (possible-SQL-injection) lint on `fills_store.py`'s `pnl_by_day()` f-string — a pre-existing, already-accepted pattern in this codebase (`ranking_engine/metrics_store.py` has the identical finding, unaddressed, for the same reason: the interpolated value is a fixed internal constant, never external input). `mypy` clean.
  - [x] Redis: **not exercised this session** (same disclosed gap as prior stories) — only `compute_history()`/`fills_store`'s pure logic and `trade_history.subscribe()`'s in-process msgbus wiring were exercised against a real Nautilus `BacktestEngine`; `trade_history.run()`'s actual Redis `SET`/reconnect path was never smoke-tested against a live Redis instance.

## Dev Notes

### Exact API signatures (verified against this repo's installed nautilus_trader 1.229.0 — no need to re-derive these)

- `CacheConfig` (`nautilus_trader/cache/config.py:23`): `database: DatabaseConfig | None = None`, `encoding: str = "msgpack"`, plus purge/capacity fields — all fine at defaults for this story.
- `DatabaseConfig` (`nautilus_trader/common/config.py:309`): `type: str = "redis"`, `host: str | None`, `port: int | None`, plus timeout/retry fields at sane defaults — only `type`/`host`/`port` need setting.
- `TradingNodeConfig` (`nautilus_trader/live/config.py:284`) inherits `cache: CacheConfig | None = None` from `NautilusKernelConfig` (`nautilus_trader/system/config.py:39`, field declared there, not redeclared in `TradingNodeConfig` itself — the docstring at `live/config.py:292-293` documents it but the field lives on the parent class).
- `Cache.positions_closed(venue=None, instrument_id=None, strategy_id=None, account_id=None, side=None) -> list[typing.Any]` (`python/nautilus_trader/common/__init__.pyi:538`) — pass `strategy_id=strategy.id`, same call shape `bot_status.py:81` already uses.
- `Position.events() -> list[OrderFilled]`, `Position.realized_pnl -> Money | None`, `Position.calculate_pnl(avg_px_open, avg_px_close, quantity) -> Money` (`python/nautilus_trader/model/__init__.pyi:4698-4780`) — `calculate_pnl` exists but is **not needed** for this story given the documented one-open-one-close simplification above; don't reach for it unless you're building the general partial-close case, which is explicitly out of scope.
- `OrderFilled`: `.ts_event: int`, `.order_side: OrderSide`, `.last_px: Price`, `.last_qty: Quantity` — no `.realized_pnl` (`python/nautilus_trader/model/__init__.pyi:3579-3639`).
- `ExecEngineConfig.snapshot_positions: bool = False` (`nautilus_trader/execution/config.py:95`) — leave unset (default `False`); do not enable, per the AC2 architecture correction above.

### Redis connection (reuse, don't reinvent)

Every other `troll/` module (`bot_status.py`, `ranking_engine/engine.py`, `dashboard.py`, `collector.py`, `bot_tui/*_state.py`) reads `REDIS_URL` env var, default `"redis://127.0.0.1:6379"`, via `aioredis.Redis.from_url(redis_url, decode_responses=True)`. `node.py:72` already defines `_REDIS_URL` this way. `docker-compose.yml` sets the same literal value for every service (confirmed: `grep -n REDIS_URL troll/docker-compose.yml`, all 5 occurrences identical). `DatabaseConfig` wants `host`/`port` separately, not a URL — parse `_REDIS_URL` with `urllib.parse.urlparse` rather than hardcoding a second copy of the default (see Task 1).

### Module boundary (AD-4/AD-8/AD-10)

`trade_history.py` lives in `troll/live_paper/` (the one `troll/` module where `TradingNode`/`Strategy` usage is sanctioned, per AD-8) and is structured exactly like `bot_status.py`: the only place that reads this running `Strategy`'s `Cache`/`Portfolio` for trade history, publishing a stable Redis contract (`bots:history:{bot_id}:{day,week,month,all}` **keys**, GET-only — not a pub/sub channel, unlike `bots:status`) that `bot_tui` (Story 4.7) and the web dashboard will read later. This story does **not** touch `bot_tui/` at all — Story 4.7 is a separate story. Do not add any `bot_tui` reader code here (scope discipline — see Story 4.5's own Dev Notes item 4 for the same YAGNI reasoning applied to a sibling story).

### Wire contract this story must produce exactly (binding — Story 4.7 will consume this verbatim)

Per AD-10 (`ARCHITECTURE-SPINE.md:148-153`): four Redis **keys** per bot (not channels), `bots:history:{bot_id}:day`, `:week`, `:month`, `:all`, each `{bot_id, range, updated_at, trades: [...], pnl_series: [...]}`. `trades[].ts`/`pnl_series[].period_start` are UNIX nanoseconds. `trades[].realized_pnl` is that fill's own value (not cumulative); `pnl_series[].pnl` is that bucket's own net value (not cumulative) — summing a range's `pnl_series` must equal that range's total realized PnL. Windows roll from "now" (never calendar-aligned). `trades` capped at 500 most recent regardless of range. Cross-key atomicity is explicitly not required (each key independently refreshed/timestamped; a few seconds of skew between `day` and `month` is accepted).

### Testing standards (from `troll/CLAUDE.md` / project-context.md, applied here)

TEST-01 requires tests for financial calculations — `compute_history()`'s realized-PnL-per-fill and PnL-by-day aggregation are exactly that; test them with real numbers, not just shape assertions. TEST-03: never mock `Cache`/`Position`/`Strategy` — the `BacktestEngine`+`DummyStrategy` harness `test_bot_status.py` already established is the correct (and only sanctioned) way to get a real populated `Cache` with real closed `Position`s for this story's tests too. No class-based tests, `pytest` only, one assertion per logical claim, `-> None` return types, `test_*.py` naming.

### Project Structure Notes

- **New production file**: `troll/live_paper/trade_history.py`.
- **Modified production files**: `troll/live_paper/node.py` (`CacheConfig`/`DatabaseConfig` added to `TradingNodeConfig`; `trade_history.run()` task scheduled alongside `bot_status.run()`).
- **New test file**: `troll/live_paper/tests/test_trade_history.py`.
- **Modified test file**: `troll/live_paper/tests/test_node.py` (Cache config assertions).
- **No changes** to `troll/live_paper/strategy.py`, `config.py`, `bot_status.py`, `bot_tui/`, `ml_signals/dashboard.py`, or `troll/docker-compose.yml` — this story is additive within `live_paper/` only (mirrors every prior Epic 4 story's own scope discipline, per Story 4.5's Project Structure Notes precedent).
- No conflicts found with the unified project structure — `trade_history.py` sits as a sibling to `bot_status.py`, same package, same naming register (`*_history`/`*_status` both describe a read/query surface, not a verb).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.6] original story definition (Given/When/Then ACs, verbatim) — the source of ACs 1-4 above.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-10] lines 138-157 — binding, more recent (2026-07-24 update, explicitly re-verified against installed nautilus_trader 1.229.0 source) than the epics.md AC2 text it partially supersedes (the `position_snapshots()` correction above); also the source of the exact `bots:history:*` wire contract, refresh cadence, windowing, capping, and PnL-cumulative-vs-per-item semantics.
- [Source: _bmad-output/implementation-artifacts/epic-4-context.md#Technical-Decisions] restates AD-10's wire contract in epic-summary form; confirms Story 4.7 (not this story) is the sole consumer of `bots:history:*` from `bot_tui`.
- [Source: troll/live_paper/node.py] full file read — `build_node()` (lines 75-138), `_REDIS_URL` (line 72), existing `bot_status.run()` scheduling pattern (lines 128-136) this story's Task 3 extends.
- [Source: troll/live_paper/bot_status.py] full file read — structural precedent this story's `trade_history.py` mirrors (pure `build_status()`/computation function + `run()` heartbeat-loop orchestration, `_parse_control_message`-style small pure helpers, reconnect-on-error `while True` pattern at `run()`'s outer level).
- [Source: troll/live_paper/strategy.py] full file read — `DummyStrategy._maybe_trade()` (lines 254-286) is the source of the "exactly one opening fill + one closing fill per closed Position" simplification documented above; confirmed no pyramiding/partial-close branch exists.
- [Source: troll/live_paper/tests/test_bot_status.py, conftest.py] full read — exact `BacktestEngine`/`DummyStrategy` test harness (`_engine()`, `_quotes_and_deltas()`, `_config()`, `_run_strategy()`) this story's `test_trade_history.py` should mirror (duplicate the small pieces needed rather than cross-import private helpers), plus the `_fresh_event_loop`/`_keep_nautilus_log_guard_alive` autouse fixtures already covering this story's new test file for free (same `live_paper/tests/` directory).
- [Source: python/nautilus_trader/common/__init__.pyi:400-585] `Cache.orders_closed`/`.positions_closed`/`.position_snapshots` exact signatures (verified live against the installed package, not assumed from memory).
- [Source: python/nautilus_trader/model/__init__.pyi:3579-3639, 4698-4780] `OrderFilled` and `Position` exact fields/methods used above.
- [Source: nautilus_trader/cache/config.py, nautilus_trader/common/config.py:309-370, nautilus_trader/system/config.py:39-121, nautilus_trader/live/config.py:284-313, nautilus_trader/execution/config.py:33-96] `CacheConfig`/`DatabaseConfig`/`NautilusKernelConfig`/`TradingNodeConfig`/`ExecEngineConfig` field definitions and docstrings.
- [Source: troll/docker-compose.yml] confirms `REDIS_URL` is identically `"redis://127.0.0.1:6379"` across all 5 existing services — no deployment change needed for this story.
- [Source: troll/CLAUDE.md] TEST-01/02/03, READ-01/02/03, DESIGN-01 (YAGNI — basis for skipping on-fill-triggered refresh, skipping `position_snapshots()`, skipping purge config).
- [Source: _bmad-output/implementation-artifacts/4-5-bot-detail-live-snapshot-view.md#Dev-Notes, #Dev-Agent-Record] precedent for this story's own Dev Notes structure (judgment calls called out explicitly), the disclosed-gap convention for anything not manually/interactively smoke-tested, and the exact `live_paper` regression command + last-known-green baseline (34 passed) this story's Task 4 re-confirms against.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5

### Debug Log References

### Completion Notes List

- **Tasks 1-3 done**: `node.py` wires `CacheConfig(database=DatabaseConfig(type="redis", ...))` parsed from `_REDIS_URL` (not a second hardcoded literal); `trade_history.py` written per spec (`compute_history()` + `run()`/`_history_loop()`/`_refresh_cycle()` mirroring `bot_status.py`); `trade_history.run()` scheduled alongside `bot_status.run()` in `build_node()`. `test_node.py` extended with a Cache-config assertion.
- **Task 4 (tests) BLOCKED — found a load-bearing correctness gap in the architecture, not a test-authoring problem.** While building the "drive a scenario with 2+ closed positions" test fixture, verified directly against this repo's `nautilus_trader/execution/engine.pyx` + `nautilus_trader/cache/cache.pyx` source (not assumed):
  - Under `OmsType.NETTING` (what `live_paper` uses), a position's ID is fixed as `{instrument_id}-{strategy_id}` for the strategy's entire lifetime — never a new ID per open/close cycle.
  - `Cache._positions` is a plain `dict[PositionId, Position]`. `_reopen_position()` (engine.pyx) calls `cache.snapshot_position()` on the old closed object (pickles it into a side-table reachable only via `cache.position_snapshots()` — the exact API this story's AC2 correction already forbids using) and then `add_position()` **overwrites** `self._positions[same_id]` with a brand-new Position object, discarding the old one from `_index_positions_closed` (`cache.pyx:2379`, `self._index_positions_closed.discard(position.id)  # Cleanup for NETTING reopen`).
  - Empirically reproduced: a `BacktestEngine` run with 2 real round-trips (open->close->reopen->close) on one instrument ends with `cache.positions_closed(strategy_id=...)` returning 0 or 1 entries, never 2 — the earlier closed position vanishes the instant it reopens. Confirmed there is no workaround via two `Strategy` instances sharing one `strategy_id` either — `Trader.add_strategy()` (`trading/trader.py:401`) hard-rejects duplicate strategy IDs.
  - **Impact beyond this story's test**: `compute_history()` as currently written (using `cache.positions_closed()`, per this story's original Dev Notes/AD-10 guidance) would silently drop every trade before a live bot's most recent reopen — directly contradicting AC1's "survive a restart" framing, since durability is pointless if the read surface itself only ever exposes the latest open-close cycle. This also retroactively means Story 4.4's `bot_status.build_status()`'s `closed_trades`/win-rate stats (which use the same `positions_closed()` call) are wrong for any bot that has traded more than once.
  - **Fix identified, not yet implemented**: rewrite `compute_history()` to walk `cache.orders_closed(strategy_id=...)` instead — closed *orders* are never overwritten/reused the way positions are, so they accumulate for the bot's full lifetime. Track running signed position qty locally, compute realized PnL per reducing (closing) fill. Still simple for `DummyStrategy`'s one-open-one-close shape; no need for full partial-close weighting.
  - **Resumed via a `bmad-brainstorming` session** (`_bmad-output/brainstorming/brainstorm-positions-closed-history-loss-2026-09-02/.memlog.md`) rather than picking directly from the three paused options. Constraint Mapping + Morphological Analysis + a PMI convergence pass converged on a fourth option none of the original three named: **event-sourced write-through SQLite**, not an `orders_closed()`-based Cache-reconstruction rewrite. Key reasoning from that session, in order:
  - The user's real constraint wasn't "must read from Cache" (never an AC requirement) but "prefer reuse over building new" — and `orders_closed()` still requires hand-rolled signed-qty/PnL-per-fill tracking, i.e. new app-side logic reading Cache, not less.
  - The codebase already has a established, boring precedent for exactly this shape of problem: `ranking_engine/metrics_store.py`'s plain-`sqlite3` file store. Reusing *that pattern* (not a Cache-reading technique) is the more honest interpretation of "prefer reuse."
  - Write-on-`OrderFilled` (via `strategy.msgbus.subscribe`) beats polling `orders_closed()` on the existing timer: it's event-sourced, so it structurally can never depend on how Nautilus internally represents position/order history at all — immune to *this* bug and any future Cache-shape surprise in the same family, not just a patch for the one bug found.
  - User correction after the session nominally wrapped: shared SQLite file across all bots (`bot_id` column), not one file per bot — simpler operationally (one mount, one connection lifecycle), and write volume at paper-trading/human timescale makes per-bot sharding unnecessary.
- Implementation matches the session's converged combo exactly (A1 event-triggered write + B2 shared file + C1 pure-SQL query + D1 unchanged Redis wire contract) — see rewritten Tasks 2-4 above for what shipped.
- This also means Story 4.4's `bot_status.py` `closed_trades`/win-rate stats (same `cache.positions_closed()` call, same bug) were **not** touched — out of this story's scope, flagged as a follow-up (also noted in the brainstorm session's synthesis). `deferred-work.md` should get an entry for this if the project maintains one; not added here since this session didn't check whether that ledger is still in active use for this repo.
  - **Follow-up fix applied (2026-09-02, same day):** `bot_status.build_status()` now reads `closed_trades`/`win_rate` from `fills_store.win_rate_stats(bot_id, db_path)` (new function, same file this story added) instead of `cache.positions_closed()`. `build_status()`/`_heartbeat_loop()`/`run()` all gained a `db_path` parameter, threaded from `node.py`'s existing `_FILLS_DB_PATH`. Regression test `test_build_status_closed_trades_survives_a_netting_reopen` seeds `fills_store` directly with 2 synthetic round trips while the driving strategy itself never traded (impossible thresholds), proving `closed_trades`/`win_rate` can only have come from `fills_store`, not `Cache`. 48/48 `live_paper` tests passing (46 + this fix's 1 new test + 1 unrelated heartbeat-startup-race regression test from concurrent work on this same file); ruff/mypy clean.
- Real Redis instance: **not exercised this session** (same disclosed gap as prior stories) — only `compute_history()`/`fills_store`'s pure logic and `trade_history.subscribe()`'s in-process msgbus wiring were exercised against a real Nautilus `BacktestEngine`; `trade_history.run()`'s actual Redis `SET`/reconnect path was never smoke-tested against a live Redis instance.
- Full regression (Docker, rebuilt image, documented command): **45 passed**, 0 failed — see Task 4.

### File List

- `troll/live_paper/node.py` (modified — `_FILLS_DB_PATH`, `trade_history.run(..., db_path=...)` wiring)
- `troll/live_paper/tests/test_node.py` (modified — Task 1's Redis Cache config assertion; unchanged by the Task 2 redesign)
- `troll/live_paper/fills_store.py` (new)
- `troll/live_paper/trade_history.py` (rewritten — event-sourced, no Cache reconstruction)
- `troll/live_paper/tests/test_fills_store.py` (new)
- `troll/live_paper/tests/test_trade_history.py` (new — replaces the never-created file the previous session left blocked)
- `troll/docker-compose.yml` (modified — `live-paper` service: `FILLS_DB_PATH` env var + `./live_paper/data` directory bind mount)
- `.gitignore` (modified — `/troll/live_paper/data/`)
