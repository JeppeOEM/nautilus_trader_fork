# Codebase Concerns

**Analysis Date:** 2026-06-13

## Tech Debt

### Database Schema Incomplete

**Issue:** PostgreSQL cache database schema lacks proper foreign key constraints and is incomplete for full event persistence layer.

**Files:**
- `crates/infrastructure/tests/test_cache_postgres.rs` (lines 340-346, 396-402)
- `crates/infrastructure/src/sql/models/instruments.rs` (line 178)

**Impact:** 
- Multiple integration tests are ignored and cannot run (`test_order_cancel_rejected_insert_and_load`, `test_order_modify_rejected_insert_and_load`)
- Betting instrument schema not supported
- Referential integrity cannot be enforced between tables
- Data consistency guarantees at the database level are missing

**Fix approach:**
- Add FK constraints from `order_events` → `orders` table
- Add FK constraints for `instruments` table for `instrument_id`
- Add FK constraints to `accounts` table for `account_id`
- Complete schema for betting instruments
- Re-enable and expand integration tests for full coverage

### Bulk Operations Performance Gap

**Issue:** Data engine processes instrument updates one at a time instead of using bulk update methods.

**Files:** `crates/data/src/engine/mod.rs` (line 3812)

**Impact:**
- Handling large batches of instruments (line 3814-3818) iterates and acquires locks individually
- Performance degrades with large numbers of instruments
- Cache and database writes are inefficient

**Fix approach:**
- Implement `bulk_add_instruments()` method on Cache trait
- Implement corresponding database bulk update operation
- Update `handle_instruments()` to use bulk methods

### Incomplete Message Bus Database Integration

**Issue:** Message bus database layer is declared but not actually integrated with the backing database.

**Files:** `crates/common/src/msgbus/core.rs` (line 578)

**Impact:**
- Message persistence to database is not fully wired
- Message bus state may not be recoverable after restarts

**Fix approach:**
- Implement database integration methods in MessageBus
- Add tests for persistence and recovery

### Cache Database Greeks and Yield Curve Support

**Issue:** Database adapter lacks methods to store Greeks and yield curve data.

**Files:** `crates/common/src/cache/mod.rs` (lines 1978, 2013)

**Impact:**
- Greeks and yield curve updates cannot be persisted to database
- Data is lost on application restart
- Analytics features that depend on historical Greeks/curves are limited

**Fix approach:**
- Implement `database.add_greeks()` method
- Implement `database.add_yield_curve()` method
- Update cache to call database methods when data arrives

## Known Bugs & Regressions

### Data Engine Bulk Insert Serialization Type Mismatch

**Issue:** Serialized Arrow format may have type mismatches between Rust and Python types when deserializing deltas and trades.

**Files:** `tests/unit_tests/serialization/test_arrow.py` (lines 150, 195, 310)

**Impact:**
- Deserialization tests are commented out
- Legacy wrangler incompatibilities exist
- Cannot reliably round-trip complex data types

**Fix approach:**
- Align Rust and Python Arrow schema definitions
- Update wrangler to handle both formats during migration
- Complete round-trip test coverage for all data types

### Option Contract Tick Scheme Under Development

**Issue:** Multiple instrument types have incomplete tick scheme implementations marked as "Under development".

**Files:**
- `tests/unit_tests/model/instruments/test_equity_pyo3.py` (line 80)
- `tests/unit_tests/model/instruments/test_option_spread_pyo3.py` (line 79)
- `tests/unit_tests/model/instruments/test_option_contract_pyo3.py` (line 86)
- `tests/unit_tests/model/instruments/test_crypto_perpetual_pyo3.py` (line 169)
- `tests/unit_tests/model/instruments/test_crypto_future_pyo3.py` (line 183)
- `tests/unit_tests/model/instruments/test_currency_pair_pyo3.py` (line 70)
- `tests/unit_tests/model/instruments/test_futures_contract_pyo3.py` (line 78)
- `tests/unit_tests/model/instruments/test_crypto_option_pyo3.py` (line 187)
- `tests/unit_tests/model/instruments/test_futures_spread_pyo3.py` (line 79)
- `tests/unit_tests/model/instruments/test_perpetual_contract_pyo3.py` (line 164)

**Impact:**
- Test assertions skip `tick_scheme_name` field validation
- Tick scheme serialization may fail for these instruments
- Quote/trade data processing may not handle ticks correctly

**Fix approach:**
- Complete tick scheme implementation for all instrument types
- Remove "Under development" skip from tests
- Ensure all instrument types handle custom tick schemes

### Betfair Symbology Issues

**Issue:** Betfair adapter has incorrect asset class assignment and symbology problems.

**Files:**
- `tests/integration_tests/adapters/databento/test_loaders.py` (line 151) - instrument marked as COMMODITY instead of EQUITY
- `tests/integration_tests/adapters/betfair/test_betfair_providers.py` (line 65) - symbology marked for fixing
- `tests/integration_tests/adapters/betfair/test_betfair_data.py` (line 103) - Betfair symbology needs fixing

**Impact:**
- Betfair instruments misclassified
- Symbol resolution fails in certain contexts
- Integration tests cannot pass

**Fix approach:**
- Correct asset class mapping in Databento loader
- Implement Betfair symbology normalization
- Update tests with corrected expectations

### InstrumentStatus Repr Needs Improvement

**Issue:** Rust-derived InstrumentStatus repr output is incomplete.

**Files:** `tests/unit_tests/model/test_status_pyo3.py` (line 37)

**Impact:**
- String representation of status objects is incomplete
- Debugging and logging are harder

**Fix approach:**
- Update Rust repr implementation for InstrumentStatus
- Verify output matches expected format

### Data Client Response Assertion Testing

**Issue:** Test assertions rely on log parsing for validating data client responses instead of direct assertions.

**Files:** `tests/unit_tests/data/test_client.py` (lines 99, 118, 142)

**Impact:**
- Tests are fragile and depend on logging implementation details
- Harder to maintain and understand test failures

**Fix approach:**
- Implement mock data client with assertion hooks
- Capture responses directly instead of parsing logs
- Reduce coupling to logging infrastructure

## Performance Bottlenecks

### Serialization SBE Writer Uninitialized Memory

**Issue:** SBE writer uses narrow `unsafe` blocks to avoid zero-initializing backing buffer, trading memory overhead for speed.

**Files:** `crates/serialization/src/sbe/writer.rs` (lines 18-26, 57, 165, 181, 263)

**Impact:**
- Unsafe code in performance-critical path
- Potential for memory safety bugs if invariants are violated
- Narrow window for bugs but high severity

**Fix approach:**
- Keep existing optimization but add comprehensive safety comments
- Expand test coverage for boundary conditions
- Consider using `MaybeUninit` helpers to reduce unsafe scope
- Document the SAFETY invariants clearly

### Data Engine Order Book Snapshotter Polling

**Issue:** Order book snapshotters may trigger before order book is updated, leaving snapshots stale.

**Files:** `crates/data/src/engine/book.rs` (line 272)

**Impact:**
- Snapshot timing may be off by one event cycle
- Gaps in order book history possible
- Performance impact from extra polling cycles

**Fix approach:**
- Implement event-driven snapshot trigger instead of timer-based
- Or ensure snapshotter waits for explicit updates

### Large Test Files with Complex Logic

**Issue:** Several test files exceed 10,000 lines with tightly coupled assertions.

**Files:**
- `crates/data/tests/engine.rs` (21,235 lines)
- `crates/execution/tests/exec_engine.rs` (14,089 lines)
- `crates/execution/tests/matching_engine.rs` (12,938 lines)

**Impact:**
- Tests are slow and take long to compile
- Difficult to isolate specific test failures
- Maintenance burden is high

**Fix approach:**
- Split large test files by functional area
- Extract shared test fixtures and builders
- Use parameterized tests for similar assertions

## Fragile Areas

### Serialization Code Generation

**Files:**
- `crates/serialization/generated/capnp/commands/data_capnp.rs` (15,602 lines - generated)
- `crates/serialization/generated/capnp/events/order_capnp.rs` (11,911 lines - generated)
- `crates/serialization/generated/capnp/commands/trading_capnp.rs` (10,414 lines - generated)

**Why fragile:**
- Auto-generated code that breaks if schema changes
- Manual edits are overwritten on regeneration
- Hard to understand and debug

**Safe modification:**
- Only modify `.capnp` schema files, never generated code
- Regenerate using official tool, verify no manual changes are lost
- Add schema changes to version control before regeneration

**Test coverage:**
- Round-trip tests for all schema types exist
- Type conversion tests present
- Coverage for Python/Rust interop exists

### Order Book and Matching Engine

**Files:**
- `crates/execution/src/matching_engine/engine.rs` (6,414 lines)
- `crates/model/src/orderbook/tests.rs` (8,616 lines)

**Why fragile:**
- Complex state machine managing order fills
- Edge cases in market order matching against various depths
- Interactions between multiple order types (limit, market, stop)
- Fill timing and precision sensitive

**Safe modification:**
- Add regression tests before any changes
- Test edge cases: empty book, single level, multiple fills at same price
- Verify that subsequent fills are not dropped (regression #4063)
- Run full matching engine test suite

**Test coverage:**
- Extensive tests for order fills exist
- Regression tests for specific issues (#3790, #4063)
- Coverage for edge cases like stale events

### Order Reconciliation Logic

**Files:** `crates/execution/src/reconciliation/tests.rs` (4,956 lines)

**Why fragile:**
- Matches local order state with venue order state
- Multiple order states and transitions possible
- Network delays and partial failures must be handled

**Safe modification:**
- Test before/after state transitions carefully
- Add regression tests for specific venues
- Verify that all reconciliation paths have exit conditions

### Cache and Portfolio Position Tracking

**Files:**
- `crates/common/src/cache/mod.rs` (5,594 lines)
- `crates/portfolio/tests/portfolio.rs` (5,312 lines)

**Why fragile:**
- Multiple systems feed updates to cache concurrently
- Position tracking depends on correct order of events
- Currency conversions and account consolidations complex

**Safe modification:**
- Isolate cache operations in separate transactions
- Add balance update validation tests
- Verify position calculations match portfolio

### Blockchain Adapter Block Syncing

**Files:** `crates/adapters/blockchain/src/cache/mod.rs` (lines 187, 287)

**Why fragile:**
- Block syncing disabled (TODO)
- Block number/transaction index handling incomplete
- Timestamps for DeFi-only data not configured

**Safe modification:**
- Enable and test block syncing only when timestamps are ready
- Handle missing timestamps gracefully
- Add regression tests for block ordering

## Scaling Limits

### Single-Threaded Event Loop

**Architecture:** Nautilus uses a single-threaded event-driven architecture (Tokio runtime).

**Current capacity:**
- ~1,000 instruments per data engine instance
- ~10,000 TPS throughput typical
- Order book snapshots every 100-1000ms

**Limit:** CPU-bound at 100% utilization with ~50 concurrent connections

**Scaling path:**
- Horizontal: Run multiple engine instances with shared cache (Redis)
- Vertical: Enable multi-threading for CPU-intensive adapters (not yet supported)
- Optimize: Reduce allocations in hot paths (data encoding/decoding)

### Cache Memory Usage

**Current behavior:** All instruments, orders, positions, bars kept in memory

**Scaling limit:** ~50GB with 10,000+ instruments and years of historical data

**Scaling path:**
- Implement tiered caching (hot/cold data)
- Move older bars/quotes to persistent storage
- Implement cache eviction policies

### PostgreSQL Database Connections

**Current:** Single connection pool for all operations

**Scaling limit:** Pool size limits concurrent queries; batch operations lock entire cache

**Scaling path:**
- Implement connection pooling per subsystem
- Add read replicas for queries
- Implement write batching to reduce lock contention

## Security Considerations

### Unsafe Code in SBE Serialization

**Risk:** Memory corruption if `unsafe` invariants are violated in performance-critical serialization path.

**Files:** `crates/serialization/src/sbe/writer.rs`

**Current mitigation:**
- Clippy rule enforces safety comments on all unsafe blocks
- Narrow scope: only 3 unsafe blocks in entire module
- Load-bearing safety checks with panic conditions
- Comprehensive test coverage

**Recommendations:**
- Continue strict audit of unsafe code additions
- Consider `audit` tool for vulnerability scanning
- Document invariants explicitly in SAFETY comments

### API Credential Handling

**Risk:** API credentials might be leaked in logs, error messages, or crash dumps.

**Files:**
- `crates/network/src/websocket/client.rs` (line 2084) - legacy callback for Python
- Various adapter HTTP clients handle auth headers

**Current mitigation:**
- `.env` files not committed to git
- Error handling sanitizes credentials in messages

**Recommendations:**
- Audit all error paths for credential leaks
- Add regex sanitization to logs for sensitive patterns
- Review Python bridge for credential passing safety

## Dependencies at Risk

### NumPy Deprecated API Warnings

**Risk:** NumPy API version mismatch between Cython and build toolchain.

**Files:** `build.py` (lines 260-264)

**Current mitigation:**
- Compiler flag `NPY_NO_DEPRECATED_API` set to `NPY_1_7_API_VERSION`
- Cython v3.0.11+ required for coverage support

**Recommendations:**
- Plan migration path when NumPy 3.0 releases
- Monitor NumPy deprecation notices in release notes

### Serialization Format Stability

**Risk:** Arrow and Cap'n Proto schema formats are not stable and may break between releases.

**Files:**
- `crates/serialization/src/lib.rs` (line 53)
- `crates/serialization/src/capnp/mod.rs` (line 21)

**Current mitigation:**
- Version schemas in Cargo.toml version bump
- Backward compatibility tests exist

**Recommendations:**
- Stabilize schema versions before 1.0 release
- Add schema versioning to wire format
- Implement schema migration tools

## Missing Critical Features

### WebSocket SOCKS Proxy Support

**Issue:** SOCKS proxy scheme recognized but not implemented.

**Files:**
- `crates/network/src/websocket/proxy.rs` (lines 23-27)
- `crates/network/src/websocket/config.rs` (lines 144, 153)
- `crates/network/src/websocket/client.rs` (lines 378, 397, 477)

**Problem:** Cannot route WebSocket connections through SOCKS proxies (only HTTP proxies supported).

**Blocks:** Enterprise deployments requiring SOCKS proxy routing.

**Migration path:**
- Add `tokio-socks` as workspace dependency
- Implement SOCKS proxy handling in WebSocket client
- Test against common SOCKS proxy implementations

### Book Delta and Trade History Requests

**Issue:** Historical data request methods not yet implemented.

**Files:**
- `crates/testkit/src/testers/data/config.rs` (lines 109-132)
- `crates/testkit/src/testers/data/actor.rs` (lines 118, 191, 294)

**Problem:** Cannot request historical order book deltas or trades via data engine.

**Blocks:** Backtesting with realistic market microstructure data.

**Implementation path:**
- Implement `RequestBookDeltas` message type
- Implement `RequestTrades` message type
- Add subscribe/unsubscribe for book depth when available
- Support book grouping size configuration

### Live Kernel Runtime Features

**Issue:** Several features are not implemented on live runtime.

**Files:** `crates/live/src/config.rs` (lines 136, 209, 313-320)

**Problem:**
- Portfolio state tracking not available
- Real-time account balance tracking incomplete
- Order modification without venue support missing

**Blocks:** Advanced risk management and portfolio features in live trading.

**Implementation path:**
- Integrate portfolio state tracking into live kernel
- Implement real-time balance monitoring
- Add order modification emulation for limited venues

### Betting Instrument Support

**Issue:** Betting instruments are not fully implemented.

**Files:**
- `tests/unit_tests/model/instruments/test_betting_pyo3.py` (line 77)
- `tests/unit_tests/model/instruments/test_binary_option_pyo3.py` (line 66)

**Problem:** Tests marked as "Not implemented".

**Blocks:** Betfair and prediction market support.

**Implementation path:**
- Complete betting instrument model
- Add parsers for betting exchanges
- Enable test assertions

## Test Coverage Gaps

### Data Engine Rust Implementation Tests

**Untested area:** Full integration between Rust data engine and Python strategy layer.

**Files:** `crates/data/tests/engine.rs` (21,235 lines)

**Risk:** Missing end-to-end tests for cross-language event flow.

**Priority:** High

**Fix approach:**
- Add Python FFI tests that verify event delivery to strategies
- Test reconnection scenarios with pending subscriptions
- Test backpressure handling with slow subscribers

### Option Chain Greeks Computation

**Untested area:** Greeks computation edge cases and precision.

**Files:** `nautilus_model/data/greeks.pyx` (if exists)

**Risk:** Greeks may be incorrect under extreme market conditions.

**Priority:** High (affects risk management)

**Fix approach:**
- Add tests for boundary conditions (deep ITM/OTM)
- Verify precision against financial libraries
- Test with extreme volatility scenarios

### Portfolio Multi-Currency Scenarios

**Untested area:** Multi-currency account handling and consolidation.

**Files:** `crates/analysis/src/analyzer.rs` (line 264)

**Risk:** Analytics may fail silently on multi-currency accounts.

**Priority:** Medium

**Fix approach:**
- Add tests for currency conversion in P&L calculations
- Test consolidation across multiple accounts
- Add regression tests for balance mismatch scenarios

### Redis Cache Database Synchronization

**Untested area:** Concurrent updates to Redis cache with multiple engines.

**Files:** `crates/infrastructure/src/redis/cache.rs`

**Risk:** Race conditions between multiple engine instances.

**Priority:** Medium (only affects distributed setups)

**Fix approach:**
- Add turmoil-style chaos tests for network partitions
- Test cache coherency across instances
- Verify transaction isolation

### Blockchain Adapter Integration

**Untested area:** Block syncing, timestamp handling, and transaction indexing.

**Files:** `crates/adapters/blockchain/src/` (multiple files with TODO)

**Risk:** Block data may be lost or misordered.

**Priority:** Medium (blockchain-specific)

**Fix approach:**
- Enable block syncing tests once timestamps are ready
- Add comprehensive transaction ordering tests
- Test DeFi-specific data fields (gas, nonces)

---

*Concerns audit: 2026-06-13*
