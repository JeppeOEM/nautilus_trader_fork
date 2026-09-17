# Story 19.4: `troll/hyperliquid_collector/`

Status: ready-for-dev

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

- [ ] Task 1 — Investigate Hyperliquid's actual wire behavior (AC: #2)
  - [ ] Read `crates/adapters/hyperliquid/src/` (http/websocket/common modules) to independently confirm: book-crossing behavior, price-precision stability, whether open interest is forwarded through the PyO3 bindings, and Hyperliquid's own documented WS subscription limits — do not assume any answer from Stories 19.3's Bybit investigation or dYdX's own known quirks transfers over; Hyperliquid is architecturally distinct from both.
- [ ] Task 2 — `HyperliquidClient` wrapper (AC: #1)
  - [ ] New `troll/hyperliquid_collector/client.py`, mirroring `DydxClient`'s/`BybitClient`'s shape (own HTTP+WS PyO3 client pair, subscribe methods, `_handle_message` dispatch) — precision/re-stamping logic only if Task 1 justifies it, in whatever form Hyperliquid's actual wire format needs.
- [ ] Task 3 — `Collector` class (AC: #1, #3, #4)
  - [ ] New `troll/hyperliquid_collector/collector.py`: asyncio loop, ingest-queue pattern, flush loop via `ParquetDataCatalog.write_data()`, a periodic snapshot sampler if Hyperliquid's book model supports the same shape — crossed-book/resync handling and an open-interest poll workaround only if Task 1's investigation shows they're needed, and built against Hyperliquid's actual failure modes.
- [ ] Task 4 — Tests
  - [ ] TEST-01: any precision-handling logic uses real `Price`/`Quantity` objects.
  - [ ] An integration test confirming a real (or realistically-simulated) Hyperliquid snapshot round-trips through the catalog with a `.HYPERLIQUID`-suffixed instrument id.

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

### File List
