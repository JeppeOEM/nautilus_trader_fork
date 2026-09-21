---
status: awaiting-operator
followup_review_recommended: false
final_revision: a46d8e2305ba1c3bad98684817455857b25d37f4
operator_actions:
  - "Run collector locally ~60s on mainnet: verify 1s custom_dydx_second_snapshot rows, candles.db 1m bars, snapshots:raw .DYDX ids, a collector:status publish, and a collector:control stop/start round-trip. [done locally 2026-09-21: 1 s rows spaced ~1.007 s for 29 .DYDX ids, candles_dydx.db 1m bars for 29 ids, snapshots:raw carries .DYDX ids, 58 collector:status messages, stop/start of ALGO-USD-PERP.DYDX unsubscribed+resubscribed and restored config.toml byte-identical]"
  - "On the VPS run make redeploy (thin image) and confirm dydx-collector healthy >= 10 min in Dozzle, bot_tui Collector pane populates, pin_top_liquid works, incident reports land on a WARNING."
  - "Check VPS troll/config.toml for an explicit snapshot_interval_seconds = 0.5 and set it to 1.0."
  - "Confirm no double Rust-logging init (WS_RAW file sink intact). [done locally 2026-09-21: entrypoint uses init_rust_logging=False, no init/logging errors in container log, /tmp/nautilus_logs/ws_raw_debug_*.log receives [WS_RAW] lines (15,137 in one rotated file)]"

baseline_revision: b008ed3b3eee9649c647e8cce4612ab00ea7a050
---

# Story 22.2: dYdX collector onto the core

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want `dydx_collector/collector.py` to be `class DydxCollector(collector_core.Collector)`,
so that the production dYdX feed shares the single write gate (AD-1) instead of a 1.7k-line copy of it.

## Acceptance Criteria

1. **Venue-specific book logic is isolated and wired by two overrides.** dYdX's per-level message-id tagging (`_apply_deltas`) and uncross + escalation (`_handle_crossed_book`, DATA-04) live in `dydx_collector/uncross.py` and are wired by overriding exactly those two core methods; the core's `_second_loop` gate is otherwise unchanged from today's `dydx_collector/collector.py:1188-1269`.
2. **Control plane stays dYdX-only, unchanged in behaviour.** Config hot-reload, `collector:status`/`collector:control` Redis loops, liquidity tiering, prune, watchdog notifications, incident reports, `[WS_RAW]` flush and `_MAX_WS_SUBSCRIPTIONS = 32` stay in `dydx_collector/` as `extra_loops` and `DydxConfig(CoreConfig)` fields.
3. **All 18 existing test modules pass with only import/attribute-path updates** (no behavioural rewrites), `make test` is green, and the dYdX collector is live-verified on the VPS with the bot_tui collector pane (`collector:status` rows, `start`/`stop`/`unpin`/`pin_top_liquid` actions) still working.

## Tasks / Subtasks

- [x] Task 0 — Preconditions (AC: #1)
  - [x] Story 22.1 is merged: `collector_core.Collector` exists with the hooks `_instrument_ids()`, `_clear_book_state(iid)`, `_apply_deltas(iid, deltas)`, `_handle_crossed_book(iid, book, now_ns)`, `run_forever(build, *, init_rust_logging=...)` and dYdX's attribute names (see 22.1 Task 2's "attribute names are a contract" list). If any is missing, add it to the core in this story rather than working around it — the core is the one write gate.
- [x] Task 1 — `dydx_collector/config.py`: `DydxConfig(CoreConfig)` (AC: #2)
  - [x] Keep `InstrumentEntry` and `save_config` as they are. `DydxConfig` is a frozen dataclass subclass adding `network: DydxNetwork`, `config_reload_seconds`, `open_interest_poll_seconds`, `non_config_retain_hours`, `liquidity_min_oi_usd`, `liquidity_check_seconds`, `exclude: frozenset[str]`, and re-declaring `instruments: tuple[InstrumentEntry, ...]` (the core's field is `tuple[str, ...]`; the `_instrument_ids()` override below is what keeps the core agnostic). Set the inherited `environment` to `str(network)` so `CoreConfig` stays satisfied.
  - [x] `load_config(path) -> DydxConfig` keeps every current default **except** `snapshot_interval_seconds`, which becomes `1.0` (Epic 22 decision). Check `troll/config.toml` (the file compose mounts over `/app/dydx_collector/config.toml`) and the VPS copy for an explicit `0.5` and set it to `1.0` at deploy — the VPS file is runtime state, not in git.
  - [x] `tests/test_config.py`: adjust the `0.5` default assertion to `1.0`; nothing else should change.
- [x] Task 2 — `dydx_collector/uncross.py` (AC: #1)
  - [x] Move verbatim from `collector.py`: `_UNCROSS_MAX_STEPS` (`:230`), `_resolve_stale_level` (`:384-402`), `_apply_stale_delete` (`:405-424`), `_log_uncross_result` (`:427-451`), and the four methods `_uncross_step` (`:1015-1051`), `_handle_uncrossed_book` (`:1053-1077`), `_try_active_uncross` (`:1079-1095`), `_escalate_persistent_crossed_book` (`:1097-1147`) as functions taking the state they touch (`book`, `level_msg_id: dict`, `crossed_since_ns`, `crossed_prices`, `last_bid/ask_delta_ns`, `now_ns`, and `resync: Callable[[str], Awaitable[None]]` for the escalation). Keep every docstring and the DATA-04/DATA-02/DATA-03 comments — they are the documented evidence trail.
  - [x] `DydxCollector` keeps thin methods with the **same names** (`_uncross_step`, `_handle_crossed_book`, `_resync_book`) delegating to `uncross.py`, because `tests/test_collector_snapshot.py` and `test_collector_resilience.py` call `collector._uncross_step(...)`, `collector._handle_crossed_book(...)`, `collector._resync_book(...)` and inspect `collector._level_msg_id` / `collector._crossed_prices` directly (AC #3: attribute paths, not behaviour, may change).
- [x] Task 3 — `dydx_collector/collector.py` becomes `DydxCollector(Collector)` (AC: #1, #2)
  - [x] `__init__(self, config: DydxConfig)`: build `DydxClient(on_data=self._on_data, network=config.network)`, call `super().__init__(config, client, extra_loops=(self._reload_config_loop, self._open_interest_loop, self._status_loop, self._control_loop, self._prune_loop, _ws_raw_debug_flush_loop))`, then initialise the dYdX-only state exactly as today (`:467-489, 527-549`): `_known_markets`, `_last_liquid_by_volume`, `_delta_store`, `_delta_retain_hours`, `_last_bid_delta_ns`, `_last_ask_delta_ns`, `_crossed_prices`, `_level_msg_id`. Everything else (`_buffer`, `_redis`, accumulators, dedup, `_live_books`, watchdog state, `_candle_db`, `_ingest_queue`, `_stop`) now comes from the core — delete the duplicates.
  - [x] Overrides, and only these: `_instrument_ids()` → `{e.id for e in self._config.instruments}`; `_apply_deltas(iid, data)` → today's `:667-704` (per-level `_level_msg_id` tagging + per-side timestamps) **plus** the raw-delta catalog buffering today's `_process_data` does for `_delta_store` instruments (`:633-637`: `self._buffer[_buffer_key(data)].append(data)` only when `iid in self._delta_store` — the core never buffers deltas, so this is the one place it happens); `_handle_crossed_book(iid, book, now_ns)` → today's `:1149-1167` via `uncross.py`; `_clear_book_state(iid)` → `super()` + pop `_crossed_prices`, `_level_msg_id`; `_resync_book(iid)` → today's `:795-800` (unsub/sub orderbook via the client + `_clear_book_state`).
  - [x] Keep as dYdX methods (now `extra_loops`, bodies unchanged): `_open_interest_loop` (`:759-766`), `_subscribe`/`_unsubscribe` (`:768-778`, still used by `_apply_config`), `_apply_config`, `_status_loop`, `_publish_status`, `_reload_config_loop`, `_apply_and_persist`, `_handle_control_message`, `_publish_removed`, `_pin_top_liquid`, `_control_loop`, `_prune_loop`; module-level `_prune_*`, `_ws_raw_debug_flush_loop`, the incident-report subsystem (`:1399-1637`), `_MAX_WS_SUBSCRIPTIONS`, `_MAX_COLLECTED_INSTRUMENTS`, `_CONTROL_CHANNEL`, `_STATUS_CHANNEL`, `_CROSSED_RESYNC_NS`'s evidence comment (`:196-223` — keep the comment; the value itself is now `CoreConfig.crossed_resync_seconds`, default 10).
  - [x] **Delete** from `dydx_collector/collector.py` everything the core now owns: `_on_data`, `_ingest_loop`, `_process_data`, `_is_duplicate_trade`, `_report_stale_trades`, `_discard_second_accumulators`, `_flush_once`, `_flush_loop`, `_second_loop`, `_watchdog_loop`, `_watchdog_transition`, `_notify`, `_publish_snapshot_batch`, `run`, `stop`, the zstd patch, `quarantine_corrupt_parquet`, `_buffer_key`, `_STALE_TRADE_NS`/`_SEEN_TRADE_IDS`/`_STALE_BOOK_NS`/`_INGEST_YIELD_EVERY`/`_WATCHDOG_*`/`_SECOND_LOOP_LAG_WARN_NS`/`_IMPOSSIBLE_LOG_EVERY_NS`. Import what the module still references from `collector_core.collector`. Target: `collector.py` drops from 1723 lines to roughly 700 (control plane + incidents).
  - [x] `run()` is the core's. Two things it must still do for dYdX and does via the contract: `subscribe_global()` (Task 4) before the per-instrument subscribes, and the `Started: N subscribed` log line — keep that in the core (venue-neutral).
  - [x] `main()`: keep dYdX's `logging.basicConfig`, `_prune_stale_ws_raw_logs()`, `_IncidentHandler` and the full `init_logging(...)` call with the WS_RAW file sink + `_log_guard` (`:1639-1692`, including its comment block), then `await run_forever(lambda: DydxCollector(load_config(CONFIG_PATH)), init_rust_logging=False)`. Delete the hand-rolled restart loop (`:1697-1719`) — `run_forever` is it.
- [x] Task 4 — `dydx_collector/client.py`: satisfy the core contract additively (AC: #1)
  - [x] Add `subscribe(iid)` (= `subscribe_trades` + `subscribe_orderbook`), `unsubscribe(iid)`, `subscribe_global()` (= `subscribe_markets`), `resync_orderbook(iid)` (= `unsubscribe_orderbook` + `subscribe_orderbook`). Keep the existing per-channel methods — `_apply_config`, `_resync_book` and `tests/test_collector_control.py`'s fake client use them.
  - [x] Nothing else changes: `_at_fixed_precision` (AD-5) stays here and stays dYdX-only.
- [x] Task 5 — tests: import/attribute-path updates only (AC: #3)
  - [x] `from dydx_collector.collector import Collector` → `DydxCollector` (4 files). `_watchdog_transition`, `_WATCHDOG_REMINDER_NS`, `_publish_snapshot_batch`, `quarantine_corrupt_parquet` → import from `collector_core.collector`. `_prune_*`, `_STATUS_CHANNEL`, `_MAX_COLLECTED_INSTRUMENTS` stay in `dydx_collector.collector`.
  - [x] `tests/test_watchdog.py` and `tests/test_redis_pub.py`: delete (22.1 copied them into `collector_core/tests/`); if 22.1 did not, move them now.
  - [x] `tests/test_integration.py` is a script, not a pytest module — update its `Collector(...)` construction and leave it otherwise.
  - [x] Run `make test`; the full suite (dYdX 18 modules minus the two moved, core, Bybit, HL, common, ml_signals, ranking_engine, bot_tui, data_api) must be green. Any dYdX test that needs a behavioural change is a **regression in the core**, not a test to edit — fix the core.
- [ ] Task 6 — live verification (AC: #3)
  - [x] (done locally 2026-09-21, see operator_actions note) Local ~60 s mainnet run: `custom_dydx_second_snapshot` rows at 1 s, `candles.db` holds that coin's 1m bars after the first flush, `snapshots:raw` still carries `.DYDX` ids, at least one `collector:status` publish on startup (run-then-sleep loop, `:834-839`), a `collector:control` `stop`/`start` round-trip via `redis-cli publish`.
  - [ ] VPS: `make redeploy` (thin image); Dozzle shows `dydx-collector` healthy ≥ 10 min, `bot_tui` Collector pane populates within seconds of restart, `pin_top_liquid` works, incident reports still land in `./dydx_collector/incident_reports` on a WARNING.
  - [x] (done locally 2026-09-21, see operator_actions note) Confirm no double Rust-logging init (`init_rust_logging=False` path) — a second `init_logging` call would either error or silently replace the WS_RAW file sink.

## Dev Notes

### This is the highest-risk story of the epic — order and discipline

- Research §D: build the core from the siblings first (22.1), live-verify, **then** move dYdX. Do not start this story on an unverified core.
- The core's `_second_loop` gate is *literally* today's dYdX gate minus the uncross call. If porting dYdX exposes a difference (skip order, accumulator discard, candle-store feed timing, publish placement), the core is wrong — fix it there, with a core test, never by special-casing dYdX.
- **Diff before/after behaviour with the tests, not by reading.** The 18 modules encode two years of production incidents (crossed-book categories, stale-trade replay, reconnect resubscribe, control-action state cleanup). "Import/attribute-path updates only" is the acceptance bar precisely so those tests stay the oracle.

### What must not move into the core (venue-specific by evidence)

- Per-level message-id tagging and the uncross algorithm — dYdX's `sequence` is the connection-global `message_id`, a valid *per-level* comparator (DATA-04) and an invalid per-instrument gap detector (`_apply_deltas` docstring, commit `944891bbba`). Bybit's `u` (22.5) and Hyperliquid's `sequence=0` mean the field has three different meanings across venues.
- Liquidity tiering (`classify_liquidity`, OBS-03: USD volume, never token OI), `_MAX_WS_SUBSCRIPTIONS = 32` (dYdX's hard per-connection cap, live-confirmed error text at `:175-183`), `_MAX_COLLECTED_INSTRUMENTS = 30`, prune loops, config hot-reload + `collector:control`, incident reports + `[WS_RAW]` (only the dYdX Rust handler emits it: `crates/adapters/dydx/src/websocket/handler.rs:221`).
- `collector:status`/`collector:control` stay dYdX-only (research §B4); promoting them is a later story once the bot_tui pane is venue-aware.

### Lifecycle-cleanup checklist (Epic 1 retro action item, `sprint-status.yaml` action_items)

Every subscribe/unsubscribe/resync/shutdown transition must state what per-instrument state it resets. After this story that state is split across two classes — write it down in `_clear_book_state`'s docstring: core pops `_live_books`, `_crossed_since_ns`; dYdX adds `_level_msg_id`, `_crossed_prices`. A stale `_crossed_since_ns` surviving a resubscribe was a real false-CRITICAL + destructive-resync bug (`:780-793` docstring).

### `_process_data` split

Today dYdX buffers raw deltas for `_delta_store` instruments *and* applies them; the core only applies. The override in `_apply_deltas` re-adds the buffering for exactly those instruments. `_flush_once`'s `if dtype is OrderBookDeltas and iid not in self._delta_store: continue` guard (`:727-728`) becomes unnecessary because those deltas are never buffered — do not port it into the core.

### Precision

Unchanged: `_at_fixed_precision` re-stamps mark/index at `FIXED_PRECISION` in `dydx_collector/client.py` before anything reaches the collector (AD-5, NAUT-01). The core sees already-correct `Price`s.

### Project Structure Notes

- New: `troll/dydx_collector/uncross.py`.
- Modified: `troll/dydx_collector/{collector,config,client}.py`, `troll/dydx_collector/tests/{test_collector_snapshot,test_collector_resilience,test_collector_control,test_collector_trade_ohlc,test_config,test_integration}.py` (imports/paths), possibly `troll/config.toml` (cadence).
- Deleted: `troll/dydx_collector/tests/{test_watchdog,test_redis_pub}.py` (moved to core in 22.1).
- Unchanged: `troll/docker-compose.yml`, `troll/collector.dockerfile` (core already copied), `troll/bot_tui/**`, `troll/data_api/**`, `troll/ml_signals/**`, all catalog directory names.
- Line references in `troll/docs/DATA_INTEGRITY_AUDIT.md`, `docs/DATA_DICTIONARY.md` and the spine (`collector.py:643/735/549`) go stale — story 22.8 re-cites them; do not chase them here.

### Previous story intelligence (22.1)

Read 22.1's Dev Agent Record first: which hooks it actually shipped, the exact `run_forever` signature, whether `_watchdog_transition`'s message prefix is parameterised, and whether the Bybit/HL tests moved cleanly. Any deviation from the 22.1 story text changes Task 0.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.2] — ACs.
- [Source: _bmad-output/planning-artifacts/research/technical-multi-exchange-collector-core-and-orderbook-research-2026-09-20.md#B1, #D] — subclass shape, control plane as `extra_loops`, migration order.
- [Source: troll/dydx_collector/collector.py] — read in full; line refs above are from the 1723-line file at commit `2af978db0d`.
- [Source: troll/dydx_collector/config.py] — `InstrumentEntry`, `CollectorConfig`, `save_config`.
- [Source: troll/dydx_collector/client.py:69-140] — `DydxClient` method set.
- [Source: troll/dydx_collector/tests/*] — the 18-module oracle; attribute names poked listed in 22.1 Task 2.
- [Source: troll/CLAUDE.md DATA-02, DATA-03, DATA-04, DATA-05, DATA-07, OBS-01, OBS-03, TEST-03] — rules governing what stays venue-specific.
- [Source: ARCHITECTURE-SPINE.md#AD-1, #AD-2, #AD-4, #Deferred "Shared validator extraction"] — this story is the "structural enforcement once a second writer exists" the spine deferred.
- [Source: _bmad-output/implementation-artifacts/22-1-*.md] — core contract this story depends on.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5), bmad-dev-auto run 20260920-152042-25f1

### Debug Log References

### Completion Notes List

- `DydxCollector(collector_core.Collector)`; `collector.py` 1774 -> ~930 lines (control plane, incident reports, `main()`). Overrides exactly `_instrument_ids`, `_clear_book_state` (super + `_crossed_prices`/`_level_msg_id`), `_apply_deltas` (message-id tagging, per-side timestamps, `_delta_store` buffering, `_last_book_update_ns`), `_handle_crossed_book` (delegates to `uncross.py`), plus dYdX's `_resync_book`/`_uncross_step` thin methods. No core hook was missing.
- `uncross.py` holds the moved algorithm as functions over explicit state (docstrings and DATA-02/03/04 comments kept); the `dydx_collector.critical` logger lives there.
- `DydxConfig(CoreConfig)` is `kw_only`, `environment` derived from `network` in `__post_init__`; `snapshot_interval_seconds` default 0.5 -> 1.0 (`troll/config.toml` already had 1.0). `_CROSSED_RESYNC_NS` is now `CoreConfig.crossed_resync_seconds` (evidence comment kept).
- Client gained `subscribe`/`unsubscribe`/`subscribe_global`/`resync_orderbook` additively.
- Core change: `run()` now logs `Started: N subscribed` (venue-neutral, spec Task 3); removed the stale "until 22.2" zstd comment.
- Test edits (import/attribute paths only, plus): `Collector`->`DydxCollector`, `CollectorConfig`->`DydxConfig`, moved-symbol imports from `collector_core.collector`, `0.5`->`1.0` default assertion, `_CROSSED_RESYNC_NS` -> `int(config.crossed_resync_seconds*1e9)`. Two small non-path adaptations forced by the core's deliberate 22.1 design: `quarantine_corrupt_parquet` is now scoped per instrument id (`data/*/<iid>/*.parquet`, takes ids), so its 2 tests place the file under an iid dir and pass the id; `_buffer_key` (deleted per spec) is inlined as `(DydxSecondSnapshot, str(iid))` in test_candle_feed. `test_watchdog.py`/`test_redis_pub.py` deleted (already copied into core tests by 22.1). `test_integration.py` (script, already stale re `bar_intervals`) only had its rename.
- Small deviations: dYdX `_prune_loop` no longer calls `candle_store.prune` (the core's `_candle_prune_loop` owns it); the watchdog notify prefix is now the client class name (`DydxClient:`) instead of `dydx-collector:` (core parameterises it); the collector's status/candle DB filename comes solely from `CANDLES_DB_PATH` (compose sets `candles_dydx.db`).
- Tests (host, `cd troll && python3 -m pytest -o addopts="" --rootdir=. ...`): collector_core + dydx + bybit + hyperliquid = 159 passed, clean under `-W error::DeprecationWarning -W error::RuntimeWarning`. Full Makefile list: 596 passed, 5 failed, 3 collection errors, all pre-existing and unrelated (identical failures on the baseline commit: `ml_signals` OFI strategy tests x4, `data_api` `test_rankings` live-redis test x1 needs Redis on 6379; 3 `ml_signals` backtest modules fail to import `ml_signals.backtest_dydx`). ruff/mypy not installed on the host, not run.
- Not done (operator/Task 6): live mainnet run, VPS deploy and bot_tui verification; `sprint-status.yaml` untouched.

### File List

- New: `troll/dydx_collector/uncross.py`
- Modified: `troll/dydx_collector/{collector,config,client}.py`, `troll/collector_core/collector.py`, `troll/dydx_collector/tests/{test_candle_feed,test_collector_control,test_collector_resilience,test_collector_trade_ohlc,test_config,test_integration}.py`
- Deleted: `troll/dydx_collector/tests/{test_watchdog,test_redis_pub}.py`

## Review Triage Log

### 2026-09-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 2: (medium 2)
- reject: 20
- addressed_findings:
  - none

## Auto Run Result

Status: awaiting-operator

- Implemented: DydxCollector(collector_core.Collector), uncross.py, DydxConfig(CoreConfig), additive client contract methods; tests updated for imports/paths only. Commit 5deaaa42d4.
- Review: 0 patches, 2 deferred (pre-existing resync ordering, no snapshot-first guard), rest rejected (spec-mandated or verbatim ports, e.g. 1.0s cadence default).
- Verification: core+dYdX+Bybit+HL 159 passed (warnings as errors). Full list: 596 passed, 5 failed + 3 collection errors, all outside this change (ml_signals OFI, data_api needing Redis; 4 failures reproduced on base commit). ruff/mypy unavailable on host.
- Residual risk: Task 6 live verification not done; watchdog alert prefix now `DydxClient:`.
- followup_review_recommended: false
