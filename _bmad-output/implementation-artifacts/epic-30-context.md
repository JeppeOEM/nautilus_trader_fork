# Epic 30 Context: Catalog storage footprint

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Shrink the Parquet catalog's disk footprint with lossless changes, and remove float noise from stored snapshot values, while keeping every file loadable by `ParquetDataCatalog` and `BacktestNode` with zero conversion. Measurements on one consolidated Bybit BTC day show zstd level 19 plus delta-encoded timestamps cuts bytes per row by 29–56 % depending on type. An integer book/trade layout for the 1-second snapshot takes a further ~22 % off and makes every stored price and size exact. About 20.7 % of stored book prices are float-noisy today (e.g. `85891.90000000001`). Nothing is frozen before prod launch, so schema changes are allowed. Each one is judged on its merits and its migration cost.

## Stories

- Story 30.1: Compact Parquet encoding for every consolidated and rewritten catalog file
- Story 30.2: The second snapshot stores its book and trade fields as exact integers

(Other stories may be added later as the operator decides them: fewer files for the small types, instrument definitions written only for collected coins and only on change, legacy-type cleanup, incident-report retention.)

## Requirements & Constraints

- **Lossless and compatible:** a rewritten file must match its source in schema, schema metadata, row count, row order and every value (`Table.equals`). `ParquetDataCatalog.query` must return identical objects, `BacktestNode` must load it, and the kernel's column-projected readers must read it unchanged. The file must also be smaller than a default-settings write.
- **Rejected by measurement (record the numbers in a comment; do not reintroduce):**
  - `BYTE_STREAM_SPLIT` on the float book columns: +43 %.
  - float32 book columns: no gain, and lossy.
  - Dropping `ts_event`: on Bybit/Hyperliquid it is the exchange second and cannot be recovered from `ts_init`. The gap is 1.0–5.2 s, and several overdue seconds can close at one wake-up.
- **CPU budget for zstd 19:**
  - Measure the consolidate step's wall time and peak RSS before and after, and record them next to the size numbers.
  - If level 19 is more than 10× slower than the default level, drop to the smallest level whose size is within 2 % of level 19's, and record both numbers.
- **Price integrity:**
  - Never round-trip a market value through `float` before it is inside a `Price`/`Quantity`.
  - Integer units come from `Price.raw`/`Quantity.raw` scaled down from `FIXED_PRECISION` by integer division, asserted exact.
  - Precision comes from the instrument definition, never from a value's own digit count.
- **Migrations and recompress tools:**
  - Report-only unless `--apply`. Print before/after totals per type or venue.
  - Closed-day files only; open-day files are skipped and picked up by a later run.
  - Run under archive's maintenance lock, through `CatalogFiles.rewrite`, and verify each file before it is replaced.
  - A value that cannot be snapped exactly refuses that file with a ledger entry. Never round it silently.
- **Rules to follow:**
  - **DATA-02:** ingestion correctness needs hard, independently verified evidence. Fast detect-and-recover is not a fix.
  - **DATA-05:** no silent data loss. The `archive` compose service is the only scheduler of nightly and intraday maintenance. Every in-place rewrite is `CatalogFiles.rewrite` (verified temp-then-rename). No row of the current UTC day is ever changed. Story 30.1 makes DATA-05 name the one write-options function.
  - **MEM-01:** never load an unbounded catalog slice. Keep reads time-bounded or streamed, which matters for the whole-catalog recompress and migrate tools.
  - **TEST-04:** a new warning is treated as a failing test. Fix it at its source, or record it with file, line and reason. Never blanket-suppress.
  - **DESIGN-01:** a new abstraction is allowed only if its docstring names the invariant it protects. Otherwise use plain functions or dataclasses. Add no new dependency.
  - **FORK-01:** never touch `nautilus_trader/` or `crates/`. The live minute files come from Nautilus's own `write_data`, so their encoding is not ours to choose. Compact settings apply only when archive merges or rewrites them.
  - **MR4:** in the same commit, update the `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, and, if files move, the dockerfile `COPY` sets, compose `command:` lines and Makefile test lists.
  - **OPS-01:** a story never parks `awaiting-operator`. Any VPS run (recompress, migration, coordinated redeploy) goes into `docs/DEPLOY_CHECKLIST.md` under "Deferred operator actions" as one entry headed with the story key and commit, as unchecked items. The story then finalizes `done` through normal review. Totals from those runs are recorded in `docs/DATA_INTEGRITY_AUDIT.md`.

## Technical Decisions

- **One rewriter:** `CatalogFiles` (`archive/infrastructure/catalog_files.py`: `rewrite`, `write_merged`) is the only in-place catalog rewriter. It is temp-then-rename and preserves metadata. Consolidation, the nightly snapshot rebuild, migrations and recompress all go through it. `RetentionPolicy` is the only deleter.
- **One write-options function (30.1):**
  - It lives in `archive/infrastructure/` and is the only place `pq.write_table` options are chosen.
  - zstd level 19, as a named constant that cites the measurement.
  - `DELTA_BINARY_PACKED` with dictionary encoding off for `ts_event`, `ts_init` and every other monotonic timestamp column (e.g. `next_funding_ns`). Dictionary encoding stays on for the other columns.
  - One row group per instrument-day, capped by `_MAX_ROW_GROUP_ROWS` (1,048,576).
  - Column statistics are kept so `ts_event`/`ts_init` pushdown still prunes.
  - `kernel/parquet_compat.py`'s `apply_zstd_default()` stays as the patch for live writes, and its docstring points at the new function.
- **Snapshot integer layout (30.2):**
  - Per-row fields `price_precision: uint8` and `size_precision: uint8`.
  - `bid_prices`/`ask_prices` are `list<int64>`. Element 0 is the best price in units of `10^-precision`; each later element is the positive gap from the previous level.
  - Sizes are `list<int64>` units.
  - OHLC is nullable `int64` price units. Buy/sell volume is `int64` size units.
  - Counts and timestamps are unchanged.
- **One encoder/decoder (30.2):**
  - It lives in `kernel/second_snapshot.py` and is used for both Parquet and the `snapshots:raw` Redis payload.
  - Decoded objects expose exact `Price`/`Quantity` values (via `from_raw`) and floats, both computed once.
  - Every reader decodes through it: ranking, views, alerting, `data_api`, candles, `kernel/catalog_files.py` projected reads, archive rebuild/reconcile, research `MarketFrames` and bot_tui. Grep tests enforce this.
  - Values stay integers wherever a machine moves or stores them. Only display converts them to decimals, in one shared exact, string-based TypeScript helper in `platform/frontend/`.
  - `kernel.fold.fold_trades` produces OHLC and volume units from exact sums. The archive rebuild compares integers, with no float tolerance anywhere.
- **Tests:**
  - Round-trip fixtures cover every catalog data type: snapshot, trade tick, mark/index, funding, open interest, instrument status, instrument definitions, order book deltas.
  - A property test covers 1–50 levels, precisions 0–9 and sizes up to `QUANTITY_RAW_MAX`.
- **Where test files live:** archive tests in `archive/tests/`, kernel tests in `kernel/tests/`.

## Cross-Story Dependencies

- The epic depends on Story 25.1 (`CatalogFiles` as the one rewriter) and Story 25.1b (the `archive` service runs nightly and intraday consolidation). Both are done.
- 30.1 comes first. 30.2 writes and migrates with 30.1's write settings, and its size criterion is measured against 30.1's float layout.
- 30.2 changes the `snapshots:raw` payload, which breaks any consumer that has not been updated. All consumers change in that story. The VPS cutover (collectors, ranking, data_api/frontend, alerting and bots redeployed together, old stream entries drained or trimmed) goes into `DEPLOY_CHECKLIST` as a deferred operator action.
