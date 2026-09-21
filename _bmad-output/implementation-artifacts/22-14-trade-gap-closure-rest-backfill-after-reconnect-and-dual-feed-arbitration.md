# Story 22.14: Trade gap closure: REST backfill after reconnect and dual-feed arbitration

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want trades missed while a WebSocket was down to be recovered from the venue, and the remaining gap closed with a second independent feed,
so that the trade archive is complete on every venue that allows it, and the venue that does not is documented as such.

## Acceptance Criteria

1. **Reconnect detected, gap bounded.** The core records, per instrument, the `ts_event` of the last archived trade. A reconnect is detected either through a client hook (`on_reconnect` if the pyo3 client exposes one -- verify in `nautilus_pyo3.pyi`; the Rust clients reconnect silently today) or, failing that, feed silence longer than `stale_book_seconds` followed by new messages. On detection the core schedules a backfill for `[last_trade_ts - 5 s, now]` for every subscribed instrument of that connection.
2. **REST backfill per venue, stdlib only.** `collector_core/trade_backfill.py` (an `extra_loop`, `urllib`): dYdX indexer `GET /v4/trades/perpetualMarket/{ticker}?limit=100&createdBeforeOrAt=<iso>` paged backwards until `last_trade_ts`; Bybit `GET /v5/market/recent-trade?category=<linear|spot>&symbol=...&limit=1000` (most recent 1000 only -- if the oldest returned trade is newer than `last_trade_ts`, the gap is reported as `unrecoverable` with the count of seconds uncovered); Hyperliquid: none (AC 3). Returned trades are converted to `TradeTick` at the instrument's precisions (`Price.from_str`/`Quantity.from_str`, never floats), deduped by `trade_id` against the bounded set, and appended to the flush buffer with venue `ts_event` and `ts_init = now`. They are **not** folded into the live second accumulators; the nightly rebuild (22.13) places them. Counted in `collector:status` (`trade_backfill: {iid: n}`) and `error_ledger` (`collector.trade_backfill`, one entry per reconnect with instrument count and trade count). Endpoint parameters verified against the live API on the day and cited in Completion Notes.
3. **Venue capability table.** `DATA_DICTIONARY.md` gains a table: venue, trade history endpoint, depth, what a reconnect gap costs (dYdX: recoverable; Bybit: last 1000 trades per symbol; Hyperliquid: no public trade-history endpoint -- verify `{"type":"recentTrades"}` on the day and record the result -- unrecoverable from one connection). `Known limit:` with the upgrade path = AC 4.
4. **Dual-feed arbitration.** `CoreConfig.trade_feeds: int = 1`; when `2`, the venue client opens a second independent WebSocket connection subscribed to trades only (Bybit and Hyperliquid clients; dYdX optional). Both connections deliver into the same `_on_data`; the existing `trade_id` dedup takes the union (first copy archived, second counted as `duplicate_feed`, distinct from `duplicate` replay). Each feed has its own liveness timestamp; OBS-01 warns when either feed is silent while the other is not (that is a one-sided outage, the case the second feed exists for). Memory bound: the dedup set stays 2000 ids per instrument (MEM-02).
5. **Measured.** A 24 h run per venue with `trade_feeds = 2` records in the audit: trades delivered by both feeds, by one feed only (per feed), and the resulting kline pass rate from `compare_klines` before and after. A week later the pass rate is re-recorded and every remaining mismatch has a named cause.

## Tasks / Subtasks

- [ ] Task 1 — reconnect detection (AC: #1): `collector_core/collector.py` `_last_trade_ts[iid]`, reconnect signal, backfill scheduling; test with a fake client that goes silent then resumes.
- [ ] Task 2 — REST backfill (AC: #2): `trade_backfill.py` with one `fetch_trades(venue, iid, since_ns) -> list[TradeTick]` per venue; unit tests on recorded JSON fixtures (a real response per venue committed under `tests/fixtures/`); integration: kill the network for 30 s locally on Bybit and dYdX, confirm the archive has no hole in `compare_klines`.
- [ ] Task 3 — capability table (AC: #3).
- [ ] Task 4 — dual feed (AC: #4): `bybit_collector/client.py`, `hyperliquid_collector/client.py`: a second `*WebSocketClient` for trades; `collector_core` per-feed liveness; watchdog test.
- [ ] Task 5 — measurement (AC: #5) on the VPS; audit entries.

## Dev Notes

### Why REST first, dual feed second

REST backfill is cheap and closes reconnect gaps completely on dYdX. Bybit's endpoint is shallow but covers short outages on all but the busiest pairs. Hyperliquid cannot be backfilled, so only a second feed closes its gaps; the same mechanism is the industry-standard A/B arbitration and also covers the single-packet losses REST cannot see. Both are needed for "complete"; measure how much each buys.

### Do not fold backfilled trades live

They belong to seconds that were already sampled, flushed and folded into the store. The nightly rebuild is the only correct place; folding them live would double-count against the watermark (`candle_store.apply_seconds` skips seconds at or before the watermark by design).

### Project Structure Notes

- New: `troll/collector_core/trade_backfill.py`, tests + fixtures.
- Modified: `troll/collector_core/{collector,config}.py`, `troll/bybit_collector/client.py`, `troll/hyperliquid_collector/client.py`, configs, `troll/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT}.md`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.14] — ACs.
- [Source: troll/collector_core/collector.py `_is_duplicate_trade`, `_last_feed_message_ns`, watchdog (:677)] — dedup and liveness to extend.
- [Source: troll/dydx_collector/open_interest.py] — stdlib `urllib` indexer poll precedent.
- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md D-39, D-42, D-44, D-47, D-48] — replay and gap findings.
- [Source: troll/CLAUDE.md DATA-02, DATA-06, DATA-07, MEM-02, OBS-01] — rules applied.

## Dev Agent Record

### Agent Model Used

Claude Opus 5 (`claude-opus-5`), bmad-dev-auto (spec: `spec-22-14-trade-gap-closure-rest-backfill-after-reconnect-and-dual-feed-arbitration.md`).

### Debug Log References

- Live wire check, 2026-09-21: the pyo3 WS clients were run against mainnet with `trade_feeds = 2`, then compared with the REST endpoints over the same interval.
- Local 30 s network cut, 2026-09-21: real collectors wrote into a scratch catalog on a Docker network that was disconnected and reconnected.

### Completion Notes List

- **Endpoint parameters, verified against the live API on 2026-09-21 (AC 2):**
  - **dYdX:** `GET https://indexer.dydx.trade/v4/trades/perpetualMarket/{ticker}?limit=1000&createdBeforeOrAt=<iso>`.
    - `limit=1000` works (the AC's 100 does too). The response is newest first, and `createdBeforeOrAt` is inclusive.
    - The indexer returns 403 for urllib's default User-Agent.
  - **Bybit:** `GET https://api.bybit.com/v5/market/recent-trade?category=linear|spot&symbol=...&limit=1000`.
    - Linear returns 1000 trades: 28-64 s of BTCUSDT in the two samples.
    - **Spot returns at most 60 trades**, even with `limit=1000` (audit D-60).
  - **Hyperliquid:** `POST https://api.hyperliquid.xyz/info {"type":"recentTrades","coin":"BTC"}` exists.
    - It returns **exactly the last 10 trades** and ignores `startTime` (audit D-48).
- **WS trade ids equal REST trade ids on every venue:**
  - Bybit: linear 999/999, spot 60/60.
  - Hyperliquid: 19/19.
  - dYdX: 3/3.
  - Price, size and aggressor are equal on every one.
  - The two feeds of `trade_feeds = 2` delivered identical id sets.
- **Hyperliquid's live `ts_event` is up to 128 ns off the exact millisecond.** The adapter converts through `f64`. The Hyperliquid client now re-stamps it to the exact millisecond (audit D-62).
- **Network-cut test:**
  - dYdX: archive == REST for the outage window.
  - Bybit ADAUSDT: 155/155.
  - Bybit BTCUSDT linear and ETHUSDT spot: backfilled, with the rest reported `unrecoverable` (venue depth).
  - Hyperliquid: `unrecoverable` (10-trade depth).
  - The first run exposed a baseline bug, since fixed: the baseline was read when the backfill ran instead of at detection.
- **AC 5 is owed on the VPS:** the 24 h and 1-week measurements are listed as the spec's `operator_actions`.

### File List

- New:
  - `troll/collector_core/feed.py`
  - `troll/collector_core/trade_backfill.py`
  - `troll/collector_core/venue_http.py`
  - `troll/collector_core/tests/test_trade_backfill.py`
  - `troll/collector_core/tests/fixtures/{dydx_trades_btc_usd,bybit_trades_btcusdt_linear,bybit_trades_btcusdt_spot,hyperliquid_recent_trades_btc}_20260921.json`
  - `troll/bybit_collector/tests/test_client.py`
  - `troll/hyperliquid_collector/tests/test_client.py`
- Modified, code:
  - `troll/collector_core/{collector,config,compare_klines,archive_gaps}.py`
  - `troll/bybit_collector/{client,collector}.py`
  - `troll/hyperliquid_collector/{client,collector}.py`
  - `troll/dydx_collector/collector.py`
- Modified, config:
  - `troll/bybit_collector/config.toml`
  - `troll/hyperliquid_collector/config.toml`
- Modified, tests:
  - `troll/collector_core/tests/{test_collector,test_config,test_watchdog}.py`
  - `troll/dydx_collector/tests/test_collector_resilience.py`
- Modified, docs:
  - `troll/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT,DEPLOY_CHECKLIST}.md`
  - `troll/CLAUDE.md`
