# Codebase Concerns

**Analysis Date:** 2026-06-26

## Tech Debt

**Price precision inconsistency across data pipeline:**
- Issue: dYdX's oracle (mark/index price) feed derives each tick's `Price.precision` from the decimal digits present after stripping trailing zeros. Consecutive ticks for the same instrument can carry different precision labels (e.g. one tick has precision=5, next has precision=6). `ParquetDataCatalog` encodes this label in Arrow metadata and correctly refuses to read/merge files whose labels disagree — this causes catalog read failures when mixed-precision files are combined.
- Files: `troll/dydx_collector/client.py` (line 45-66: `_at_fixed_precision()` workaround), `crates/adapters/dydx/src/common/parse.rs` (source of precision derivation)
- Impact: Catalog becomes unreadable when mixing price updates from different time periods. Backtest and analytics pipelines fail silently on schema conflicts.
- Fix approach: All mark/index prices re-stamped at `nautilus_pyo3.FIXED_PRECISION` via `Decimal.scaleb()` + `Price.from_raw()` before storage. This is the current mitigation in `client.py`, but the root cause (variable precision in the Rust adapter) should be fixed upstream.

**Price constructor precision bug (nautilus_trader version):**
- Issue: `Price(decimal, precision)` constructor has a real bug for some decimal/precision combinations (e.g., `Price(Decimal("61090.59855"), 16)` silently returns `61090.5985500000026624` via internal float64 round-trip). Never round-trip market data through `float` before it's inside a `Price`/`Quantity`.
- Files: `troll/dydx_collector/client.py` (line 45-66: uses `Price.from_raw()` workaround instead)
- Impact: Silent data corruption when re-stamping prices at different precisions if constructor is used instead of `from_raw()`.
- Fix approach: Always use `Decimal.scaleb()` + `Price.from_raw()` (exact integer arithmetic) for precision re-stamping. Never use `Price(decimal, precision)` constructor directly for market data.

**Silent data loss in flush pipeline:**
- Issue: `troll/dydx_collector/collector.py`'s `_flush_once()` (line 73-81) catches all exceptions and silently drops data without persisting backlog or retry queue.
- Files: `troll/dydx_collector/collector.py` (line 78-81)
- Impact: If `ParquetDataCatalog.write_data()` fails (permission, disk full, schema mismatch), buffered market data (trades, order book deltas, bars) is discarded and lost forever. No alert or recovery mechanism.
- Fix approach: Implement a dead-letter queue for failed writes (local SQLite or disk file backup), emit metrics on flush failures, provide manual recovery script to replay backlog.

**Arrow deserializer gaps in catalog:**
- Issue: `IndexPriceUpdate` has no Arrow deserializer in this nautilus_trader version. `catalog_stats.py`'s `coverage()` function (line 145-153) silently skips data types that can't be read, masking the problem.
- Files: `troll/ml_signals/catalog_stats.py` (line 145-153: catches NotImplementedError and continues)
- Impact: Analytics and backtest dashboards report incorrect data coverage (missing `IndexPriceUpdate` metrics), and recovery attempts fail silently. Live strategy development doesn't catch schema issues until backtest time.
- Fix approach: Implement `IndexPriceUpdate.from_arrow()` in nautilus_trader or fork/patch Arrow serialization layer. Log warnings when data types are skipped, not silently.

## Performance Bottlenecks

**Dashboard metric recomputation at scale:**
- Problem: `troll/ml_signals/catalog_stats.py`'s `overview_table()` (line 222-231) recomputes all price stats from scratch on every call (~300+ catalog lookups, full price_series scans per instrument). Dashboard calls this on every rankings page load.
- Files: `troll/ml_signals/catalog_stats.py` (line 222-231), `troll/ml_signals/dashboard.py` (line 136-189: calls overview_table indirectly via metrics_store fallback)
- Current mitigation: Marked as "ponytail: recomputed from scratch on every call" (line 226); acknowledged but accepted for personal dashboards.
- Impact: As catalog grows (100+ instruments, months of data), rankings page load time and CPU usage increase linearly. Not cached; every page refresh rescans Parquet files.
- Improvement path: Add TTL in-memory cache or materialized view layer (e.g., Redis, DuckDB, or incremental update via timestamp filtering). Consider pushing computation to off-peak hours.

**Full-catalog metric computations on short intervals:**
- Problem: `troll/ml_signals/dashboard.py`'s `_fast_loop()` (line 536-559) computes OFI/microprice for all instruments every `LIVE_INTERVAL_SECONDS` (default 5 seconds). `_slow_loop()` (line 562-578) computes full snapshots every `DB_WRITE_INTERVAL_SECONDS` (default 60 seconds). Both use ThreadPoolExecutor with `max_workers=32`.
- Files: `troll/ml_signals/dashboard.py` (line 79, 536-559, 562-578), `troll/ml_signals/metrics_computer.py` (line 98, 124)
- Current design: Fast loop reads only 1 minute of order book deltas per coin (fast enough for ~300 coins in seconds). Slow loop reads 25h of trade history (slower).
- Impact: CPU sustained high on collector box; scales poorly above ~500 instruments. No configurable rate limiting or backoff.
- Improvement path: Make intervals configurable and adaptive based on instrument count. Implement incremental metric updates (only recent deltas) instead of full scans. Consider moving slow computations off-peak or to a separate compute cluster.

**In-memory chart series capped at fixed window:**
- Problem: `troll/ml_signals/dashboard.py` uses rolling deques with `maxlen=2_000` for OFI/microprice chart points (line 489-495). As catalog accumulates days of data, older history is never plotted on the per-coin indicator page.
- Files: `troll/ml_signals/dashboard.py` (line 489-495, 484-495: comments note this is a ponytail tradeoff)
- Impact: Historical trend charts on `/coin/{id}` page show only the most recent ~2000 delta events (~1-2 minutes), not the full recorded history. Long-term strategy backtests can't access full time series for offline analysis.
- Improvement path: Implement real downsampling/decimation (e.g., via DataFusion or Pandas rolling aggregations). Cache computed series to disk (Parquet, SQLite). Use query APIs instead of loading full history.

**ThreadPoolExecutor with fixed high concurrency:**
- Problem: `troll/ml_signals/metrics_computer.py` spawns ThreadPoolExecutor with `max_workers=32` (line 98, 124) unconditionally, regardless of CPU cores or system load.
- Files: `troll/ml_signals/metrics_computer.py` (line 98, 124)
- Impact: On systems with fewer than 32 cores (e.g., single cloud instance), thread overhead dominates. On shared systems, 32 concurrent Parquet reads contend with other processes.
- Fix approach: Use `max_workers=min(os.cpu_count(), 8)` or make configurable via environment variable.

## Fragile Areas

**Background daemon loop exception handling:**
- Issue: Dashboard's `_fast_loop()` and `_slow_loop()` (line 536-559, 562-578) catch all exceptions and log, then continue sleeping. If metrics computation fails, the background thread silently stops updating `_LIVE` dict, but the daemon appears healthy (no exception propagates to main thread).
- Files: `troll/ml_signals/dashboard.py` (line 536-559, 562-578: bare `except Exception` with logger.exception call)
- Why fragile: Live rankings page continues serving stale data without alerting the operator. Strategy running against this dashboard gets incorrect signal snapshots indefinitely.
- Safe modification: Add a "last_update" timestamp to _LIVE entries; have dashboard page check staleness and emit a warning if >2x the expected update interval has elapsed.

**Global state in dashboard with incomplete locking:**
- Issue: `troll/ml_signals/dashboard.py` uses `_METRICS_LOCK` to protect `_LIVE` dict (lines 101-102, 137-138, etc.) but `_SERIES` dict (line 97) is accessed WITHOUT a lock in `_render_live_page()` (line 517-518).
- Files: `troll/ml_signals/dashboard.py` (line 97-102, 517-518)
- Why fragile: Concurrent modification of `_SERIES` deque from `record()` callback while `_render_live_page()` iterates can cause deque iteration errors or lost updates.
- Safe modification: Acquire `_LOCK` before reading `_SERIES` in `_render_live_page()` (already done correctly on line 517-518, but double-check other accesses).

**SQLite concurrent access without synchronization:**
- Issue: `troll/ml_signals/metrics_store.py` opens SQLite connections with `check_same_thread=False` (line 45-48) and uses a single module-level lock `_lock` (line 26). Multiple threads writing simultaneously via `write()` calls could interleave transactions if clock jitter causes two threads to call `write()` at nearly the same time.
- Files: `troll/ml_signals/metrics_store.py` (line 26, 43-49, 52-64)
- Current mitigation: WAL mode enabled (line 46), so readers don't block writers. But no explicit transaction scoping — each INSERT/DELETE is a separate auto-commit by default.
- Why fragile: If `_fast_loop()` calls metrics_store.write() while `_slow_loop()` is mid-write, the pruning DELETE (line 59) could race with INSERT (line 60-63), causing data loss or duplicates.
- Safe modification: Wrap all DB operations in explicit `BEGIN IMMEDIATE` transaction, or use a proper connection pool with serialization.

**Order flow imbalance indicator state never resets mid-run:**
- Issue: `troll/ml_signals/ofi_strategy.py`'s `_ofi` indicator (line 124) is only reset in `on_reset()` (line 335-345), which is called between backtest runs, not between strategy live sessions.
- Files: `troll/ml_signals/ofi_strategy.py` (line 124, 335-345)
- Why fragile: If strategy instance is reused across multiple live trading days without explicit `on_reset()`, the OFI rolling window carries stale state from the previous day, corrupting signals.
- Safe modification: Add a check in `on_start()` to detect if `_ofi` state is non-zero and reset if so. Or make on_reset() mandatory (raise error if called twice without reset in between).

**Online logistic trend model lacks retraining/persistence:**
- Issue: `troll/ml_signals/indicators.py`'s `OnlineLogisticTrend` (line 27-100) trains one SGD step per bar but never resets weights, persists to disk, or validates against overfit. Over long deployments (months), the model weights drift without recalibration.
- Files: `troll/ml_signals/indicators.py` (line 27-100, 88-91: single _sgd_step call per bar)
- Current design: Acknowledged in comment (line 67-69) as "ponytail: single online SGD step per bar, no batch retraining or persistence". Suggests swapping for `sklearn.linear_model.SGDClassifier` if drift occurs.
- Why fragile: Strategy can silently decay over weeks as model weights drift. No telemetry to detect model degradation.
- Improvement path: Implement periodic model snapshot/save to disk. Add validation metric (e.g., rolling holdout test set). Emit alert if accuracy drops below threshold. Consider warm-starting from disk on strategy restart.

## Known Bugs

**Open interest unavailable via WebSocket:**
- Symptoms: Open interest field is parsed Rust-side but never forwarded to Python in both REST and WebSocket market-data paths.
- Files: `crates/adapters/dydx/src/python/http.rs`, `crates/adapters/dydx/src/python/websocket.rs` (Rust bindings), `troll/dydx_collector/open_interest.py` (workaround)
- Trigger: Any attempt to fetch open interest from dYdX WS feed will get empty/zero values.
- Workaround: `troll/dydx_collector/open_interest.py` (line 120-123) polls dYdX's public REST indexer every 5 minutes (configurable). Creates separate `DydxOpenInterest` data type registered for Parquet serialization.
- Root cause: PyO3 bindings don't expose the parsed field; custom type + REST fetch is the only option.

**Config reload can miss hot-updated bar_intervals:**
- Symptoms: If you add a new bar interval to an existing instrument in config.toml, the reload loop (line 111-132) doesn't diff bar intervals — only instrument IDs.
- Files: `troll/dydx_collector/collector.py` (line 111-132), `troll/dydx_collector/config.py` (line 61-72: diff_instruments only compares ID)
- Trigger: Edit config.toml to change `bar_intervals` for an instrument that already exists, then wait for reload.
- Workaround: Manually restart collector, or subscribe directly via collector API before next reload cycle.
- Fix approach: Extend diff_instruments() to also compare bar_intervals; emit subscribe/unsubscribe for changed intervals.

## Security Considerations

**urllib usage without User-Agent validation:**
- Risk: `troll/dydx_collector/open_interest.py` (line 112-114) hardcodes `User-Agent: nautilus-dydx-collector/1.0` and uses `urllib.request.urlopen` without retries or exponential backoff. Public endpoint could rate-limit or block based on user agent.
- Files: `troll/dydx_collector/open_interest.py` (line 112-117)
- Current mitigation: No explicit mitigation; assumes dYdX won't block this agent string.
- Recommendations: Rotate user agent, add request retries with exponential backoff, consider rate-limiting upstream (deduplicate via cache).

**SQLite database not encrypted:**
- Risk: `troll/ml_signals/metrics_store.py` writes live metrics (price, OFI, volatility) to SQLite (line 52-64) without encryption. If database file is copied/stolen, all market data is readable.
- Files: `troll/ml_signals/metrics_store.py` (line 52-64)
- Impact: Low for internal personal use; high if collector runs on shared hosting.
- Recommendations: Enable SQLCipher (sqlite3-pysqlcipher3) or use AES-encrypted blob storage. Or store metrics in Redis with ACL instead.

## Scaling Limits

**Auto-subscribe mode hits rate limit during startup:**
- Problem: In "all-instruments mode" (empty config.instruments list), `collector.py` (line 139-147) auto-subscribes to every dYdX perpetual (~300+ at peak). dYdX enforces 2/sec WebSocket subscribe rate limit. Subscription startup takes ~150+ seconds with no backoff.
- Files: `troll/dydx_collector/collector.py` (line 139-147, 165-167)
- Current mitigation: Bar subscriptions are explicitly skipped in auto-mode (line 143) to avoid exceeding rate limit during trades/orderbook subscription burst.
- Impact: If collector restarts and mode is auto-subscribe, it's unavailable for ~2.5 minutes during subscription catchup.
- Scaling path: Implement adaptive subscription rate (slow ramp-up), store subscription state to disk, resume from checkpoint on restart. Or accept staggered subscriptions (shard across multiple collector instances).

**Flush interval cannot handle high message rates:**
- Problem: `collector.py` buffers all market data in-memory (defaultdict(list)) with a single flush timer (default 60 seconds). On days with high volatility (e.g., ETH spike), 300+ instruments * (trades + book deltas + bars) = 100k+ messages/sec * 60s = millions of items in buffer before flush.
- Files: `troll/dydx_collector/collector.py` (line 66, 83-86)
- Impact: Memory usage can spike to gigabytes during high-volume periods. If flush fails, entire buffer is lost.
- Scaling path: Implement incremental micro-flushes (e.g., flush every 100k items or 30 seconds, whichever comes first). Or stream directly to Parquet without buffering.

**Catalog search/query scales linearly with file count:**
- Problem: `troll/ml_signals/catalog_stats.py`'s `list_instruments()` (line 42-47) and `find_gaps()` (line 68-94) use glob and full array scans. On a multi-terabyte catalog with 100k+ Parquet files, these operations become slow (minutes to scan).
- Files: `troll/ml_signals/catalog_stats.py` (line 42-47, 68-94)
- Impact: Analytics dashboards hang when opening new instruments pages. Backtest parameter sweeps timeout.
- Scaling path: Add metadata index (SQLite or Parquet footer metadata), use parquet-cli stats, or partition catalog differently (e.g., by date ranges instead of flat directory).

## Missing Critical Features

**No data backfilling capability:**
- Problem: Collector only captures new market data forward in time. If collector crashes/restarts and dYdX historical REST API has gaps, missing history is lost forever.
- Files: `troll/dydx_collector/collector.py` (realtime-only), no backfill module
- Impact: Catalog has date ranges with zero data (gaps). Backtests produce spurious results if run during gap periods.
- Blocks: Accurate historical strategy testing during multi-day gaps. Live signal forensics (can't replay exact conditions).

**No catalog integrity verification:**
- Problem: `ParquetDataCatalog.write_data()` has no checksum validation or schema consistency checks at write time. Corrupted or malformed Parquet files silently accumulate.
- Files: `troll/dydx_collector/collector.py` (line 79), `troll/ml_signals/catalog_stats.py` (line 145-153: catches RuntimeError from Arrow reader but doesn't repair)
- Impact: Discover data corruption late (during backtest or recovery), with no way to isolate exact files or time ranges affected.
- Fix approach: Add CRC32/SHA256 checksums to Parquet file metadata. Implement repair tool to validate/rebuild corrupted partitions.

## Test Coverage Gaps

**Collector error paths not tested:**
- What's not tested: `_flush_once()` exception handling (line 73-81), `_open_interest_loop()` fetch failures (line 88-95), reconnect logic under dYdX WebSocket outages, buffer overflow under sustained high-load scenarios.
- Files: `troll/dydx_collector/test_client.py` (exists but limited scope), no test_collector.py
- Risk: Silent data loss during real outages goes undetected until manual inspection of catalog gaps.
- Priority: High — data integrity is the product.

**Dashboard/indicator unit tests minimal:**
- What's not tested: Concurrent access to `_LIVE`/`_SERIES` dicts under load, metric computation failures, dashboard page rendering with empty/partial catalog.
- Files: `troll/ml_signals/test_*.py` files exist for book_features, metrics_store, ofi_strategy, but not dashboard.py, metrics_computer.py
- Risk: Race conditions, stale data in live view, uncaught exceptions in background threads.
- Priority: Medium — UI layer is less critical than data pipeline, but affects trading confidence.

**OFI strategy signal validation missing:**
- What's not tested: Verify OFI moving average thresholds actually trigger entries/exits as designed. Test all filter branches (`_all_filters_pass()` combinations). Validate cumulative delta window pruning.
- Files: `troll/ml_signals/test_ofi_strategy.py` (exists but limited) and `troll/ml_signals/backtest_ofi.py` (backtest, not unit test)
- Risk: Silent signal failures in live trading (e.g., filter rejects all valid trades, or invalid ones pass).
- Priority: High — strategy correctness is the product.

---

*Concerns audit: 2026-06-26*
