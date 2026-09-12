# Epic 13 Context: ranking_engine Memory/CPU Stabilization

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

`ranking_engine` OOM-restarts roughly every 2 minutes on nifelheim (confirmed via `docker events` as a real host OOM-kill, not an app-level exit). The root mechanism: every 60-second cycle, its periodic metrics computation re-scans a full 25-hour Parquet window, up to 32-way concurrent, per instrument — for `DydxSecondSnapshot` data that is already streaming live through Redis and already being ingested into `ranking_engine`'s own in-memory state, just discarded there after only 5 minutes. This epic first bounds the immediate concurrency spike (fast, independent, ships alone) and then removes the recurring Parquet re-scan entirely by holding a long-window price series in memory, backfilled once at process startup.

## Stories

- Story 13.1: Bound `compute_all()`'s catalog-read concurrency
- Story 13.2: In-memory long-window price series, replacing the recurring Parquet re-scan

## Requirements & Constraints

- The periodic (60s-cycle) catalog-read concurrency must be capped at a small, fixed worker count — not scaled to instrument count as it is today.
- Lowering concurrency must not change results: same instruments, same 25h lookback, same returned values — only the number of concurrent in-flight catalog reads changes. Total per-cycle wall-clock time may grow but must stay well under the 60s cycle interval at current instrument count (~29).
- The periodic cycle's `price`/`pct_1h`/`pct_24h`/`volatility` fields must ultimately be computed from data already held in memory rather than re-reading the catalog every cycle. Only one Parquet backfill read per instrument is allowed, at process startup (or restart) — never on the recurring cycle.
- The in-memory long-window series must be fed incrementally from the same live snapshot feed already being consumed, must not duplicate or diverge from the engine's existing short-window bookkeeping, and must evict entries older than the lookback window in O(1).
- Computed values (`pct_1h`, `pct_24h`, `volatility`) must numerically match the existing Parquet-backed computation's formulas and semantics exactly, including its "not enough history yet → `None`" guard — verified side by side against the existing path before that path is removed, not just by code inspection.
- Both stories require real before/after operational evidence (RSS/memory measurements, OOM-restart counts) to verify — not a code-review-only claim. Report actual numbers; if the host is still resource-oversubscribed at rest even after both fixes, that must be stated explicitly rather than claiming full resolution.
- Financial-calculation and catalog-integration code in this epic requires test coverage using real Nautilus/catalog objects (never mocked): ring-buffer append/evict behavior, the startup-backfill-then-incremental-update sequence, and the in-memory `pct`/`volatility` computation matching the existing catalog-backed computation's output for equivalent input.

## Technical Decisions

- `ranking_engine` is the sole computer and sole publisher of derived ranking data; readers (dashboard, bot_tui) never recompute it independently — this epic's changes must preserve that property, not introduce a second computation path readers might diverge against.
- `ranking_engine`, like the rest of this data pipeline, uses `nautilus_trader` purely as a library — never instantiates `TradingNode`/`Strategy`/`DataEngine` — even while restructuring how it reads/holds price data.
- No unbounded catalog reads; the new long-window structure must stay a bounded, evicting buffer, not unbounded accumulation.
- The long-window price series should be implemented as numpy arrays of `(ts_event_ns, close_price)`, not Python-boxed tuples/deques, to keep steady-state memory low (~40MB across all instruments at 25h retention) versus the current spiky multi-instrument-concurrent-DataFrame peak.
- The startup backfill must reuse the existing price-series/query helper used by the current Parquet-backed path rather than reimplementing it.
- A snapshot contributes to the price series only when it carries a trade `close_price` (seconds with no trade contribute nothing) — matching current semantics exactly.

## Cross-Story Dependencies

- Story 13.2 depends on Story 13.1 landing first, so the concurrency mitigation stays in place while the larger in-memory-series change is built and reviewed. Story 13.1 is independently shippable with immediate effect and does not require Story 13.2.
- A separate epic (moving the dashboard and bot_tui off the VPS) addresses the same underlying host resource-oversubscription incident but is otherwise independent — no story-level dependency in either direction.
