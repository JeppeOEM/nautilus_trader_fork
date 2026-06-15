# Phase 4: Open Interest Spike - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-15
**Phase:** 4-Open Interest Spike
**Areas discussed:** Extraction path & polling cadence, Fields captured per OI record, Persistence: every poll vs dedup-on-change, Custom Data type identity/shape

---

## Extraction path & polling cadence

| Option | Description | Selected |
|--------|-------------|----------|
| Every 60 seconds | Matches typical OI update granularity on most exchanges; low overhead, ~1 req/min regardless of instrument count (batch call) | |
| Every 5 minutes | Even lighter weight; suitable if OI is mainly for coarse trend/backtest context | |
| Configurable in recorder.toml (Claude picks a default) | Adds an `oi_poll_interval_seconds` config knob, future-proofs without locking in now | ✓ |

**User's choice:** Configurable in recorder.toml (you pick a default)
**Notes:** WS-ticker path ruled out — `BybitWsTickerLinear.open_interest` exists in the raw struct but has no Rust parser exposing it to Python, and adding one would require modifying `crates/adapters/bybit` (core, forbidden by CLAUDE.md). REST polling via `BybitHttpClient.request_tickers()` is the only viable no-core-change path.

| Option | Description | Selected |
|--------|-------------|----------|
| One batched call, filter to configured instruments | Single HTTP request per poll cycle regardless of instrument count — minimal rate-limit impact | ✓ |
| One call per configured linear instrument | More requests (N per poll cycle), per-instrument error isolation | |

**User's choice:** One batched call, filter to configured instruments (recommended)

---

## Fields captured per OI record

| Option | Description | Selected |
|--------|-------------|----------|
| Both fields (open_interest + open_interest_value) | Full fidelity — base quantity and USD notional both useful | ✓ |
| open_interest only (base quantity) | Matches venue's primary OI metric; value derivable later | |

**User's choice:** Both fields

| Option | Description | Selected |
|--------|-------------|----------|
| Use the HTTP response's top-level timestamp (Bybit server time) | Most accurate "when this OI snapshot was true" per the venue | ✓ |
| Use local clock.timestamp_ns() at poll time | Simpler, consistent with other recorder timers | |

**User's choice:** Use the HTTP response's top-level timestamp (Bybit server time)

---

## Persistence: every poll vs dedup-on-change

| Option | Description | Selected |
|--------|-------------|----------|
| Record every poll (raw timeseries) | Simplest — fixed-cadence timeseries, matches "capture as much data as possible" priority | ✓ |
| Dedup on value-change (like funding rate) | Smaller catalog, but loses fixed-cadence shape | |

**User's choice:** Record every poll
**Notes:** User asked whether dedup would save meaningful disk space ("MB of data"). Analysis: OI is a continuously-changing aggregate (unlike funding rate, static for ~8h) — it changes on nearly every poll, so dedup would skip few rows. Records are tiny (~1,440 rows/instrument/day at 60s cadence, well under 1MB/day across all instruments). Recommended "record every poll" on that basis; user agreed.

Follow-up (combined with above): writer choice —

| Option | Description | Selected |
|--------|-------------|----------|
| Add to kernel "*" writer's include_types | Simplest — OI flows through existing StreamingConfig/rotation/conversion machinery | ✓ (implied by "record every poll") |
| Separate strategy-owned writer (like funding rate) | Only needed for dedup-on-change | |

**User's choice:** Add to kernel "*" writer's include_types (follows from "record every poll" decision; not re-asked separately)

---

## Custom Data type identity/shape

| Option | Description | Selected |
|--------|-------------|----------|
| OpenInterestUpdate | Mirrors MarkPriceUpdate/IndexPriceUpdate/FundingRateUpdate naming convention | ✓ |
| BybitOpenInterest | Venue-prefixed, like Betfair's BSPOrderBookDelta | |

**User's choice:** OpenInterestUpdate

| Option | Description | Selected |
|--------|-------------|----------|
| scripts/bybit_recorder/data_types.py using customdataclass_pyo3 | Keeps new code in scripts/bybit_recorder/ per CLAUDE.md; registers Arrow + pyo3 catalog path | ✓ |
| You decide (Claude's discretion during planning) | Leave to planner | |

**User's choice:** scripts/bybit_recorder/data_types.py using customdataclass_pyo3 (recommended)

---

## Claude's Discretion

- Default value for `oi_poll_interval_seconds` (suggested 60s)
- Exact field types on `OpenInterestUpdate` (`Quantity` vs `float`, etc.)
- Where/how the polling timer + async HTTP call is wired into the synchronous strategy
- Symbol-to-InstrumentId correlation for ticker responses
- Exact `request_tickers()` Python call signature (e.g. `BybitTickersParams`)

## Deferred Ideas

None — discussion stayed within phase scope.
