# Story 22.12: Exchange-time bucketing for trades on every venue and the book on Bybit/Hyperliquid (depends on 22.13)

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Rewritten 2026-09-20 after the candle-accuracy review. The earlier version put a venue-time hold-back into the live loop and dropped late messages. That cannot make a bar correct, only later, and it contradicted the epic's "never dropped". Correctness now comes from 22.13's rebuild of closed days from raw trades on exchange time; this story defines the exchange-time semantics, extends them to the Bybit/Hyperliquid book, measures lag, and keeps the hold-back only as an optional knob.

## Story

As a strategy developer,
I want every rebuilt second and bar to hold the trades the exchange executed in that second, and the Bybit/Hyperliquid book as the exchange stamped it,
so that bars match the venues' klines bar for bar and align across venues, while the live loop stays instant.

## Acceptance Criteria

1. **Trades exchange-timed everywhere.** `rebuild_seconds` (22.13) buckets trades by venue `ts_event` into half-open `[S, S+1)` on dYdX, Bybit and Hyperliquid alike (dYdX trades carry venue time; only its book does not). This closes D-31 (boundary-minute misattribution) and D-44 (young replayed trades counted into the arrival second) for trades. `DATA_DICTIONARY.md` states `DydxSecondSnapshot` semantics per venue: trade fields exchange-timed on every venue after the nightly rebuild; book columns exchange-timed on Bybit/Hyperliquid (AC 2) and arrival-timed on dYdX (`crates/adapters/dydx/src/websocket/parse.rs` stamps `OrderBookDelta.ts_event = ts_init`); `ts_init` always arrival.
2. **Book exchange-timed on Bybit/Hyperliquid, live.** `CoreConfig.book_time_source: Literal["arrival", "venue"] = "arrival"`; Bybit and Hyperliquid configs set `"venue"`. Under `"venue"`, `_apply_deltas` applies deltas in `ts_event` order (per-instrument pending list keyed by `ts_event`, `bisect.insort`, bounded to `hold_back_seconds + 5 s` of messages, overflow = ledger + resync, MEM-02), and `_sample_tick` for second `S` uses the book as of the last delta with `ts_event < S+1`. Under `"arrival"` the path is byte-for-byte today's code (existing tests unchanged). dYdX stays `"arrival"`.
3. **Hold-back is optional, measured, and never a correctness device.** `CoreConfig.hold_back_seconds: float = 0.0` delays the live close of second `S` to wall `S + 1 + hold_back_seconds` so fewer seconds differ between the live fold and the nightly rebuild. `collector_core/measure_lag.py` (report-only; existing clients, same `on_data` shape) records `ts_init - ts_event` per venue per kind and prints p50/p99/p99.9/max after `--seconds N`; run >= 3 h per venue, recorded in the audit; the per-venue value is set from p99.9 rounded up to 0.5 s, or left at `0.0` with the reason. A trade arriving after its second closed is **not dropped**: it is archived (22.13 AC 1), counted (`collector.late_trade`, per venue, in `collector:status` and `error_ledger` once per instrument per minute), excluded from the live second, and placed by the rebuild. `ranking_engine` staleness and the OBS-01 watchdog compare on arrival (`ts_init`), so a hold-back never flags a healthy feed; `live_candles.LiveCandleBus` buckets by `ts_event` (already does) so the forming bar is simply `hold_back_seconds` late.
4. **Proven against the venues' klines.** After one full UTC day per venue has been rebuilt, `compare_klines` (22.13) reports volume equal in integer units and OHLC equal in raw price units on every minute our trade archive covers completely. Each remaining mismatch is root-caused: a missing trade is 22.14's gap, a book-related one is a finding; no tolerance. Pass rate per venue recorded in the audit.

## Tasks / Subtasks

- [ ] Task 1 — measure first (AC: #3): `collector_core/measure_lag.py`; >= 3 h per venue; audit entry with the distributions.
- [ ] Task 2 — config (AC: #2, #3): `book_time_source`, `hold_back_seconds` (validated >= 0), per-venue TOML values; dYdX untouched.
- [ ] Task 3 — venue-ordered book (AC: #2): pending list in `_apply_deltas` under `"venue"`; stale-book test compares `S` to the last applied delta's `ts_event`; crossed-book handling and dYdX's uncross override unaffected (dYdX is `"arrival"`).
- [ ] Task 4 — hold-back and late-trade accounting (AC: #3): `_second_loop` closes `S` at `S + 1 + hold_back_seconds`; late trades archived + counted + excluded live; `ranking_engine._LAST_SEEN` confirmed arrival-stamped (test with a snapshot whose `ts_event` is `hold_back` old); `data_api/live_candles.py` test that `(ts_event = S, ts_init = S + 2 s)` lands in bucket `S`; frontend live-bar freshness threshold tolerates the lag.
- [ ] Task 5 — tests (TEST-01/03, real `OrderBook`/`TradeTick`/`OrderBookDeltas`): `test_venue_time.py`: (a) delta with `ts_event = S + 0.9` arriving after `S + 0.95` is applied first; (b) a late trade is archived, counted, not in the live second, and is in the right second after `rebuild_seconds`; (c) `"arrival"` mode unchanged; (d) `seconds_observed` per minute == 60 when every second had a message; (e) candle store equivalence test still holds.
- [ ] Task 6 — reconciliation run (AC: #4) and docs: `DATA_DICTIONARY.md` per-venue clock semantics; `DATA_INTEGRITY_AUDIT.md` lag capture, pass rates, D-31/D-44 closed for trades, D-49 (dYdX book) stays documented.

## Dev Notes

### Two clocks, two jobs

`ts_event` (exchange) decides which second a trade belongs to; the rebuild uses it, so bars match the exchange. `ts_init` (arrival) decides what a strategy could have seen; backtests replay on it (Nautilus orders by `ts_init` by default), so backtest and live see the same provisional world. Never collapse the two.

### Why the live loop does not need the hold-back

Whatever the live loop gets wrong at a boundary is corrected by the nightly rebuild from raw trades. The hold-back only reduces how often live and rebuild disagree (a metric 22.13 reports). Set it from measurement, not to make reconciliation pass.

### dYdX book stays arrival-timed

Its deltas carry no venue timestamp, so a venue-timed dYdX second would pair an arrival-timed book with exchange-timed trades; `ohlc_outside_book` would then fire on honest data. Documented as D-49; revisit only if the adapter starts stamping deltas.

### Project Structure Notes

- New: `troll/collector_core/measure_lag.py`, `troll/collector_core/tests/test_venue_time.py`.
- Modified: `troll/collector_core/{config,collector}.py`, `troll/bybit_collector/config.toml`, `troll/hyperliquid_collector/config.toml`, possibly `troll/ranking_engine/engine.py`, docs.
- Depends on: 22.13 (`fold_trades`, `rebuild_seconds`, `compare_klines`, trade archive).
- Unchanged: `troll/dydx_collector/**`, `troll/ml_signals/candle_store.py`, `troll/data_api/**` (verify only).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.12, #Story 22.13] — ACs and dependency.
- [Source: crates/adapters/bybit/src/websocket/parse.rs:217,238,325; crates/adapters/hyperliquid/src/websocket/parse.rs:90,110,178; crates/adapters/dydx/src/websocket/parse.rs:520-539] — which venue stamps what.
- [Source: troll/collector_core/collector.py `_process_data` (:455), `_sample_tick` (:612), `_second_loop` (:699), `_apply_deltas` (:347)] — the paths this story branches.
- [Source: troll/ranking_engine/engine.py:100-103, :230-237] — `_WATCHLIST_STALE_NS`.
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md D-31..D-34, D-44, D-45..D-49] — what this closes and what stays open.
- [Source: troll/CLAUDE.md DATA-01, DATA-02, DATA-06, DATA-07, MEM-02, OBS-01, TEST-01/03] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
