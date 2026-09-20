# Story 22.12: Venue-timed 1s sampling for Bybit and Hyperliquid (dYdX stays arrival-timed)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a strategy developer,
I want Bybit and Hyperliquid snapshots and candles stamped with the venue's own event time,
so that a bar holds the trades the venue did in that second, not the trades that happened to arrive in it, and bars align across venues.

## Acceptance Criteria

1. **Per-venue time source.** `CoreConfig` gains `time_source: Literal["arrival", "venue"] = "arrival"` and `hold_back_seconds: float = 0.0`. Bybit and Hyperliquid configs set `time_source = "venue"`; dYdX stays `"arrival"` (its book deltas carry no venue timestamp — `crates/adapters/dydx/src/websocket/parse.rs` stamps `OrderBookDelta.ts_event = ts_init`). Under `"arrival"` the gate is byte-for-byte today's behaviour (existing tests unchanged).
2. **Venue-timed second.** Under `"venue"`, the snapshot for second `S` (UTC, integer) is built from messages whose venue `ts_event` falls in `[S, S+1)`: the book as of the last venue-timed delta with `ts_event < S+1` applied in venue-time order, and the trades with `ts_event` in `[S, S+1)`. `snapshot.ts_event == S * 1e9` exactly, `ts_init` = wall clock at build. Second `S` is closed and emitted at wall time `≥ S + 1 + hold_back_seconds`, never earlier. One snapshot per wall second is still emitted (`seconds_observed` stays one per second in the candle store); a second with no venue message at all produces no row (same "no row for a skipped second" contract as today).
3. **Late messages are counted, never dropped or misattributed.** A trade or delta arriving after its second has closed is recorded via `error_ledger.record("collector.late_message", f"{iid} {kind} late by {ms} ms")` and a per-venue counter published in `collector:status`; it is not applied to a later second. `hold_back_seconds` per venue is set from a measured capture of `ts_init - ts_event` (p99.9 over ≥ 3 h, both venues, trades and book), written into `troll/docs/DATA_INTEGRITY_AUDIT.md` as a new D-entry, with the capture script committed (`collector_core/measure_lag.py`, report-only, stdlib + the existing WS client).
4. **Downstream tolerates the hold-back.** `ranking_engine`'s per-instrument staleness (`_WATCHLIST_STALE_NS`, 30 s) and the collector's OBS-01 watchdog compare against `ts_init`/arrival, not `ts_event`, so a venue-timed feed that is `hold_back_seconds` old by `ts_event` is not flagged stale; `live_candles.LiveCandleBus` buckets a venue-timed snapshot by its `ts_event` (it already does) so the forming bar is simply `hold_back_seconds` late, and the chart's "live" marker is not shown as stale for that lag.
5. **Proven against the venues' own candles.** For one full UTC day per venue (captured on the VPS or locally), the candle store's 1m bars (`candle_store.window(..., 60, ...)`) are compared bar-for-bar against Bybit `GET /v5/market/kline` (interval `1`) and Hyperliquid `POST /info {"type":"candleSnapshot"}` (interval `1m`): volume equal within 1e-9 of the venue's quantity precision on ≥ 99.9 % of bars, OHLC within one tick, and every mismatch listed with its cause. The comparison script is committed (`collector_core/compare_klines.py`) and the result recorded in the audit. dYdX is documented there as arrival-timed with up to ~3 s boundary misattribution (D-31..D-34) until its own story.

## Tasks / Subtasks

- [ ] Task 1 — measure first (AC: #3)
  - [ ] `collector_core/measure_lag.py`: subscribe (existing `BybitClient`/`HyperliquidClient`, same `on_data` shape), for each `TradeTick` and `OrderBookDeltas` record `ts_init - ts_event`; print p50/p99/p99.9/max per venue per kind after `--seconds N`. Run ≥ 3 h each; record in the audit; pick `hold_back_seconds` = p99.9 rounded up to the next 0.5 s (expect ~1–3 s; verify, don't assume).
- [ ] Task 2 — config (AC: #1)
  - [ ] `collector_core/config.py`: the two fields, validated (`time_source` in the literal, `hold_back_seconds >= 0`, and `time_source == "venue"` requires `hold_back_seconds > 0`). `bybit_collector/config.toml`, `hyperliquid_collector/config.toml`: `time_source = "venue"`, `hold_back_seconds = <measured>`. dYdX untouched.
- [ ] Task 3 — the venue-timed gate (AC: #2, #3)
  - [ ] `collector_core/collector.py`: under `"venue"`, `_process_data` no longer applies deltas/trades immediately. Deltas and trades go into a per-instrument, venue-time-ordered pending buffer (`heapq` or `sortedcontainers` is NOT available — use `bisect.insort` on a list keyed by `ts_event`; MEM-02: bounded to `hold_back_seconds + 5 s` of messages, overflow = ledger + resync). `_second_loop` at wall time `T` closes every second `S ≤ T - 1 - hold_back_seconds` not yet closed: apply pending deltas with `ts_event < S+1` to the live book (same `_apply_deltas` hook — dYdX's uncross override is unaffected because dYdX stays `"arrival"`), fold trades in `[S, S+1)` into the second accumulators, then build the snapshot exactly as `_sample_tick` does today with `now_ns = S * 1e9`.
  - [ ] Stale-book test (`stale_book_seconds`) compares `S` against the last applied delta's `ts_event`, not wall clock; crossed-book handling unchanged.
  - [ ] Late arrivals (`ts_event < last closed second`): counted per venue/kind, ledgered once per instrument per minute (rate-limited like `_IMPOSSIBLE_LOG_EVERY_NS`), and dropped from the book/trade path with the reason in the ledger text (this is the *only* drop, and it is loud — DATA-07).
  - [ ] `snapshots:raw` publish and the candle-store feed unchanged (they consume the built snapshot).
- [ ] Task 4 — downstream (AC: #4)
  - [ ] `ranking_engine/engine.py`: confirm `_LAST_SEEN` is stamped from arrival (`ts_init` or receive time). If it uses `ts_event`, switch it and add a test with a snapshot whose `ts_event` is `hold_back` old.
  - [ ] `data_api/live_candles.py`: no change expected; add a test that a venue-timed snapshot (ts_event = S, ts_init = S + 2 s) lands in bucket S.
  - [ ] `frontend`: the chart's live-bar freshness indicator (if any threshold < 5 s exists) tolerates the hold-back — grep `stale` in `frontend/src/hooks/useLiveCandle.ts` and `LightweightChart.tsx`.
- [ ] Task 5 — tests (TEST-01/03; real `OrderBook`, `TradeTick`, `OrderBookDeltas`, `ParquetDataCatalog`)
  - [ ] `collector_core/tests/test_venue_time.py`: (a) a trade with `ts_event = S + 0.8 s` arriving at wall `S + 2.1 s` lands in second `S`, not `S+2`; (b) a delta with `ts_event = S + 0.9` arriving after a delta with `ts_event = S + 0.95` is applied first (venue order); (c) a message later than the hold-back is ledgered and not applied; (d) `seconds_observed` per minute == 60 when every second had a message; (e) `"arrival"` mode: the existing gate tests pass unchanged; (f) the equivalence test in `ml_signals/tests/test_candle_store.py` still holds (store unaware of the time source).
- [ ] Task 6 — kline comparison (AC: #5)
  - [ ] `collector_core/compare_klines.py --venue BYBIT|HYPERLIQUID --instrument <id> --date YYYY-MM-DD --db candles_<venue>.db`: fetch the venue's 1m klines (Bybit `BybitHttpClient` pyo3 `request_bars` is fine; Hyperliquid `POST https://api.hyperliquid.xyz/info` via stdlib `urllib`), join on bar start, print per-bar diffs and the summary. Run for one day per venue after Task 3 has run ≥ 24 h; record in the audit. A systematic offset (every bar shifted by one) means the second boundary or hold-back is wrong — fix, don't tolerate.
- [ ] Task 7 — docs
  - [ ] `troll/docs/DATA_DICTIONARY.md`: `DydxSecondSnapshot.ts_event` semantics per venue (venue time for Bybit/Hyperliquid, arrival time for dYdX); `ts_init` always arrival. `DATA_INTEGRITY_AUDIT.md`: lag capture, kline comparison, dYdX asymmetry entry. `troll/CLAUDE.md` DATA-01: one sentence on the two clocks.

## Dev Notes

### Why only Bybit and Hyperliquid

Both venues stamp *both halves* of a second with venue time: Bybit `trade.T` and `orderbook.ts` (`crates/adapters/bybit/src/websocket/parse.rs:217, :238, :325`), Hyperliquid `trade.time` and `book.time` (`crates/adapters/hyperliquid/src/websocket/parse.rs:90, :110, :178`). dYdX's book deltas carry no venue time, so a venue-timed dYdX second would hold a book sampled on one clock and trades on another; that design and its `ohlc_outside_book` consequences are deferred (operator decision 2026-09-20).

### The one real piece of new logic

Applying deltas on arrival (today) vs. in venue-time order after a hold-back (this story). Everything after the book-and-accumulators step — snapshot build, `ohlc_outside_book`, Redis publish, Parquet buffer, candle store — is unchanged and must stay unchanged (AD-1: one write gate). Keep the `"arrival"` path literally today's code; branch once, early, on `self._config.time_source`.

### Hold-back is latency the feed already has

A trade shown at `S` under arrival time was really `1–3 s` old; venue time makes that visible instead of hiding it. The chart's forming bar and `snapshots:raw` consumers see the same data `hold_back_seconds` later than today — say so in the docs rather than shrinking the hold-back to hide it.

### Subscribe-time replay (DATA-06) interacts well

Replayed history has old `ts_event`; the existing `stale_trade_seconds` filter still discards it before the pending buffer, and `trade_id` dedup still applies. Keep both; add a test that a replayed trade older than `stale_trade_seconds` never reaches the pending buffer.

### Cadence assumption in the store

`candle_store` counts one observed second per snapshot; `seconds_observed` and the `partial` flag are only right at 1.0 s cadence (22.1 unified it). This story emits exactly one snapshot per UTC second per instrument that had a message — preserve that.

### Project Structure Notes

- New: `troll/collector_core/{measure_lag,compare_klines}.py`, `troll/collector_core/tests/test_venue_time.py`.
- Modified: `troll/collector_core/{config,collector}.py`, `troll/bybit_collector/config.toml`, `troll/hyperliquid_collector/config.toml`, possibly `troll/ranking_engine/engine.py`, docs.
- Unchanged: `troll/dydx_collector/**` (arrival-timed), `troll/ml_signals/candle_store.py`, `troll/data_api/**` (verify only).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.12] — ACs.
- [Source: crates/adapters/bybit/src/websocket/parse.rs:217,238,325; crates/adapters/hyperliquid/src/websocket/parse.rs:90,110,178; crates/adapters/dydx/src/websocket/parse.rs:520-539] — which venue stamps what.
- [Source: troll/collector_core/collector.py `_process_data` (:448, stale-trade filter :455), `_sample_tick` (:605), `_second_loop` (:679), `_apply_deltas` (:340)] — the gate this story branches.
- [Source: troll/collector_core/config.py:32-40] — `CoreConfig` fields.
- [Source: troll/ranking_engine/engine.py:100-103, :230-237] — `_WATCHLIST_STALE_NS` staleness.
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md D-31..D-34] — the arrival-time boundary misattribution this fixes for two venues.
- [Source: troll/CLAUDE.md DATA-01, DATA-02, DATA-06, DATA-07, MEM-02, OBS-01, TEST-01/03] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
