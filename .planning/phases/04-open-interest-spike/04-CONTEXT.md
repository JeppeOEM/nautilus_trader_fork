# Phase 4: Open Interest Spike - Context

**Gathered:** 2026-06-15
**Status:** Ready for planning

<domain>
## Phase Boundary

Open interest for linear perpetuals is recorded into the catalog via a custom Nautilus `Data` subclass with Arrow serializer registration. There is no native framework support for OI, so this phase is isolated as a spike: define the custom type, get a value into it (via REST polling), and prove it round-trips through `ParquetDataCatalog`.

</domain>

<decisions>
## Implementation Decisions

### Extraction path & polling cadence
- **D-01:** Use REST polling via `BybitHttpClient.request_tickers()` (already exposed to Python via the `request_tickers` pyo3 binding, returns `open_interest` and `open_interest_value` for linear tickers). The WS-ticker path is NOT viable: `BybitWsTickerLinear` carries an `open_interest` field, but no Rust parser converts it to a Python-exposed event — adding one would require modifying `crates/adapters/bybit` (core), which CLAUDE.md forbids.
- **D-02:** Poll interval is configurable via a new `oi_poll_interval_seconds` key in `recorder.toml` (mirrors the existing config-knob pattern for thresholds like `restart_gap_threshold_seconds`, `heartbeat_interval_seconds`). Claude picks a sensible default (suggested: 60s) and validates it `> 0` at load, consistent with sibling threshold validations in `config.py`.
- **D-03:** Each poll cycle makes ONE batched `request_tickers(category=linear)` call covering all linear tickers, then filters the response down to `self.config.linear_instrument_ids`. Do NOT make per-instrument calls.

### Fields captured per OI record
- **D-04:** Capture BOTH `open_interest` (base-asset quantity) and `open_interest_value` (quote/USD notional) from the ticker response — full fidelity, notional enables cross-instrument comparison.
- **D-05:** `ts_event` is set from the HTTP response's top-level timestamp (Bybit server time for the snapshot), not the local clock at poll time. `ts_init` follows the existing recorder convention (local clock at processing time).

### Persistence: every poll, no dedup
- **D-06:** Record EVERY poll as a row — no dedup-on-change. Unlike `FundingRateUpdate` (static for ~8h, deduped via a strategy-owned writer in Phase 2), open interest is a continuously-changing aggregate that differs on nearly every poll. Dedup would save negligible space (records are tiny: ~1,440 rows/instrument/day at 60s cadence, well under 1MB/day across all configured instruments) and would lose the clean fixed-cadence timeseries shape.
- **D-07:** Because every poll is recorded, `OpenInterestUpdate` is added to the kernel `"*"` `StreamingFeatherWriter`'s `include_types` (alongside `MarkPriceUpdate`/`IndexPriceUpdate`/etc.) — NOT a separate strategy-owned writer. The strategy-owned-writer pattern exists specifically to exclude a deduped type from the kernel's raw passthrough, which doesn't apply here.

### Custom Data type identity/shape
- **D-08:** Name the new type `OpenInterestUpdate` — mirrors the existing venue-update naming convention already used in this recorder (`MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`), even though it's a new custom type rather than a built-in Nautilus one.
- **D-09:** Define `OpenInterestUpdate` in a new module `scripts/bybit_recorder/data_types.py`, using `customdataclass_pyo3` (per the `nautilus_trader/adapters/betfair/data_types.py` pattern: `customdataclass` + `register_custom_data_class` for the pyo3 catalog path) so it registers Arrow serialization AND round-trips through `ParquetDataCatalog.write_custom_data()` / `catalog.query("OpenInterestUpdate", ...)`. This keeps all new code in `scripts/bybit_recorder/`, per CLAUDE.md.

### Claude's Discretion
- Exact default value for `oi_poll_interval_seconds` (suggested 60s, but confirm during planning/research based on Bybit's actual OI update cadence).
- Exact field names/types on `OpenInterestUpdate` (e.g., `open_interest: Quantity` vs `float`, `open_interest_value: float`) — follow `customdataclass_pyo3` conventions and existing recorder type-hint style.
- Where the polling timer is set up (`on_start`, alongside the existing `clock.set_timer` calls for heartbeat/conversion) and how the async `request_tickers()` HTTP call is invoked from the synchronous strategy callback (research should confirm the cleanest mechanism — e.g., `asyncio` task scheduling pattern already used elsewhere in the recorder, if any).
- How `OpenInterestUpdate.instrument_id` correlates ticker-response symbols back to configured `InstrumentId`s (likely the same symbol-to-InstrumentId mapping already used elsewhere in `strategy.py`).
- Whether `request_tickers()` requires a `category` param object (`BybitTickersParams` per `crates/adapters/bybit/src/python/params.py`) — research should confirm the exact Python call signature.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Custom Data type pattern (Arrow + pyo3 catalog registration)
- `nautilus_trader/model/custom.py` — `customdataclass` / `customdataclass_pyo3` decorators: registers Arrow serializer (`register_arrow`) and serializable type (`register_serializable_type`); `customdataclass_pyo3` additionally supports `register_custom_data_class` for the pyo3 `ParquetDataCatalog` path
- `nautilus_trader/adapters/betfair/data_types.py` — reference implementation of a venue-specific custom `Data` subclass with Arrow registration (`BSPOrderBookDelta`)

### Bybit OI data source
- `crates/adapters/bybit/src/python/http.rs` (~lines 526-555) — `BybitHttpClient.request_tickers()` pyo3 binding, returns `BybitTickerData` with `open_interest()` / `open_interest_value()` getters (per `crates/adapters/bybit/src/http/models.rs` ~lines 174-180, 269, 343-347)
- `crates/adapters/bybit/src/websocket/messages.rs` (~line 628) — confirms `BybitWsTickerLinear.open_interest` exists on the WS struct but has NO corresponding Rust parser to a Python-exposed event (rules out the WS path without core changes)

### Existing recorder patterns to extend
- `scripts/bybit_recorder/strategy.py` (~lines 152-210) — `on_start`, existing `clock.set_timer` calls (heartbeat/conversion timers) — new OI poll timer follows this pattern
- `scripts/bybit_recorder/strategy.py` (~lines 40-51) — `StreamingConfig`/`include_types` list where `OpenInterestUpdate` gets added (alongside `MarkPriceUpdate`, `IndexPriceUpdate`, etc.)
- `scripts/bybit_recorder/config.py` (~line 79+, `RecorderConfig`) — existing fail-fast `> 0` validation pattern for threshold config knobs (e.g. `restart_gap_threshold_seconds`), to mirror for `oi_poll_interval_seconds`

### Project-level
- `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md` (OI-01), `.planning/ROADMAP.md` (Phase 4 section) — requirement wording and success criteria (unchanged by this discussion)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `customdataclass_pyo3` (`nautilus_trader/model/custom.py`) — handles Arrow schema derivation, `to_dict`/`from_dict`/`to_arrow`/`from_arrow`, and pyo3 catalog registration for free once the dataclass fields are defined
- Existing `clock.set_timer` pattern in `strategy.py` for periodic work (heartbeat, conversion) — directly reusable for the OI poll timer
- `RecorderConfig`'s fail-fast `> 0` validation pattern in `config.py` — directly reusable for `oi_poll_interval_seconds`

### Established Patterns
- Venue-update naming convention (`MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`) — `OpenInterestUpdate` follows this even as a new custom type
- `StreamingConfig.include_types` is the mechanism for routing a `Data` type to the kernel `"*"` `StreamingFeatherWriter` — `OpenInterestUpdate` joins this list (D-07)

### Integration Points
- New module: `scripts/bybit_recorder/data_types.py` (defines `OpenInterestUpdate`)
- `strategy.py::on_start` — new poll timer setup
- `strategy.py` `_RECORDED_TYPES` / `StreamingConfig.include_types` — add `OpenInterestUpdate`
- `config.py::RecorderConfig` / `load_recorder_config` — add `oi_poll_interval_seconds` with validation

</code_context>

<specifics>
## Specific Ideas

No specific UI/UX references — this is a backend data-capture spike. The core constraint driving every decision is CLAUDE.md's "never modify nautilus_trader/ core" rule, which rules out the WS-ticker path and shapes D-01 through D-03.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 4-Open Interest Spike*
*Context gathered: 2026-06-15*
