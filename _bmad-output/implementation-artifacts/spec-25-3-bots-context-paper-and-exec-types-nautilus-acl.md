---
title: 'Story 25.3: bots/ context: paper and exec as types, Nautilus behind an ACL'
type: 'refactor'
created: '2026-09-26'
status: 'done'
baseline_revision: '88abf7026918abd555033f2907038f664745a35e'
final_revision: 'bdde71b66169ac0916ff53c11e0eb35d67ac741a'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/25-3-bots-context-paper-and-exec-types-nautilus-acl.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `live_paper/` mixes the Nautilus runtime, the Redis control plane, the SQLite fill log and the paper/exec config in flat modules. Two module globals hold runtime state: `trade_history._pending_realized_pnl` and `fills_store._connections`/`_lock`. The paper/real split rests on loader discipline alone: `ExecConfig(mode="paper")` can be built directly, and nothing stops a second module from importing `TradingNode`.

**Approach:** Create the `bots/` bounded context:
- `domain/` holds `Bot`, `FillLedger`, and `PaperFleet`/`ExecBot` as distinct aggregates over the config value objects.
- `application/` holds `supervise` and `history`, over `typing.Protocol` ports.
- `infrastructure/` holds `nautilus_host` (the only `TradingNode` importer), `cache_reader`, `fills_store`, `redis` and `config`.
- `strategies/dummy.py` holds `DummyStrategy`.
- `__main__` is the composition root.

Replay tests recorded from the pre-move code prove every `bots:*` payload is byte-identical. `live_paper` becomes re-export shims. The 13 archive shims that expire at this story are deleted.

## Boundaries & Constraints

**Always:**
- These contracts stay byte-for-byte:
  - the `bots:status` JSON (field order and values), published on change and on the 5 s heartbeat;
  - `bots:control` parsing (`{bot_id, action: start|stop}` only; a mode field is ignored);
  - the `bots:history:{bot_id}:{day,week,month,all}` blobs (30 s refresh, 500-trade cap, rolling windows, `metrics`);
  - the `bots:incidents:{bot_id}` list (50 max, `data_stale`/`process_start`, `closed by restart`);
  - the `fills.db` schema and rows;
  - the env vars `REDIS_URL`, `FILLS_DB_PATH`, `LIVE_PAPER_REAL_MONEY_CONFIG` and `ERROR_LEDGER_*`;
  - the compose service `live-paper` and its data mount `./data/live_paper:/app/live_paper/data`;
  - `TraderId("LIVE-PAPER-001")`, the Redis-backed Cache config, and `order_id_tag == bot_id`.
- Layering (AD-D2):
  - `domain/` imports only the stdlib, `kernel` and `nautilus_trader.model`/`core`;
  - `application/` holds the ports and loops and never imports `bots.infrastructure`;
  - `infrastructure/` is imported only by `__main__` and tests.
- No module-level mutable runtime state in non-test `bots/` modules: tables are `MappingProxyType`, env vars are read only inside `__main__.main()`, and there is no `global`.
- Each aggregate/port docstring names its invariant (DESIGN-01). Every tolerated failure goes to `error_ledger.record` under a `bots.*` site (DATA-07).
- `bot_tui/` is not modified at all.

**Block If:**
- Byte identity with the recorded pre-move payloads cannot be reached without changing a wire payload.

**Never:**
- Touch `nautilus_trader/`, `crates/`, `bot_tui/` or `sprint-status.yaml`.
- Rename a Redis key/channel, env var, compose service or `fills.db` column.
- Let `bots:control` or any config key select paper vs non-paper.
- Import `TradingNode`/`TradingNodeConfig`/`nautilus_trader.live.node` outside `bots/infrastructure/nautilus_host.py` (tests included).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Recorded run | A fixed BacktestEngine run of `DummyStrategy`, fills through the ledger, fixed `now` | `bots:status` string and the four `bots:history` strings equal the fixture recorded from pre-move `live_paper` | — |
| Incident sequence | Prior incidents with an open span; stale → recover → stale ticks; 60 entries | JSON after each transition equals the fixture: orphan closed with the note, `process_start` appended, trimmed to 50 | — |
| Control messages | Matching/non-matching bot_id, bad action, extra `mode`, non-JSON | start/stop only for the matching valid action; mode ignored | A non-JSON message is ledgered `bots.control_message` and skipped |
| Mode smuggling | `PaperConfig` TOML with `mode`; `ExecConfig(mode="paper")`; `real_money` + `testnet` | Loader/aggregate raises `ValueError` naming the value | Fail closed at load |
| Fill write fails | Store raises on `write_fill` | The strategy is unaffected; the loop keeps running | `bots.fill_lost` ledgered with every field |
| Redis drop | A heartbeat, control or history connection raises | Reconnects after 2 s; incidents state survives the reconnect | `bots.redis` ledgered |

</intent-contract>

## Code Map

- `platform/live_paper/{config,venues,node,strategy,bot_status,trade_history,fills_store}.py` -- the code being moved (read fully during planning).
- `platform/live_paper/tests/*` -- move to `bots/tests`. `conftest.py`'s loop and log-guard fixtures stay as they are.
- `platform/live_paper/{README.md,DEPLOY_CHECKLIST.md,config.toml}` -- `git mv` to `bots/`.
- `platform/collector_core/{archive_gaps,backfill_bars,compare_klines,consolidate_catalog,crosscheck_errors,integrity,measure_lag,migrate_open_interest,nightly,prune_catalog,rebuild_seconds,repair_catalog}.py` and `dydx_collector/normalize_snapshot_schema.py` -- Story 25.1 shims with `REMOVE_AFTER = 25-3`: delete.
- `platform/tests/test_boundaries.py` -- `THIS_STORY`, the maps at `:124-160`, the ranking state rule at `:1310-1405` (generalise it to `bots`), and `test_checker_flags...` (`:597`).
- `platform/tests/test_images.py` -- `_module_of` does not map `-m pkg` to `pkg.__main__`, so today `python3 -m ranking`'s closure is only `ranking/__init__`. Also the entrypoint set (`:372`) and the shim-closure test (`:433`).
- Wiring and docs:
  - `live_paper.dockerfile`, `docker-compose.yml:265-330`, `Makefile:112-116,240-249`;
  - `ARCHITECTURE.md` §4, `CLAUDE.md` (intro exception, FORK-02), repo-root `CLAUDE.md:50`;
  - `docs/{BOT_OPERATIONS,DATABASE_SETUP,DATA_DICTIONARY}.md`, `README.md:154,257`, `archive/__init__.py:53`;
  - the `*/tests/test_ad8_boundary.py` docstrings, the `observability/error_ledger.py` and `kernel/{indicators,performance_metrics}.py` docstrings, the frontend docs `kbData.ts`/`data.ts`/`diagram.ts`, and `_bmad-output/project-context.md`.

## Tasks & Acceptance

**Execution:**
- [x] Scratch (before any move) -- drive the pre-move `live_paper` code and record `platform/bots/tests/fixtures/replay_payloads.json`:
  - the status and four history strings of one fixed BacktestEngine run (the fill log written by `trade_history.subscribe`, plus two seeded round trips);
  - the incidents JSON after a scripted transition sequence;
  - the control decisions for a fixed message list.
- [x] `platform/bots/domain/`:
  - `config.py`: the value objects `BotConfig`, `VenuePaperConfig`, `PaperConfig`, `ExecConfig`, and the pure table `VENUE_RULES` (allowed environments, paper quote currency; `MappingProxyType`). It also holds the aggregates:
    - `PaperFleet`: at least one bot, distinct `bot_id`s, one pool per venue in use, mode label `paper`, a per-bot starting-balance anchor;
    - `ExecBot`: mode ∈ {`real_money`, `exchange_demo`} agreeing with `environment` and with the venue, `subaccount` dYdX-only, labels `live`/`demo`, no anchor.
  - `bot.py`: `type BotId = str` and `Bot` (id, mode label, `started_at`, the bounded incident list) with commands `start(prior, now)` (close the orphan, then append `process_start`, then trim) and `observe(now, now_ns, last_data_ns, running) -> bool`. The pure transition, stale and trim functions move with it.
  - `fill_ledger.py`: `FillRecord` and `FillLedger(bot_id)` holding per-position pending estimates, with `attribute(fill, position) -> FillRecord` taken from `_fill_pnl`, plus `RANGES`, `cutoff_ns` and `history_payload`.
- [x] `platform/bots/application/`:
  - `ports.py`: `STATUS_CHANNEL`, `CONTROL_CHANNEL`, the key builders, and the ports `FillsStore`, `BotRuntime` (strategy name, symbol, running, last_data_ns, `positions()`, start, stop, `on_fill`), `BusConnection` (publish/get/set/`control_messages`) and `Connect`.
  - `supervise.py`: `build_status`, `parse_control_message` and `Supervisor` (seed once per process, heartbeat and control loops, reconnect after 2 s).
  - `history.py`: `HistoryPublisher` (`record_fill`, which writes off-loop via the executor or inline with no loop; `refresh`; the loop; reconnect).
  - Intervals are constructor arguments. Every tolerated failure is ledgered.
- [x] `platform/bots/infrastructure/`:
  - `nautilus_host.py`: `VenueSpec`, `VENUES` (keys == `VENUE_RULES`), `_paper_venue_clients`, `build_node(fleet, redis_url) -> (node, ((bot, strategy), ...))`.
  - `cache_reader.py`: `StrategyCacheReader(strategy)`, the `BotRuntime` over `positions_open/closed(strategy_id=...)` and the order-event topic.
  - `fills_store.py`: `SqliteFillsStore(db_path)` (lock, `close()`).
  - `redis.py`: `RedisBus` and `connect(url)`.
  - `config.py`: `load_paper_config`, `load_real_money_config` and `resolve_config`. They return `PaperFleet`/`ExecBot`, reuse the domain invariants, and keep the path-named error messages.
- [x] `platform/bots/strategies/dummy.py` -- move `DummyStrategy`; its 1 s interval is built inside `on_start`, not at import time.
- [x] `platform/bots/__main__.py` -- `main()`: read the env, `error_ledger.start()`, resolve the config, build the node, schedule one supervisor task and one history task per bot on the node loop, run and dispose.
- [x] `platform/bots/{__init__.py,README.md,DEPLOY_CHECKLIST.md,config.toml}` -- the context docstring and the moved docs with their paths updated.
- [x] `platform/bots/tests/` -- port every `live_paper` test onto the new API, and add:
  - the replay test;
  - invariant tests for each aggregate command (`Bot.start`/`observe`, `FillLedger.attribute`, `PaperFleet`/`ExecBot` construction);
  - a `FillsStore` port-contract test run against `SqliteFillsStore`;
  - a test that `VENUES` and `VENUE_RULES` have the same keys;
  - `test_ad8_boundary.py`.
- [x] `platform/live_paper/` -- pure re-export shims (`REMOVE_AFTER = "26-1-livebook-tradeintake-feedgroup-pure-secondsampler-in-place"`) for `config`, `venues`, `node` (with a `__main__` guard) and `strategy`. Delete `bot_status`, `trade_history`, `fills_store` and `tests/`; the package docstring maps them to their successors.
- [x] Delete the 13 archive shims. Repoint `test_images.py:434` and `test_boundaries.py:597/615` onto live shims. Remove the map entries. Update `README.md:257` and `archive/__init__.py:53`.
- [x] `platform/tests/test_boundaries.py`:
  - `THIS_STORY` → 25-3;
  - generalise the ranking state rule to `ranking` + `bots`;
  - add `test_only_the_nautilus_host_imports_trading_node` over every module, with a positive self-test.
- [x] `platform/tests/test_images.py` -- map `-m pkg` to `pkg.__main__` when it exists, and require `bots.__main__` in the entrypoint set.
- [x] Wiring:
  - `live_paper.dockerfile`: COPY `bots`, `kernel`, `observability`, `live_paper` (shims) and `tests`; `CMD ["python3", "-m", "bots"]`.
  - Compose: mount `./bots/config.toml:/app/bots/config.toml:ro`, and change the bot_tui strategy mount's source to `./bots/strategies/dummy.py`, keeping its container path.
  - Makefile: `test-live-paper` → `bots/tests …`, deselecting the two host-dependent `test_node.py` tests with the reason.
- [x] Docs:
  - the files in the Code Map, and the `platform/CLAUDE.md` exception clause → `bots/`;
  - `bots/DEPLOY_CHECKLIST.md`: a Story 25.3 VPS step for a locally edited `live_paper/config.toml`.

**Acceptance Criteria:**
- Given the moved code, when the platform suite and `bots/tests` run (host: `REDIS_URL=redis://127.0.0.1:16379`, the two host-dependent tests deselected), then there are no failures beyond the 3 baseline Redis ones and no new warnings.
- Given `test_boundaries.py`, when it runs, then:
  - an injected module-level dict, `global` or env read in a `bots` module is reported;
  - a `TradingNode` import anywhere but `nautilus_host.py` is reported;
  - every domain module passes the purity rule with no exemption.
- Given `test_images.py`, when it runs, then `live-paper`'s `bots.__main__` closure is shipped by `live_paper.dockerfile`, and the ranking closure is now really checked.
- Given the recorded fixture, when the replay test runs the new code, then every payload string is identical.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 15 (high 0, medium 5, low 10)
- defer: 6 (high 0, medium 4, low 2)
- reject: 4 (high 0, medium 0, low 4)
- addressed_findings:
  - `[medium]` `[patch]` The history publisher's and seed's Redis connections subscribed to `bots:control` without ever reading it, so control messages buffered for each connection's whole life. `connect(..., subscribe_control=)` is now opt-in; only the supervisor's connection subscribes (`bots/infrastructure/redis.py`, `__main__`).
  - `[medium]` `[patch]` `asyncio.gather` left the sibling loop running when heartbeat or control failed, so every Redis blip added a heartbeat loop. The supervisor now uses a `TaskGroup`. A regression test counts concurrent heartbeat loops: 23 under `gather`, 1 with the fix.
  - `[medium]` `[patch]` A failed incident-log seed reset the `Bot` to an empty log, losing the adopted history and the restart marker. Now a failed write keeps the adopted log in memory, and an unreadable prior log starts an empty one that still marks the start. `Bot.start` is enforced once per process life. Tests updated and added.
  - `[medium]` `[patch]` The TradingNode guard missed `from nautilus_trader import live` and a literal `importlib.import_module("nautilus_trader.live...")`. Both are now caught, with self-test lines added.
  - `[medium]` `[patch]` Shutdown:
    - task references are kept, and the tasks are cancelled and unwound before `node.dispose()`;
    - `SqliteFillsStore` refuses every call after `close()` instead of silently reopening a leaked connection, so a late executor write is a loud `bots.fill_lost`. Test added.
  - `[low]` `[patch]` Redis database number: `_cache_config` refuses a `REDIS_URL` naming one, because `DatabaseConfig` would silently put the Cache on db 0 and split it from the bus.
  - `[low]` `[patch]` Cache config coverage: pure `_cache_config` tests now cover credentials, TLS and port on every host, which the two deselected node tests never did. The Makefile comment now states that every `build_node` test needs the compose Redis.
  - `[low]` `[patch]` `PaperConfig.venues` is frozen (`MappingProxyType`), so validated pools cannot be edited afterwards.
  - `[low]` `[patch]` `PaperFleet` parses each `starting_balance` at construction, not first after the node and Cache exist.
  - `[low]` `[patch]` `starting_balance_anchor` takes a `bot_id`, removing the composition root's `type: ignore`.
  - `[low]` `[patch]` `test_images._PYTEST_VALUE_OPTIONS` now includes `--ignore`, `--rootdir`, `-c`, `--junitxml`, `--basetemp`, `--ignore-glob` and `-W`.
  - `[low]` `[patch]` The test fake `_UnusedRuntime.__getattr__` raises `AttributeError`, so doctest collection's `__wrapped__` probe works.
  - `[low]` `[patch]` The non-object `bots:control` message is ledgered with a `TypeError`, so it no longer logs a bare "NoneType: None" traceback.
  - `[low]` `[patch]` `python -m live_paper.node` now warns visibly from `__main__`, as the 25.1 shims did.
  - `[low]` `[patch]` The Story 25.3 VPS checklist step now greps the crontab for the deleted archive shim paths.

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 0, medium 2, low 5)
- defer: 3 (high 0, medium 1, low 2)
- reject: 10 (high 0, medium 0, low 10)
- addressed_findings:
  - `[medium]` `[patch]` Fill writes still queued on the loop's executor at shutdown were cancelled by `TradingNode.dispose` (`executor.shutdown(cancel_futures=True)`) before `_write` ran, so they were lost with no `bots.fill_lost` entry. `HistoryPublisher` now tracks its pending writes, `drain()` awaits them, and `__main__._stop` drains every publisher before dispose. The regression test fails when the drain is removed.
  - `[medium]` `[patch]` `Bot.start` set its once-per-life flag before building the log. A valid-JSON but malformed prior log (a dict, or non-incident entries) raised with the flag already set, so `seed`'s fallback skipped the start: no `process_start` marker, and an empty log. The flag is now set only after the log builds. Domain and supervisor tests cover three malformed shapes.
  - `[low]` `[patch]` The database-number guard read only the URL path. redis-py also honours a `?db=` query (the query wins), so `redis://host:6379?db=2` still split the Cache from the bus. Both are now checked, and a test was added.
  - `[low]` `[patch]` An ended `bots:control` stream (the subscription ends without raising) left the heartbeat publishing while every command went unheard. `_control_loop` now raises `ConnectionError` into the reconnect. Test added.
  - `[low]` `[patch]` A strategy refusing a start/stop transition raised out of `handle_control`. That tore down the shared connection, dropped the command, and was ledgered as `bots.redis`. It is now ledgered under a new `bots.control_action` site, and the connection stays up. Test added; the new site is listed in `bots/DEPLOY_CHECKLIST.md`.
  - `[low]` `[patch]` `__main__`:
    - code between `build_node` and the `try` (the loop assert, the per-bot wiring) could raise without `node.dispose()` running;
    - a failing `SqliteFillsStore` open left a built node undisposed.
    - `main` now opens the store first. `_run` builds the node inside its own `try`/`finally`, so the node is always disposed and the store always closed.
  - `[low]` `[patch]` `connect(subscribe_control=True)` subscribed before the `try` that closes the pubsub, so a subscribe failing mid-handshake skipped `pubsub.aclose()`. The subscribe now runs inside the `try`. Also: the Story 25.3 VPS step in `docs/DEPLOY_CHECKLIST.md` now names the two startup changes. A `REDIS_URL` with a database number now refuses to start. A host run's default `fills.db` moved to `platform/data/live_paper/`.

## Design Notes

- **Config mount moves with the code.** The frozen mounts are the `platform/data/` ones; `config.toml` is code-adjacent. It moves to `bots/config.toml` and is mounted at `/app/bots/config.toml`, and the checklist step covers a VPS-local edit of it. Keeping `/app/live_paper/config.toml` would make the in-image default path and the mount disagree.
- **`running` is not cached on `Bot`.** The strategy's `is_running` is the single truth. `Bot.observe` receives it every tick, so a cached copy can never go stale.
- **Venue facts split.** The domain may not import Nautilus adapters, so `VENUE_RULES` holds the pure per-venue facts and `nautilus_host.VENUES` holds the client factories. A test pins that both have the same keys.
- **Ledgering new.** Six failure paths only logged before; each now has one site: `bots.fill_lost`, `bots.incidents_write`, `bots.status_build`, `bots.control_message`, `bots.history_refresh` and `bots.redis`. The follow-up review added a seventh, `bots.control_action`, for a start/stop the strategy refuses (it used to raise into the connection and read as `bots.redis`). A startup `bots.status_build` entry is now visible where it was a debug line. The follow-up review added a seventh, `bots.control_action`: a start/stop the strategy refuses used to raise into the shared connection and read as `bots.redis`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -W default` -- expected: only the 3 baseline Redis failures.
- `cd platform && REDIS_URL=redis://127.0.0.1:16379 python3 -m pytest -o addopts="" --rootdir=. bots/tests -q -W default --deselect bots/tests/test_node.py::test_build_node_configures_redis_backed_cache --deselect bots/tests/test_node.py::test_build_node_passes_redis_credentials_and_ssl_from_url` -- expected: all pass.
- The scratchpad ruff/mypy venv on `bots/` and `live_paper/` -- expected: clean.



## Auto Run Result

Status: done

**Summary.** A follow-up review pass on the finished Story 25.3 `bots/` context. The code from the first run (commit `32628a57c7`) stands. This pass fixed 7 findings, 2 of them medium:
- **Fill writes lost at shutdown.** A fill write still queued on the node's executor was cancelled by `TradingNode.dispose` without any trace. Every `HistoryPublisher` is now drained before dispose.
- **Restart missing from the incident log.** A malformed prior incident log left the bot marked started with no `process_start` entry. The flag is now set only after the log builds.

No wire payload changed: the replay byte-identity test still passes.

**Files changed:**
- `platform/bots/application/history.py`: tracks pending executor writes; new `drain()`.
- `platform/bots/__main__.py`: the store opens first, and `_run` owns the node's `try`/`finally`. `_stop` drains every publisher before `dispose`.
- `platform/bots/domain/bot.py`: `Bot.start` sets its once-per-life flag only after the log builds.
- `platform/bots/application/supervise.py`:
  - a refused start/stop is ledgered as `bots.control_action` instead of tearing down the connection;
  - an ended control stream raises into the reconnect.
- `platform/bots/infrastructure/nautilus_host.py`: `_cache_config` also refuses a `?db=N` query database.
- `platform/bots/infrastructure/redis.py`: the control subscribe runs inside the `try` that closes the pubsub.
- `platform/bots/tests/{test_domain,test_bot_status,test_trade_history,test_node}.py`: 10 new test cases.
- `platform/docs/DEPLOY_CHECKLIST.md` (the Story 25.3 VPS step) and `platform/bots/DEPLOY_CHECKLIST.md`: two startup changes and the new ledger site. The startup changes: a `REDIS_URL` naming a database number now refuses to start, and a host run's default `fills.db` path moved.
- `_bmad-output/implementation-artifacts/deferred-work.md`: 3 new entries appended.

**Review findings:** one pass (Blind Hunter + Edge Case Hunter), 20 after deduplication.
- 7 patches applied (medium 2, low 5).
- 3 deferred, all pre-existing in `live_paper` at baseline `88abf70269`:
  - an unreachable-Redis seed overwrites the prior incident log;
  - Redis credentials reach the Cache still percent-encoded;
  - an unguarded `FillLedger.attribute` call on the fill dispatch.
- 10 rejected:
  - by design: `bots.*` ledgering at startup and on reconnects (spec Design Notes), and the deleted `live_paper` modules (spec task);
  - pre-existing and unreachable, or duplicates of existing deferred entries: the real-money loader's `instrument_id` type check, `bot_id` character validation, the history loop's reconnect site, the restart PnL double count;
  - harmless: the seed's short-lived control subscription;
  - out of scope or already documented: the `live_paper.config` shim's changed `PaperConfig` API (no in-repo consumer), `make test-live-paper` needing the compose Redis (documented in the Makefile).

**Verification:**
- Platform suite (the spec's first command, with `-p no:cacheprovider`): 3 failed / 1648 passed / 77 warnings. This matches the baseline. The 3 failures are the known Redis-dependent `data_api` tests, and the warning count is within the known 74-77 spread.
- `bots/tests` (`REDIS_URL=redis://127.0.0.1:16379`, the two deselects): 161 passed, up from 151 with the 10 new cases, including the replay test.
- Mutation check: with `await history.drain()` removed, the drain regression test fails.
- `ruff format`/`ruff check` on the changed files: clean except the pre-existing S105 in `test_node.py`.
- mypy on `bots/` sources: only the pre-existing `kernel/indicators.py` errors and `StrategyConfig, frozen=True`.

**Residual risks:**
- The rewired shutdown (`_run`/`_stop`/drain) is covered by unit tests of `drain` only. `python3 -m bots` was not run end to end against real venues or a live `TradingNode` stop in this session.
- The three deferred items stay open. The seed overwrite loses incident history when Redis and `live-paper` restart together.
- The VPS deploy still needs the Story 25.3 step in `docs/DEPLOY_CHECKLIST.md`, now including the two startup notes.
