---
baseline_revision: a5f58ce1c7b802e06f2b5ad46ca7061761784e8c
status: done
final_revision: 32eaf26419fcabab45434f50cad4a25a492c91dd
review_loop_iteration: 0
followup_review_recommended: true
operator_actions:
  - "On the VPS, once this branch is merged into `troll`: `cd troll && make up` to rebuild the thin collector image (it now bakes in `troll/collector_core/`) and restart `bybit-collector` and `hyperliquid-collector`."
  - "Watch Dozzle for at least 10 minutes: both containers stay up, no `[collector.*]` error_ledger lines, at most one `Dropped subscribe-time trade history` line per (re)subscribe, and `DydxSecondSnapshot` rows for `BTCUSDT-LINEAR.BYBIT` and `BTC-USD-PERP.HYPERLIQUID` land 1 s apart (e.g. via `/api/candles` or a catalog query)."
  - "Confirm `dydx-ranking-engine` lists the `.BYBIT`/`.HYPERLIQUID` ids in `/api/rankings` and its memory stays flat (`docker stats dydx-ranking-engine`) over those 10 minutes."
  - "Tick the VPS bullet under Task 6 in this story file and set its status to done."
---

# Story 22.1: `troll/collector_core/` extracted from the Bybit and Hyperliquid collectors

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want the ~200 identical lines of `bybit_collector/collector.py` and `hyperliquid_collector/collector.py` to live once in `troll/collector_core/collector.py`,
so that every venue gets the same ingest/flush/sample/write path and the same integrity guards.

## Acceptance Criteria

1. **Core exists and the siblings shrink.** `collector_core.Collector(config, client, extra_loops=())` and `collector_core.run_forever(build)` exist. `bybit_collector/collector.py` and `hyperliquid_collector/collector.py` each become a client + config + entrypoint of roughly 15–25 lines; the Bybit open-interest REST poll is passed as an `extra_loops` entry. The duck-typed client contract — `fetch_instruments()`, `connect(loop, instruments)`, `disconnect()`, `subscribe(iid)`, `unsubscribe(iid)`, optional `subscribe_global()`, optional `resync_orderbook(iid)` — is documented in the core module docstring.
2. **dYdX's venue-neutral guards run for every venue.** Trade stale-age filter + bounded `trade_id` dedup (DATA-06), the SQLite candle-store feed (DATA-05: `ml_signals/candle_store.py`, fed from each Parquet flush, one `candles_<venue>.db` per venue), `snapshots:raw` Redis publish, the `_second_loop` lag canary and the OBS-01 watchdog all run for Bybit and Hyperliquid, with thresholds in `CoreConfig` defaulting to dYdX's current values (`stale_trade_seconds=10`, `seen_trade_ids=2000`, `stale_book_seconds=5`, `crossed_resync_seconds=10`, lag warn 2 s, watchdog 30 s stale / 60 s startup grace / 10 min reminder).
3. **Unified cadence.** Every venue samples at `snapshot_interval_seconds = 1.0`; `bybit_collector/config.toml` and `hyperliquid_collector/config.toml` lose their `0.5` and the config default becomes `1.0`.
4. **Crossed book = skip + ledger + fallback-only resync.** When `best_bid >= best_ask` the core skips the sample, records `error_ledger.record("collector.crossed_book", ...)`, and resyncs only if the client exposes `resync_orderbook` **and** the book has stayed crossed longer than `crossed_resync_seconds` (DATA-03: a fallback, never the fix). A client without `resync_orderbook` (Hyperliquid) only ever skips.
5. **Tests + live verification.** The sibling tests move to `collector_core/tests/` using real `OrderBook`/`TradeTick`/`ParquetDataCatalog` objects (TEST-01/TEST-03), `make test` is green, and both collectors are live-verified on the VPS writing `DydxSecondSnapshot` rows at 1 s spacing.

## Tasks / Subtasks

- [x] Task 1 — `troll/collector_core/config.py` (AC: #2, #3)
  - [x] `CoreConfig` frozen dataclass: `environment: str`, `catalog_path: str`, `flush_interval_seconds: int` (60), `snapshot_interval_seconds: float` (**1.0**, must be > 0), `stale_book_seconds: float` (5.0), `crossed_resync_seconds: float` (10.0), `stale_trade_seconds: float` (10.0), `seen_trade_ids: int` (2000), `instruments: tuple[str, ...]`.
  - [x] `load_core_config(path: Path, environments: tuple[str, ...]) -> CoreConfig`: same TOML shape as today's sibling `load_config`, validating `environment in environments` and `snapshot_interval_seconds > 0`.
  - [x] Venue-specific keys stay in the venue package: `bybit_collector/config.py` keeps `open_interest_poll_seconds` (e.g. `BybitConfig(CoreConfig)` with that one extra field, built from `load_core_config` + the raw dict). Hyperliquid needs nothing extra beyond `stale_book_seconds = 30.0` in its TOML.
- [x] Task 2 — `troll/collector_core/collector.py`: the `Collector` class (AC: #1, #2, #4)
  - [x] Constructor `Collector(config: CoreConfig, client, extra_loops: tuple[Callable[[], Awaitable[None]], ...] = ())`. Each extra loop is a no-arg coroutine function started as a task in `run()` alongside the core loops — in practice a bound method of a thin venue subclass (see Task 4), which is how story 22.2's `DydxCollector` passes its control-plane loops too.
  - [x] **Attribute names are a contract for 22.2.** `dydx_collector/tests/*` poke these names directly, and 22.2 must pass them with import/attribute-path updates only — so the core uses dYdX's names, not the siblings': `_config`, `_client`, `_catalog`, `_buffer`, `_redis`, `_stop`, `_ingest_queue`, `_live_books`, `_last_book_update_ns`, `_crossed_since_ns`, `_second_buy_volume`/`_second_sell_volume`/`_second_buy_count`/`_second_sell_count`/`_second_open_price`/`_second_high_price`/`_second_low_price`/`_second_close_price` (separate dicts, **not** the siblings' `_trades` list), `_discard_second_accumulators(iid)`, `_stale_trades_dropped`, `_duplicate_trades_dropped`, `_seen_trade_ids`/`_seen_trade_id_set`, `_last_second_loop_tick_ns`, `_candle_db`; methods `_on_data`, `_ingest_loop`, `_process_data`, `_apply_deltas(iid, deltas)`, `_handle_crossed_book(iid, book, now_ns) -> bool`, `_is_duplicate_trade`, `_report_stale_trades`, `_flush_once`, `_flush_loop`, `_second_loop`, `_watchdog_loop`, `run`, `stop`; module-level `_publish_snapshot_batch`, `_watchdog_transition`, `_notify`, `quarantine_corrupt_parquet`, and the constants `_INGEST_YIELD_EVERY`, `_SECOND_LOOP_LAG_WARN_NS`, `_WATCHDOG_*`, `_IMPOSSIBLE_LOG_EVERY_NS`.
  - [x] Port verbatim from `bybit_collector/collector.py:89-124, 196-256`: `_on_data` (O(1) enqueue), `_ingest_loop` (`asyncio.wait_for(..., 1.0)`, yield every 64), `_process_data` routing, `_flush_once` via `asyncio.to_thread(self._catalog.write_data, items)`, `_flush_loop`, `run()` task supervision, `stop()`. Replace every bare `logger.exception(...)` at a data-dropping site with `error_ledger.record(site, detail, exc)` (DATA-07): sites `collector.enqueue`, `collector.process`, `collector.flush_write`, `collector.crossed_book`, `collector.resync`, `collector.candle_store`.
  - [x] Candle store, ported from dYdX (`dydx_collector/collector.py`: `_seconds_until_next_flush` wall-clock flush at :02, `_flush_once` -> `_apply_to_candle_store` with exactly the flushed seconds, `_catch_up_candle_store` at startup, hourly `candle_store.prune`): the core owns it; `CANDLES_DB_PATH` is per venue (compose: `candles_dydx.db`, `candles_bybit.db`, `candles_hyperliquid.db`) so three collectors never share one SQLite writer; `data_api` picks the file by `venue_of(instrument_id)`. `seconds_observed` assumes the 1.0 s cadence of AC #3.
  - [x] `_process_data` for `OrderBookDeltas` calls an overridable `_apply_deltas(iid, deltas)` (default: `book.apply_delta` per delta, then `_last_book_update_ns[iid] = now`). `QuoteTick` is ignored (not persisted); every other type buffers as-is for the catalog.
  - [x] Trade path from `dydx_collector/collector.py:642-665, 741-757`: drop if `now - ts_event > stale_trade_seconds` (count in `_stale_trades_dropped`), drop duplicate `trade_id` via the bounded `deque(maxlen=seen_trade_ids)` + `set` pair (`_is_duplicate_trade`), then update per-second OHLC + per-side volume/count. `_report_stale_trades()` runs after each flush (INFO for stale, WARNING for duplicates — never silent, DATA-05).
  - [x] `_second_loop` — the gate, in this exact order per instrument (mirrors `dydx_collector/collector.py:1169-1269` minus the uncross call): lag canary (warn if the tick is > 2 s late — name the consequence: crossed-book detection was not running); no book → discard accumulators + skip; empty top-of-book → discard + skip; `await self._handle_crossed_book(iid, book, now_ns)` returns True → discard + skip; stale (`now - _last_book_update_ns > stale_book_seconds`) → WARNING + discard + skip. **Always pop the trade accumulators on a skipped tick** — never carry an outage's trades into the next valid second (DATA-01/DATA-02, giant-range candle at recovery).
  - [x] Snapshot build unchanged (`DydxSecondSnapshot`, top `BOOK_DEPTH` levels, `None` OHLC when no trades). Then: `ohlc_outside_book(snapshot)` → `logger.error("IMPOSSIBLE trade OHLC ...")` rate-limited to one per instrument per 60 s (DATA-06 canary); the candle store is fed from `_flush_once`, not per second (Task 2's candle-store bullet); buffer the snapshot; after the loop `await _publish_snapshot_batch(self._redis, batch)` (AD-1: both sinks from the same validated object, same iteration).
  - [x] Three tiny hooks that keep story 22.2 an override-only migration: `_instrument_ids(self) -> Iterable[str]` (default `self._config.instruments`; dYdX's config holds `InstrumentEntry` objects and overrides it to `{e.id for e in ...}`) — `_second_loop`, `_watchdog_loop` and `run()` use this, never `self._config.instruments` directly; `_clear_book_state(self, iid)` (pops `_live_books`, `_crossed_since_ns`) — the resync path calls it, dYdX extends it with its per-level tags; and `_last_feed_message_ns` set in `_process_data` on **any** message (story 22.5 builds the feed-liveness gate on it).
  - [x] `_handle_crossed_book(self, iid, book, now_ns) -> bool` (overridable, AC #4): record first-crossed time in `_crossed_since_ns`, `error_ledger.record("collector.crossed_book", f"{iid} bid={bid} ask={ask}")` once per episode (not per second — keep the per-tick line at WARNING), and if `hasattr(self._client, "resync_orderbook")` and crossed for > `crossed_resync_seconds`: pop the live book + `_crossed_since_ns`, `await self._client.resync_orderbook(iid)` (ERROR log naming DATA-03), else nothing. Clear `_crossed_since_ns[iid]` when the book is seen uncrossed. Hyperliquid's client has no `resync_orderbook` → skip-only by construction, next `l2Book` message replaces the book.
  - [x] Watchdog (OBS-01) from `dydx_collector/collector.py:298-350, 1303-1328`: `_watchdog_transition` pure function (parameterise the message prefix, e.g. `f"{type(client).__name__}"` or the venue string, instead of the hard-coded `"dydx-collector"`), `_notify` (ntfy POST when `WATCHDOG_NTFY_URL` is set, else `logger.critical`), `_watchdog_loop` with the constants above.
  - [x] `run()`: `self._redis = aioredis.Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"))`; `fetch_instruments` → `catalog.write_data(instruments_from_pyo3(...))` → `connect(loop, instruments)` → `if hasattr(client, "subscribe_global"): await client.subscribe_global()` → warn on configured-but-unknown ids → `subscribe(iid)` for the rest → tasks = `_ingest_loop, _flush_loop, _second_loop, _watchdog_loop` + `extra_loops` → `asyncio.wait(FIRST_COMPLETED)` and re-raise a dead task → `finally`: cancel, `disconnect()`, `_flush_once()`, `redis.aclose()`.
  - [x] Module-level: the zstd `pq.write_table` patch (`dydx_collector/collector.py:122-137`, keep the `ponytail:` ceiling comment) and `quarantine_corrupt_parquet` (`:140-164`) move into the core. Until story 22.2 deletes dYdX's copies, both modules wrapping `pq.write_table` is harmless (`kwargs.setdefault`), but say so in a comment.
- [x] Task 3 — `collector_core.run_forever(build: Callable[[], Collector], *, init_rust_logging: bool = True) -> None` (AC: #1) — the flag exists so story 22.2's dYdX entrypoint, which installs its own richer `init_logging` (WS_RAW file sink) first, can pass `False` and not initialise Rust logging twice.
  - [x] Port `main()` from the siblings (`bybit_collector/collector.py:258-284`): `logging.basicConfig`, SIGINT/SIGTERM → `shutting_down`, `build()` per attempt, `_stop_on_shutdown` watcher, exponential backoff 1 s → 60 s on crash, reset on clean stop.
  - [x] **Close the Rust-logging observability gap (DATA-02).** Neither sibling calls `nautilus_pyo3.init_logging`, so every `log::warn!/error!` inside the Rust Bybit/Hyperliquid WS clients (including a failed `call_soon_threadsafe` = a delta silently never reaching `_on_data`) is invisible today. Call `init_logging(trader_id=TraderId("COLLECTOR-001"), instance_id=UUID4(), level_stdout=LogLevel.WARNING)` once in `run_forever` and **keep the returned `LogGuard` alive for the whole coroutine** — dropping the last guard shuts Rust logging down (see the comment at `dydx_collector/collector.py:1645-1659`). No `[WS_RAW]` file sink here — that is dYdX's incident-report subsystem and stays in `dydx_collector` (story 22.2).
  - [x] Call `quarantine_corrupt_parquet(config.catalog_path)` before the first build.
- [x] Task 4 — shrink the two venue packages (AC: #1, #3)
  - [x] `bybit_collector/collector.py` ≈ 25 lines, dYdX's own wiring pattern (`dydx_collector/collector.py:455-462`): `class BybitCollector(Collector)` whose `__init__(self, config: BybitConfig)` calls `super().__init__(config, BybitClient(on_data=self._on_data, environment=...), extra_loops=(self._open_interest_loop,))` — `self._on_data` is a bound method, so passing it before `super().__init__` is safe (no message can arrive before `connect()`), and this is exactly what dYdX does today. `_open_interest_loop(self)` is today's `:212-221` (poll `fetch_open_interest(self._config.environment)`, feed configured ids to `self._on_data`, failure → `error_ledger.record("collector.open_interest_poll", ...)`). Then `CONFIG_PATH` and `if __name__ == "__main__": asyncio.run(run_forever(lambda: BybitCollector(load_config(CONFIG_PATH))))`.
  - [x] `hyperliquid_collector/collector.py` ≈ 15 lines: `class HyperliquidCollector(Collector)` with `__init__(self, config: CoreConfig)` building `HyperliquidClient(on_data=self._on_data, environment=config.environment)`, no extra loops.
  - [x] `bybit_collector/client.py`, `hyperliquid_collector/client.py`: unchanged behaviour; add `unsubscribe(iid)` (contract requires it — one WS unsubscribe per topic subscribed) and keep `resync_orderbook` Bybit-only. Do **not** add spot here (story 22.4).
  - [x] Both `config.toml`: `snapshot_interval_seconds = 1.0`. Bybit's `open_interest_poll_seconds = 300` stays; Hyperliquid's `stale_book_seconds = 30.0` stays with its comment.
- [x] Task 5 — tests (AC: #5)
  - [x] `collector_core/tests/test_collector.py`: port both sibling files onto the core with a tiny in-test duck-typed client (a plain class recording `resync_orderbook` calls — this is *our* contract, not a Nautilus internal, so TEST-03 allows it). Cover: snapshot from book + trades; accumulators reset on the next tick; crossed → skipped + `error_ledger.counts()["collector.crossed_book"] == 1`; crossed > `crossed_resync_seconds` → `resync_orderbook` called exactly once **only when the client has it**; crossed with a client lacking `resync_orderbook` → never resynced, recovers when a new snapshot delta arrives; stale book skipped and its trades discarded (assert the following valid tick has `open_price is None`); trade older than `stale_trade_seconds` dropped; duplicate `trade_id` dropped; `ohlc_outside_book` canary fires once per 60 s; the 1m bar is in `candles.db` after the flush; catalog round-trip read back through `ml_signals.catalog_stats.query_second_snapshots` for a `.BYBIT` and a `.HYPERLIQUID` id.
  - [x] `collector_core/tests/test_watchdog.py`: copy of `dydx_collector/tests/test_watchdog.py` against the core's `_watchdog_transition` (dYdX's copy is deleted in 22.2).
  - [x] `collector_core/tests/test_redis_pub.py`: copy of `dydx_collector/tests/test_redis_pub.py` against the core's `_publish_snapshot_batch`.
  - [x] `collector_core/tests/test_config.py`: defaults (`1.0`, `5.0`, `10.0`), `environment` validation, `snapshot_interval_seconds <= 0` rejected.
  - [x] Keep only venue-specific tests in `bybit_collector/tests` (`parse_open_interest`, `BybitOpenInterest` catalog round-trip, Bybit config) and `hyperliquid_collector/tests` (`HyperliquidOpenInterest`, config). Delete the moved duplicates.
  - [x] `troll/Makefile` `test` target: add `collector_core/tests`. `troll/collector.dockerfile`: `COPY troll/collector_core ./collector_core`.
- [x] Task 6 — live verification (AC: #5)
  - [x] Local: run each collector ~60 s against mainnet; `ParquetDataCatalog(...).query(DydxSecondSnapshot, identifiers=["BTCUSDT-LINEAR.BYBIT"])` and the `.HYPERLIQUID` id show consecutive `ts_event` ≈ 1 s apart; `candles.db` holds that coin's 1m bars after the first flush; a Redis subscriber on `snapshots:raw` sees both venues' ids; Dozzle/logs show no `error_ledger` sites tripping.
  - [ ] VPS: `make up` (thin image only), Dozzle shows `bybit-collector` and `hyperliquid-collector` healthy for ≥ 10 min; confirm `ranking_engine` accepts the new ids on `snapshots:raw` (it keys by `instrument_id`; check its memory stays flat).
  - [x] Register in `troll/docs/DATA_INTEGRITY_AUDIT.md`: Bybit/Hyperliquid now carry DATA-06 stale-age + dedup guards; the subscribe-time replay *verification* for their trade channels remains OPEN (story 22.5).

## Dev Notes

### What this story is — and is not

- **It is** the venue-neutral half of `dydx_collector/collector.py` re-homed as one class, built from the two already-identical siblings (`diff bybit_collector/collector.py hyperliquid_collector/collector.py` is ~30 lines: Bybit has `_crossed_since_ns` + resync + the OI loop; Hyperliquid has `stale_book_seconds` from config). The research doc §B1 is the design; the epics AC text is the contract.
- **It is not** the dYdX migration (22.2), the module moves of `second_snapshot.py`/`integrity.py` (22.3), Bybit spot (22.4) or the Bybit `u` canary / REST cross-check (22.5). Keep importing `DydxSecondSnapshot`, `BOOK_DEPTH`, `ohlc_outside_book` from `dydx_collector.*` exactly as the siblings do today — 22.3 moves them. `dydx_collector/` is **not modified** in this story; if you feel the urge to touch it, stop — that is 22.2.
- **Ponytail shape (research §B1):** one class, duck-typed client, `extra_loops` tuple. No registry, no ABC/Protocol class for the client (a docstring is the contract; each venue's own tests exercise it), no plugin discovery.

### The gate order is load-bearing (AD-1, AD-2, DATA-01)

`_second_loop` validates *then* writes to both sinks from the same object in the same iteration. Never move the Redis publish to a separate consumer. A rejected item is dropped — not clamped, not averaged, not carried — and the reason is logged. The trade accumulators must be popped on every skip path (dYdX's `_discard_second_accumulators`), or an outage's trades get stamped onto the first valid second afterwards.

### Crossed-book semantics differ per venue — the core must not assume dYdX's

- dYdX: crossing is architectural (DATA-04) → per-level msg-id uncross. **Not in the core.** 22.2 overrides `_handle_crossed_book` + `_apply_deltas`.
- Bybit: central book; a cross is *our* local corruption (delta stream without gap check) → loud ledger entry + resync after `crossed_resync_seconds` as the DATA-03 fallback. Treat a rising resync count as an open DATA-02 incident, not "the safety net works".
- Hyperliquid: every `l2Book` is a full snapshot (Rust emits `Clear` + `Add`, `sequence=0`); a cross is impossible unless the venue is wrong → ledger + skip; the next message replaces the book. No `resync_orderbook` on its client, on purpose.

### Redis publish is new for Bybit/Hyperliquid

`snapshots:raw` payloads are disjoint by `instrument_id`; `ranking_engine` keys by iid (research §B4, spine convention becomes "one producer per (channel, venue)" in 22.8). Publish failure = WARNING + swallow, as dYdX does (missing one live tick is acceptable; the Parquet write is the durable path). `collector:status`/`collector:control` stay dYdX-only — do not add them to the core.

### DATA-07: no bare `logger.exception(...); continue`

Every continue-past-failure site calls `ml_signals.error_ledger.record(site, detail, exc)` so `/api/errors` and the frontend `<ErrorBar>` show it. The siblings currently violate this (`logger.exception` at enqueue/process/flush/resync/OI poll); fix it while porting, don't copy it.

### Precision

No re-stamping anywhere in the core. Bybit and Hyperliquid parse every price at `instrument.price_precision()` (verified in 19.3/19.4 Task 1); `_at_fixed_precision` is dYdX-only and stays in `dydx_collector/client.py`. Snapshot fields are `float` lists by schema (`DydxSecondSnapshot.schema()`), unchanged.

### Config

Plain TOML read once at startup, no hot reload — that is dYdX's control plane (22.2 keeps it there). Default cadence is now `1.0`; remove the `0.5` literal from both venue `load_config` defaults and TOMLs. A test must pin the `1.0` default.

### Project Structure Notes

- New: `troll/collector_core/{__init__,collector,config}.py`, `troll/collector_core/tests/{__init__,test_collector,test_watchdog,test_redis_pub,test_config}.py`.
- Modified: `troll/bybit_collector/{collector,config,client}.py`, `config.toml`, `tests/test_collector.py`; `troll/hyperliquid_collector/{collector,client}.py`, `config.toml`, `tests/test_collector.py`; `troll/collector.dockerfile` (`COPY troll/collector_core`); `troll/Makefile` (`test` target); `troll/docs/DATA_INTEGRITY_AUDIT.md`.
- Unchanged: `troll/dydx_collector/**`, `troll/docker-compose.yml` (entrypoints `python3 -m bybit_collector.collector` / `hyperliquid_collector.collector` still exist; both services run `network_mode: host`, so the default `REDIS_URL` reaches the host Redis — add the env line for parity with the dYdX service if you like, no port changes: SEC-01).
- Layout/lint: LGPL header on every new file (pre-commit `check-copyright-year`), ruff line length 100, one import per line, `mypy --disallow-incomplete-defs` (type the duck-typed client parameter as `Any` or a minimal `Protocol` — if a `Protocol`, keep it private and untested; the docstring is the public contract).

### Previous story intelligence (19.3 / 19.4)

- Both stories deliberately left out vs dYdX: pruning, Redis status/control, watchdog, incident reports, candle-store feed, config live-reload. This story adds back exactly the venue-neutral subset (candle-store feed, Redis `snapshots:raw`, watchdog, trade guards); pruning/status/control/incidents stay dYdX-only.
- 19.4 observed Hyperliquid `l2Book` pushes ~5 s apart → `stale_book_seconds = 30` and the 1 s sampler repeats the last authoritative book between pushes. Keep that behaviour; resolving the docs-vs-observed discrepancy is 22.5.
- 19.3 verified 879 linear instruments load and a 20 s run wrote 30 BTCUSDT snapshots; 19.4 wrote 45 BTC snapshots in 25 s. After this story both rates are 1/s.
- Ids: `SYMBOL-LINEAR.BYBIT`, `SYMBOL-USD-PERP.HYPERLIQUID`.

### Git intelligence

Recent commits (`2af978db0d`, `3436361ca0`, `cb128d5d95`) are chart/frontend correctness fixes — no collector changes since 19.3/19.4 landed. The working tree carries unrelated uncommitted `data_api`/`frontend` edits from other sessions; do not stage them with this story.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.1] — ACs (verbatim contract).
- [Source: _bmad-output/planning-artifacts/research/technical-multi-exchange-collector-core-and-orderbook-research-2026-09-20.md#B1, #C, #D] — core shape, per-venue crossed-book semantics, migration order (Bybit+HL first, dYdX second).
- [Source: troll/bybit_collector/collector.py, troll/hyperliquid_collector/collector.py] — the code being merged (read in full).
- [Source: troll/dydx_collector/collector.py:113-174 (trade constants, zstd patch, quarantine, `_STALE_BOOK_NS`), :244-256 (watchdog + lag constants), :298-350 (watchdog transition/notify), :368-381 (`_publish_snapshot_batch`), :501-510 (dedup state), :587-757 (`_on_data`/ingest/process/trade guards/flush), :1169-1269 (`_second_loop` gate), :1303-1396 (watchdog loop, `run`), :1639-1719 (`main`, `init_logging` LogGuard note)] — the venue-neutral behaviour ported into the core.
- [Source: troll/dydx_collector/tests/{test_watchdog,test_redis_pub,test_collector_snapshot}.py] — test patterns to copy.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-1, #AD-2, #AD-4, #AD-5] — single write gate, fail-closed, module boundary, precision.
- [Source: troll/CLAUDE.md DATA-01..DATA-07, DESIGN-01..03, TEST-01..04, OBS-01, SEC-01] — rules applied above.
- [Source: _bmad-output/implementation-artifacts/19-3-troll-bybit-collector.md, 19-4-troll-hyperliquid-collector.md] — completion notes and the "extract once duplication is visible" deferral this story closes.

## Review Triage Log

### 2026-09-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 1, medium 6, low 10)
- defer: 2: (high 0, medium 2, low 0)
- reject: 8
- addressed_findings:
  - `[high]` `[patch]` A book with no local state was created from incremental deltas (after a resync, or before the first snapshot), so in-flight deltas built a shallow, uncrossed-looking book that passed every gate — `_apply_deltas` now only creates a book from a snapshot (leading `Clear`, which both the Bybit and Hyperliquid Rust adapters emit); pre-snapshot deltas are counted and reported each flush; test added.
  - `[medium]` `[patch]` `quarantine_corrupt_parquet` moved into the core meant every venue's (re)start scanned the whole shared catalog root and could move a sibling collector's mid-write file — now scoped to this collector's own `data/*/<iid>/` directories (only this process writes them, and it is not writing yet); move failures are ledgered instead of escaping before the restart loop; test added.
  - `[medium]` `[patch]` A successful forced resync left only a log line — now every forced resync is an `error_ledger` `collector.resync` entry so a rising count is visible in `/api/errors` (DATA-03); a resync that fails after the book was dropped is retried from the next tick instead of leaving the instrument bookless forever; tests added (repeat-per-window and failed-then-retried).
  - `[medium]` `[patch]` The "no book" skip path was completely silent (instrument dead while the all-instruments watchdog stays quiet) — rate-limited WARNING, one per instrument per minute; test added.
  - `[medium]` `[patch]` Config accepted unknown keys (a misspelt threshold silently fell back to the default), non-positive thresholds (`seen_trade_ids = 0` would drop every trade with an `IndexError`), and duplicate instrument ids (two rows per tick) — all rejected/deduped in `core_config_from_dict`, with `extra_keys` for venue-specific keys and a `> 0` check on Bybit's poll cadence; tests added.
  - `[medium]` `[patch]` `docker-compose.yml`: the Bybit/Hyperliquid services had no `REDIS_URL` (a non-default `REDIS_PORT` would fail every publish) — added, plus the commented `WATCHDOG_NTFY_URL` line for OBS-01 parity with the dYdX service.
  - `[medium]` `[patch]` Hyperliquid lost its 30 s stale-book default when its loader became a shim over the core's 5 s default — the venue shim now sets it (TOML may still override); test added.
  - `[low]` `[patch]` Bybit's REST-polled open interest went through `_on_data` and so refreshed `_last_feed_message_ns` (story 22.5's WS-liveness gate) — it is now buffered directly; the attribute's comment says so.
  - `[low]` `[patch]` An empty `OrderBookDeltas` refreshed the staleness clock — early return, as dYdX does.
  - `[low]` `[patch]` `_notify` only caught `OSError`, so a malformed `WATCHDOG_NTFY_URL` would kill the watchdog loop and restart the collector every 30 s — broad except.
  - `[low]` `[patch]` Redis client had no socket timeouts, so a black-holed Redis could stall `_second_loop` — 1 s connect/socket timeouts.
  - `[low]` `[patch]` A `disconnect()` failure in `run()`'s `finally` skipped the final flush — guarded and ledgered; the final flush now also reports outstanding stale/duplicate drop counts.
  - `[low]` `[patch]` Tests: the "resync exactly once" test never re-fed a still-crossed snapshot, and the dedup window's eviction boundary was untested — both covered.
  - `[low]` `[patch]` `bybit_collector/config.py` hand-rolled `dataclasses.asdict` and duplicated the poll default; the OI loop read `_config.instruments` instead of the `_instrument_ids()` hook — simplified.
  - `[low]` `[patch]` Story body `Status:` line still said `ready-for-dev` — updated to match the frontmatter.

## Dev Agent Record

### Agent Model Used

Claude Fable 5.1 (claude-fable-5-1), bmad-dev-auto run 20260920-092636-284d

### Debug Log References

### Completion Notes List

- `collector_core.Collector(config, client, extra_loops=())` + `run_forever(build, *, init_rust_logging=True)` built from the two identical siblings plus dYdX's venue-neutral guards; every attribute/method/constant name in Task 2's contract list is present. One additive seam: `_sample_tick(now_ns)` (one gate pass with an injected clock, called by `_second_loop`) so tests drive the gate without running the loop.
- Crossed book: `error_ledger` `collector.crossed_book` once per episode, WARNING per tick, resync only with `resync_orderbook` on the client and past `crossed_resync_seconds`; Hyperliquid's client has no `resync_orderbook` so it is skip-only by construction (tested both shapes).
- `run_forever` installs `nautilus_pyo3.init_logging(... level_stdout=WARNING)` and keeps the `LogGuard` referenced -- proven live: the Rust `nautilus_bybit::http::client` fee-rate WARN now appears in the Bybit collector's stdout, which it never did before.
- Both venue `collector.py` files are now a `Collector` subclass + entrypoint (Bybit ~25 code lines incl. the OI loop, Hyperliquid ~12). `unsubscribe(iid)` added to both clients (verified against the runtime pyo3 method names).
- Live (local, mainnet, 100 s, `REDIS_URL` -> the local `frontend-dev` stack's Redis): BTC/ETH on both venues wrote 95-96 `DydxSecondSnapshot` rows each at exactly 1.0 s spacing (HL max gap 1.01 s), 20 levels, trades counted; the store fed after each flush; a `snapshots:raw` subscriber saw all four ids; the local `ranking_engine` ingested them (`/api/rankings` lists the `.BYBIT`/`.HYPERLIQUID` ids, memory flat at ~370 MB); no `error_ledger` site tripped. Finding: Hyperliquid's trade subscribe reply replays history (21/19 trades dropped by the age filter) -- recorded as D-39 evidence; Bybit dropped none.
- Host runner: `cd troll && python3 -m pytest -o addopts="" --rootdir=. collector_core/tests bybit_collector/tests hyperliquid_collector/tests dydx_collector/tests` -> 169 passed (22 new), no warnings with `-W error::DeprecationWarning -W error::RuntimeWarning`; `ruff check`/`ruff format --check` clean on all touched files; mypy reports no errors in any new/changed file (its 20 errors are pre-existing in `ml_signals`/`dydx_collector`).
- Not done here (operator): the VPS deploy + 10 min Dozzle/ranking_engine check in Task 6 -- this branch is unmerged and deploying it to production is not an agent action. `sprint-status.yaml` untouched (orchestrator-owned).

### File List

- New: `troll/collector_core/{__init__,collector,config}.py`, `troll/collector_core/tests/{__init__,test_collector,test_watchdog,test_redis_pub,test_config}.py`
- Modified: `troll/bybit_collector/{collector,config,client}.py`, `troll/bybit_collector/config.toml`, `troll/bybit_collector/tests/test_collector.py`, `troll/hyperliquid_collector/{collector,config,client}.py`, `troll/hyperliquid_collector/config.toml`, `troll/hyperliquid_collector/tests/test_collector.py`, `troll/collector.dockerfile`, `troll/docker-compose.yml` (env parity only: `REDIS_URL` + commented `WATCHDOG_NTFY_URL` on the two services; no ports), `troll/Makefile`, `troll/docs/DATA_INTEGRITY_AUDIT.md`, `_bmad-output/implementation-artifacts/deferred-work.md`
- Unchanged: `troll/dydx_collector/**`, `_bmad-output/implementation-artifacts/sprint-status.yaml`

## Auto Run Result

Status: awaiting-operator

**Summary:** Story 22.1 implemented and reviewed. `troll/collector_core/` (`Collector(config, client, extra_loops=())`, `run_forever(build, *, init_rust_logging=True)`, `CoreConfig`/`load_core_config`) now owns the ingest/flush/sample/write path with every contract name from Task 2, and dYdX's venue-neutral guards run for Bybit and Hyperliquid: stale-trade age filter + bounded `trade_id` dedup, stale-book skip with accumulator discard, crossed-book skip + once-per-episode ledger with resync only as a DATA-03 fallback (Bybit only; Hyperliquid skip-only by construction), `ohlc_outside_book` canary, candle-store feed, `snapshots:raw` publish, the `_second_loop` lag canary, the OBS-01 watchdog, Rust-side logging with a live `LogGuard`, and the zstd/quarantine module patches. Both venue collectors are a subclass + entrypoint (Bybit ~25 code lines with the OI loop, Hyperliquid ~12); both TOMLs and the default sample at 1.0 s. All five acceptance criteria are met except AC #5's VPS half, which is an operator deploy (see `operator_actions`).

**Files changed:** see File List above. New `troll/collector_core/` (3 modules + 4 test files, 26 tests); the two venue packages shrunk (collector, config, client, TOML, tests); `troll/collector.dockerfile`, `troll/Makefile`, `troll/docker-compose.yml` (env parity only), `troll/docs/DATA_INTEGRITY_AUDIT.md` (D-35 GUARDED, D-36 OPEN with the live finding), `deferred-work.md` (2 entries). `troll/dydx_collector/**` and `sprint-status.yaml` untouched.

**Review findings:** 17 patched (1 high: a book was created from pre-snapshot incremental deltas after a resync — now snapshot-gated; 6 medium: per-venue-scoped quarantine, resync ledgered + retried, no-book WARNING, config validation, compose `REDIS_URL`, Hyperliquid 30 s default; 10 low), 2 deferred (Redis publish invisible to `/api/errors`; host clock skew vs the stale-trade filter — both inherited from dYdX), 8 rejected (docker already restarts a failed `build()`, flush-cancel race and mid-batch `apply_delta` are dYdX-identical, watchdog on all-unknown ids is correct, `run()` wiring is live-verified glue, `troll/CLAUDE.md` scope is story 22.8, resync-in-loop timestamp skew is bounded and covered by the lag canary, the VPS AC is the operator action). `followup_review_recommended: true`: the review pass changed behaviour in the resync path, book creation, config validation and shutdown, with one high-severity fix — worth one independent look.

**Verification performed:**
- `cd troll && python3 -m pytest -o addopts="" --rootdir=. collector_core/tests bybit_collector/tests hyperliquid_collector/tests dydx_collector/tests -W error::DeprecationWarning -W error::RuntimeWarning` → 180 passed, no warnings (host Python; the Docker `make test` target also lists `collector_core/tests`).
- `ruff check` + `ruff format --check` clean on every touched file; mypy (`--disallow-incomplete-defs`) reports nothing in any new/changed file (its remaining errors are pre-existing in `ml_signals`/`dydx_collector`).
- Live, local, mainnet, before the review patches (100 s): BTC/ETH on both venues wrote 95–96 `DydxSecondSnapshot` rows each at exactly 1.0 s spacing (HL max 1.01 s), 20 levels, the store fed after each flush; a `snapshots:raw` subscriber saw all four ids; the local `ranking_engine` listed them in `/api/rankings` with memory flat at ~370 MB; no ledger site tripped; the Rust `nautilus_bybit` fee-rate WARN appeared on stdout (LogGuard alive). Re-run after the patches (45 s): 40–41 rows per instrument at 1 s, books formed from the venues' snapshots (no pre-snapshot delta drops), no ledger entries.
- Finding recorded as D-39 evidence: Hyperliquid's trade subscribe reply replays history (21/19 and 24/25 trades dropped by the age filter at subscribe); Bybit dropped none.

**Residual risks:** the D-39 replay bounds are unverified against the raw feed (story 22.5); the two deferred items above; `DydxSecondSnapshot`/`ohlc_outside_book` are still imported from `dydx_collector` until story 22.3 moves them, and both `dydx_collector` and `collector_core` wrap `pq.write_table` until 22.2 (harmless, commented).

This story's AC #5 includes a production deploy on the VPS that only the operator performs — hence `awaiting-operator`, not `blocked`, with the owed steps under `operator_actions`.

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- On the VPS, once this branch is merged into `troll`: `cd troll && make up` to rebuild the thin collector image (it now bakes in `troll/collector_core/`) and restart `bybit-collector` and `hyperliquid-collector`.
- Watch Dozzle for at least 10 minutes: both containers stay up, no `[collector.*]` error_ledger lines, at most one `Dropped subscribe-time trade history` line per (re)subscribe, and `DydxSecondSnapshot` rows for `BTCUSDT-LINEAR.BYBIT` and `BTC-USD-PERP.HYPERLIQUID` land 1 s apart (e.g. via `/api/candles` or a catalog query).
- Confirm `dydx-ranking-engine` lists the `.BYBIT`/`.HYPERLIQUID` ids in `/api/rankings` and its memory stays flat (`docker stats dydx-ranking-engine`) over those 10 minutes.
- Tick the VPS bullet under Task 6 in this story file and set its status to done.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
