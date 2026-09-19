# Story 19.3: `troll/bybit_collector/`

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want a Bybit market-data collector with the same reliability properties as the dYdX collector,
so that Bybit data lands in the same catalog without inheriting Nautilus's live-runtime OOM/wedge bug.

## Acceptance Criteria

1. **`troll/bybit_collector/` owns its own asyncio loop, in-memory buffer, and flush timer** calling `BybitHttpClient`/`BybitWebSocketClient` (`nautilus_trader/core/nautilus_pyo3.pyi`, backed by `crates/adapters/bybit/`) directly — never instantiating `TradingNode`/`DataEngine` (`troll/CLAUDE.md` FORK-02).
2. **Structural pattern mirrored from `dydx_collector`, confirmed by reading its full `client.py` and most of `collector.py` this session — but only the genuinely venue-agnostic parts, listed explicitly below (AC #3), not the whole file wholesale.**
3. **dYdX-specific logic is NOT ported without first checking whether it applies to Bybit**, specifically:
   - The crossed-book detection/active-uncrossing/resync machinery (`_handle_crossed_book`, `_try_active_uncross`, `_resync_book`, `_CROSSED_RESYNC_NS`, etc.) exists because dYdX v4 has no centralized orderbook — each validator holds its own pre-consensus book (`troll/CLAUDE.md` DATA-04). Bybit is a centralized exchange with a real matching engine; a crossed book from Bybit's own feed would be a genuine anomaly, not an expected architectural condition — do not port dYdX's "crossed book is normal" handling as-is. Investigate Bybit's own book-consistency behavior before deciding whether *any* uncrossing logic is needed, and if so, what triggers it.
   - `_at_fixed_precision()`'s dYdX-specific rationale (mark/index price precision derived from trailing-zero-stripped digit count, `crates/adapters/dydx/src/common/parse.rs`) is a dYdX wire-format quirk. Verify Bybit's own price feed's precision behavior (via `crates/adapters/bybit/`) before assuming the same re-stamping workaround is needed — it may not be, or may need a differently-shaped fix.
   - `_MAX_WS_SUBSCRIPTIONS = 32` is dYdX's own documented per-connection limit. Bybit's WebSocket subscription limits are a separate, unverified number — do not reuse `32` without checking Bybit's own API documentation/adapter.
   - `open_interest.py`'s stdlib REST-poll workaround exists because dYdX's Rust/PyO3 bindings specifically drop `open_interest` (confirmed via `crates/adapters/dydx/src/python/{http,websocket}.rs`). Check whether `BybitHttpClient`/`BybitWebSocketClient` already forward open interest before building an equivalent workaround — it may not be needed at all for Bybit.
4. **What genuinely IS venue-agnostic and should be mirrored:** the overall shape — a thin `BybitClient` wrapper class (mirrors `DydxClient`: owns the HTTP+WS PyO3 client pair, exposes `subscribe_trades`/`subscribe_orderbook`/etc., a `_handle_message` callback dispatching by message type) plus a `Collector` class (own asyncio loop via `_ingest_loop`'s queue-then-process pattern to keep the WS callback O(1), `_flush_loop` writing buffered items via `asyncio.to_thread(self._catalog.write_data, items)`, a `_second_loop`-style periodic snapshot sampler if Bybit's book data model supports the same top-N-levels-plus-trade-volume snapshot shape `DydxSecondSnapshot` uses).
5. **All writes go through `ParquetDataCatalog.write_data()`** (NAUT-02) — no hand-rolled Parquet schema, writing `SYMBOL.BYBIT`-suffixed instrument ids into the shared catalog root (Story 19.2).
6. **No shared base class with `dydx_collector` is created in this story** — a sibling module, per DESIGN-01; common helpers get extracted later, only once genuine duplication across all three collectors (dYdX/Bybit/Hyperliquid) is visible.

## Tasks / Subtasks

- [x] Task 1 — Investigate Bybit's actual wire behavior before writing any collector code (AC: #3)
  - [x] Read `crates/adapters/bybit/src/` (http/websocket modules) to confirm: does Bybit's own book data ever legitimately cross? Does its price feed have the same precision-instability dYdX's does? Does `open_interest` come through the PyO3 bindings already? Does Bybit publish its own documented WS subscription limit? Answer each before Task 2.
- [x] Task 2 — `BybitClient` wrapper (AC: #1, #4)
  - [x] New `troll/bybit_collector/client.py`, mirroring `DydxClient`'s shape (own HTTP+WS PyO3 client pair, `subscribe_trades`/`subscribe_orderbook`, `_handle_message` dispatch) — only include a precision re-stamping helper if Task 1 confirms Bybit needs one, and only in the form Task 1's investigation justifies.
- [x] Task 3 — `Collector` class (AC: #1, #2, #4, #5)
  - [x] New `troll/bybit_collector/collector.py`: own asyncio loop, `_ingest_loop` queue pattern, `_flush_loop`/`ParquetDataCatalog.write_data()`, a periodic snapshot sampler analogous to `_second_loop` if Bybit's book model supports it — include crossed-book/resync handling only if Task 1's investigation actually finds Bybit needs it, built to match what Bybit's real failure modes are, not a copy of dYdX's.
  - [x] `config.py`/`open_interest.py`-equivalents only if Task 1 shows Bybit needs an out-of-band open-interest poll — otherwise skip building this module at all.
- [x] Task 4 — Tests
  - [x] TEST-01: any precision-handling logic gets real `Price`/`Quantity`-object tests, never mocked (`troll/CLAUDE.md` TEST-03).
  - [x] An integration test confirming a real (or realistically-simulated) Bybit snapshot round-trips through `ParquetDataCatalog.write_data()` and is readable back with a `BTCUSDT.BYBIT`-style instrument id.

## Dev Notes

- **The single biggest risk in this story is treating `dydx_collector` as a template to copy-paste rather than a reference architecture to selectively mirror.** Every dYdX-specific quirk listed in AC #3 exists for a documented, dYdX-specific reason — porting any of them to Bybit without first verifying the same reason applies would be wasted work at best and a wrong fix for a problem Bybit doesn't have at worst.
- **This story explicitly does not build a shared base class with `dydx_collector`.** If, after this story and Story 19.4 both exist, real duplication turns out to be substantial and genuinely identical (not just superficially similar), that extraction is a future decision — not made here (DESIGN-01, and the multi-exchange spec's own explicit guidance).

### Project Structure Notes

- New: `troll/bybit_collector/client.py`, `troll/bybit_collector/collector.py`, and any config/open-interest modules Task 1's investigation justifies.
- Not modified: `troll/dydx_collector/*` (read for reference, never imported from).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.3] — this story's origin (FR63).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D4] — the "mirror dydx_collector's architecture, not scripts/bybit_recorder's" decision, confirmed by the user.
- [Source: troll/dydx_collector/client.py] — full file read this session; `DydxClient`'s shape, `_at_fixed_precision()`'s dYdX-specific rationale.
- [Source: troll/dydx_collector/collector.py] — most of the file read this session (1651 lines total); `Collector`'s asyncio-loop/ingest-queue/flush pattern, and the crossed-book/resync machinery this story explicitly does not port without justification.
- [Source: troll/dydx_collector/second_snapshot.py, open_interest.py] — headers read this session; `DydxSecondSnapshot`'s shape and the dYdX-specific reason `open_interest.py` exists (Rust adapter drops the field).
- [Source: troll/CLAUDE.md DATA-04] — the crossed-book architectural explanation this story's AC #3 relies on to justify NOT porting that logic to Bybit.
- [Source: nautilus_trader/core/nautilus_pyo3.pyi] — confirmed `BybitHttpClient`/`BybitWebSocketClient` exist, backed by `crates/adapters/bybit/`.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Task 1 findings (from crates/adapters/bybit): (a) precision — mark/index parsed at `instrument.price_precision()`, a per-instrument constant, so no `_at_fixed_precision` equivalent; (b) open interest — dropped on the linear-ticker WS path (only option-greeks parse reads it) → stdlib REST poll of `/v5/market/tickers?category=linear` (one call, all symbols), `BybitOpenInterest` type; (c) sub limits — one WS request per topic, no documented per-second public subscribe limit → no cap/throttle, `32` not reused; (d) crossed book — Bybit has a central book so a cross is corruption: skip the sample, and after 10s resubscribe the orderbook for a fresh snapshot (no active-uncross machinery).
- Snapshots reuse `DydxSecondSnapshot` (venue-neutral schema) so data_api reads Bybit ids with zero per-route code — the integration test reads back through `ml_signals.catalog_stats.query_second_snapshots`. Only this schema class is imported from dydx_collector; no base class (AC6).
- Left out vs dYdX (not in ACs): pruning, Redis status/control, watchdog, incident reports, minute rollup, config live-reload. Ids are the adapter's `SYMBOL-LINEAR.BYBIT` (not `BTCUSDT.BYBIT`).
- Verified live against mainnet: 879 linear instruments fetched, 20s run wrote 30 BTCUSDT snapshots (20 levels) readable via data_api's reader.
- Added compose service `bybit_collector` + Dockerfile COPY + Makefile test path (compose not brought up here). 6 new tests pass.

### File List

- troll/bybit_collector/{__init__,client,collector,config,open_interest}.py, config.toml, tests/{__init__,test_collector}.py (new)
- troll/collector.dockerfile, troll/docker-compose.yml, troll/Makefile
