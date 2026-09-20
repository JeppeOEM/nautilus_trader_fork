# Story 19.4: `troll/hyperliquid_collector/`

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the platform operator,
I want a Hyperliquid market-data collector with the same architecture as the Bybit and dYdX collectors,
so that Hyperliquid data lands in the same catalog consistently.

## Acceptance Criteria

1. **`troll/hyperliquid_collector/` follows the exact same direct-asyncio-PyO3 pattern as Story 19.3's Bybit collector** — own asyncio loop, buffer, flush timer, calling `HyperliquidHttpClient`/`HyperliquidWebSocketClient` (`nautilus_trader/core/nautilus_pyo3.pyi`, backed by `crates/adapters/hyperliquid/`) directly, never `TradingNode`/`DataEngine`.
2. **Hyperliquid-specific wire behavior is investigated before assuming any dYdX or Bybit quirk applies** — same discipline as Story 19.3's AC #3, applied fresh: Hyperliquid is (like dYdX) an on-chain perpetuals DEX, but a *different* chain/architecture than dYdX v4 — do not assume dYdX's crossed-book architectural rationale (DATA-04, specific to dYdX v4's validator-local pre-consensus books) applies to Hyperliquid just because both are DEXes. Investigate Hyperliquid's own book-consistency model independently.
3. **A sibling module, not a shared base class with `dydx_collector` or `bybit_collector`** — per DESIGN-01, extract common helpers only once real duplication across all three is visible after this story lands.
4. **All writes go through `ParquetDataCatalog.write_data()`**, writing instrument ids suffixed `.HYPERLIQUID` into the shared catalog root (Story 19.2).

## Tasks / Subtasks

- [x] Task 1 — Investigate Hyperliquid's actual wire behavior (AC: #2)
  - [x] Read `crates/adapters/hyperliquid/src/` (http/websocket/common modules) to independently confirm: book-crossing behavior, price-precision stability, whether open interest is forwarded through the PyO3 bindings, and Hyperliquid's own documented WS subscription limits — do not assume any answer from Stories 19.3's Bybit investigation or dYdX's own known quirks transfers over; Hyperliquid is architecturally distinct from both.
- [x] Task 2 — `HyperliquidClient` wrapper (AC: #1)
  - [x] New `troll/hyperliquid_collector/client.py`, mirroring `DydxClient`'s/`BybitClient`'s shape (own HTTP+WS PyO3 client pair, subscribe methods, `_handle_message` dispatch) — precision/re-stamping logic only if Task 1 justifies it, in whatever form Hyperliquid's actual wire format needs.
- [x] Task 3 — `Collector` class (AC: #1, #3, #4)
  - [x] New `troll/hyperliquid_collector/collector.py`: asyncio loop, ingest-queue pattern, flush loop via `ParquetDataCatalog.write_data()`, a periodic snapshot sampler if Hyperliquid's book model supports the same shape — crossed-book/resync handling and an open-interest poll workaround only if Task 1's investigation shows they're needed, and built against Hyperliquid's actual failure modes.
- [x] Task 4 — Tests
  - [x] TEST-01: any precision-handling logic uses real `Price`/`Quantity` objects.
  - [x] An integration test confirming a real (or realistically-simulated) Hyperliquid snapshot round-trips through the catalog with a `.HYPERLIQUID`-suffixed instrument id.

## Dev Notes

- **Same caution as Story 19.3, restated because it's easy to under-weight the second time:** having just built a Bybit collector, the temptation is to treat *that* as the new template instead of dYdX's — resist both. Hyperliquid needs its own from-Task-1 investigation; two prior collectors existing doesn't make a third one's quirks predictable.
- **This story should land after Story 19.3**, not because of a technical dependency, but so any genuine cross-collector pattern Story 19.3 discovers (e.g. "the ingest-queue-then-process shape works identically for a CEX-style feed too") is available as a confirmed precedent rather than an assumption.

### Project Structure Notes

- New: `troll/hyperliquid_collector/client.py`, `troll/hyperliquid_collector/collector.py`, and any config/open-interest modules Task 1's investigation justifies.
- Not modified: `troll/dydx_collector/*`, `troll/bybit_collector/*` (read for reference, never imported from).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.4] — this story's origin (FR64).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D4] — the architecture-mirroring decision, extended to Hyperliquid.
- [Source: troll/dydx_collector/client.py, collector.py] — read this session (Story 19.3); the structural pattern this story's collector also follows, at the same level of "mirror the shape, verify the quirks" discipline.
- [Source: nautilus_trader/core/nautilus_pyo3.pyi] — confirmed `HyperliquidHttpClient`/`HyperliquidWebSocketClient` exist, backed by `crates/adapters/hyperliquid/`.
- [Source: _bmad-output/implementation-artifacts/19-3-troll-bybit-collector.md] — the sibling story this one deliberately does not become a shared base class with (per DESIGN-01), and the investigation discipline it repeats independently for Hyperliquid.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Task 1 findings (crates/adapters/hyperliquid + live probe), independent of dYdX/Bybit: (a) every `l2Book` payload is a full snapshot (Clear + levels), so the local book cannot drift → crossed sample is skipped, next message replaces the book, no resync/uncross machinery; (b) mark/index/book prices parsed at `instrument.price_precision()` → no re-stamping; (c) open interest IS forwarded over WS (`subscribe_open_interest` → pyo3 CustomData) → no REST poll, own `HyperliquidOpenInterest` Data type registered for Arrow; (d) no per-second subscribe limit needed for a handful of instruments.
- Empirical finding: l2Book pushes arrived only ~every 5s in the live probe, so the stale-book guard is config (`stale_book_seconds`, default 30) and the 1s sampler repeats the last authoritative book between pushes. Worth revisiting if `subscribe_book` can be given finer cadence.
- Same shape as bybit_collector but written as its own sibling (no shared code beyond `DydxSecondSnapshot`'s venue-neutral schema, which lets data_api serve `.HYPERLIQUID` ids untouched). Ids are `SYMBOL-USD-PERP.HYPERLIQUID`.
- Live 25s run against mainnet: 45 BTC snapshots (20 levels) readable via data_api's reader, plus mark/funding/OI rows in the catalog. 6 new tests pass (12 with Bybit's). Compose service, Dockerfile COPY, Makefile updated (compose not brought up).
- Sibling-not-base deferral (AC #3) closed by Epic 22 (22.1: `collector_core`) — `HyperliquidCollector` now subclasses `collector_core.collector.Collector` overriding no core *hook* — only `__init__`, to supply its client (`hyperliquid_collector/collector.py:42`).

### File List

- troll/hyperliquid_collector/{__init__,client,collector,config,open_interest}.py, config.toml, tests/{__init__,test_collector}.py (new)
- troll/collector.dockerfile, troll/docker-compose.yml, troll/Makefile
