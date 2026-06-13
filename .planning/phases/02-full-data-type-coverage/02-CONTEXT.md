# Phase 2: Full Data-Type Coverage - Context

**Gathered:** 2026-06-13
**Status:** Ready for planning

<domain>
## Phase Boundary

The recorder (built in Phase 1, which proved the pipeline end-to-end with trade ticks for two instruments) widens to subscribe to and record the remaining data types for every configured instrument: quote ticks, order-book deltas (per configured depth), bars/klines (per configured intervals), funding rate (linear only), and mark/index price (linear only) — all flowing through the proven `StreamingConfig`/`StreamingFeatherWriter` -> `ParquetDataCatalog` pipeline from Phase 1. No new persistence mechanism; this phase is about subscriptions, config consumption, and `StreamingConfig.include_types` widening.

</domain>

<decisions>
## Implementation Decisions

### Funding Rate (REC-05)
- **D-01:** Funding rate is deduped on value-change, not recorded on every ticker push. The strategy caches the last-seen `FundingRateUpdate.rate` per linear instrument (in-memory, per-instance) and only lets a new `FundingRateUpdate` flow to the streaming writer when the rate differs from the cached value. This keeps the catalog to a handful of funding-rate rows per instrument per day instead of millions (Bybit's linear ticker pushes ~100ms).
- Implementation detail (planner's discretion): how exactly to gate "flow to streaming writer" — likely the strategy receives `FundingRateUpdate` via its own handler (not the implicit "*" msgbus passthrough used for trades in Phase 1) and explicitly publishes/persists only on change. Research should confirm the cleanest mechanism given `StreamingConfig.include_types`.

### Bars/Klines (REC-04)
- **D-02:** Subscribe to Bybit's venue-native kline stream for each configured `bar_intervals` entry, per instrument — in ADDITION to trades/quotes/order-book (not as a replacement or derived-from-trades aggregation). Rationale: maximize total raw data captured; venue klines are an independent stream and exchange-accurate; bars can always be re-derived from recorded trades later if a different interval is needed.
- Bar interval strings in config (e.g. `"1-MINUTE"`) must map to Nautilus `BarType`/`BarSpecification` with venue-native aggregation source — research should confirm the exact `BarType` string format Bybit's adapter expects (e.g. `{instrument_id}-{interval}-LAST-EXTERNAL` vs `-INTERNAL`).

### Order Book Depth Validation (REC-03)
- **D-03:** Spot order-book depth is capped at 50 by the venue. If a `[[instruments.spot]]` entry specifies `depth > 50`, the recorder fails fast at startup with a clear error naming the offending instrument and its configured depth — consistent with the Phase 1 fail-fast philosophy (D-05/D-06). No silent clamping.
- This validation happens alongside/near the existing CONF-03 instrument-cache validation in `on_start` (or earlier, at config-load time — planner's discretion on exact location, but it must happen before any subscription is issued).

### Mark/Index Price & Funding Rate Scope (REC-05/REC-06)
- **D-04:** Funding rate, mark price, and index price subscriptions are automatic for every instrument in `[[instruments.linear]]` — no new per-instrument opt-in flags added to the TOML schema. Spot instruments (`[[instruments.spot]]`) never get these subscriptions (the venue doesn't have them). This matches REC-05/06's blanket "for linear perpetuals" wording and keeps the config schema simple (D-07 from Phase 1 anticipated optional per-instrument funding/OI flags, but they are not needed here).

### Quote Ticks (REC-02)
- No open gray area — `subscribe_quote_ticks(instrument_id)` for every configured instrument (linear + spot), same pattern as Phase 1's trade-tick subscription. Add `QuoteTick` to `StreamingConfig.include_types`.

### Claude's Discretion
- Exact code organization for the new subscription/handler logic in `RecorderStrategy` (e.g. whether funding-rate dedup state lives as instance dict attributes, helper methods, etc.)
- Exact `BarType`/`BarSpecification` string construction from the `bar_intervals` config strings
- Whether order-book-depth validation happens at config-load time (in `config.py`) or in `on_start` (in `strategy.py`) — as long as it's fail-fast before subscriptions are issued
- Whether `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate` need any `CUSTOM_ENCODINGS` or Arrow-schema registration beyond what Phase 1 already established (research should check if these are already registered as native catalog types, given they appear to be native `Data` types per `nautilus_trader.model.data`)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project-level
- `.planning/PROJECT.md` — core value, constraints (no Redis, single process, official ParquetDataCatalog, systemd), key decisions table
- `.planning/REQUIREMENTS.md` — REC-02 through REC-06 (this phase's requirement set)
- `.planning/ROADMAP.md` — Phase 2 goal and success criteria
- `.planning/phases/01-bootstrap-config-end-to-end-slice/01-CONTEXT.md` — Phase 1 decisions (D-01 through D-10) that this phase builds on (instance_id stability, conversion scheduling, TOML schema, fail-fast validation pattern)
- `.planning/phases/01-bootstrap-config-end-to-end-slice/01-04-SUMMARY.md` — `CUSTOM_ENCODINGS` pattern for pyo3-native adapter enums; A2/A3 empirical resolutions; note that this pattern should be reused/extended if new Bybit-specific enum types appear

### Existing recorder code (Phase 1 output — to be extended, not replaced)
- `scripts/bybit_recorder/strategy.py` — `RecorderStrategy`/`RecorderStrategyConfig`, `on_start` validation+subscription pattern, `_convert_stream` timer
- `scripts/bybit_recorder/config.py` — `RecorderConfig`/`InstrumentEntry`, `load_recorder_config`, `build_streaming_config` (where `StreamingConfig.include_types` is widened)
- `scripts/bybit_recorder/recorder.py` — `TradingNode` bootstrap, `CUSTOM_ENCODINGS` registrations
- `scripts/bybit_recorder/recorder.toml` — TOML config with `[[instruments.linear]]` / `[[instruments.spot]]`, each with `id`, `depth`, `bar_intervals`

### Bybit adapter (data types & subscriptions)
- `nautilus_trader/adapters/bybit/data.py` — `_subscribe_mark_prices`, `_subscribe_index_prices`, `_subscribe_funding_rates` (around lines 370-420); `FundingRateUpdate.from_pyo3`/`from_pyo3_list` (confirms `FundingRateUpdate` is a native Nautilus `Data` type, not custom)
- `nautilus_trader/data/messages.py` — `SubscribeFundingRates`, `SubscribeMarkPrices`, `SubscribeIndexPrices`, `RequestFundingRates` message types

No external ADRs/specs beyond PROJECT.md/REQUIREMENTS.md/ROADMAP.md exist for this milestone.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/bybit_recorder/strategy.py:RecorderStrategy.on_start` — existing fail-fast validation + subscription loop; extend with order-book-depth validation (D-03) and new subscription calls (quotes, order book, bars, and linear-only funding/mark/index)
- `scripts/bybit_recorder/config.py:InstrumentEntry` — already has `depth` and `bar_intervals` fields parsed but unused in Phase 1; Phase 2 consumes them
- `scripts/bybit_recorder/config.py:build_streaming_config` — `StreamingConfig.include_types=[TradeTick]` is the single point to widen with `QuoteTick`, `OrderBookDelta`, `Bar`, `FundingRateUpdate`, `MarkPriceUpdate`, `IndexPriceUpdate`
- `recorder.py` `CUSTOM_ENCODINGS` pattern — reuse if any new pyo3-native enum types surface during config serialization for the new subscriptions

### Established Patterns
- Strategy validates configured instruments against `self.cache.instrument()` in `on_start` before subscribing (CONF-03 pattern, D-05/D-06) — extend this validation step for the new order-book-depth check (D-03)
- Linear vs spot instruments are already structurally separated in config (`instruments.linear` / `instruments.spot` arrays) — use this existing separation to gate linear-only subscriptions (D-04), no new schema field needed

### Integration Points
- All new data types flow to disk via the existing `StreamingFeatherWriter` + scheduled `_convert_stream` conversion (`RecorderStrategy._convert_stream`) — just need `include_types` widened and `data_cls` passed correctly to `convert_stream_to_data` for each type (or confirm whether `convert_stream_to_data` needs per-type calls or handles a list)
- Funding-rate dedup (D-01) is new logic not present in Phase 1 — likely a new instance-level cache dict in `RecorderStrategy.__init__`/`on_start` keyed by `InstrumentId`

</code_context>

<specifics>
## Specific Ideas

- User's stated priority: "I want to have access to full order book and as much data as possible" — drove the venue-klines-additive decision (D-02). This phase should err toward capturing MORE independent data streams, not consolidating/deriving.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 2-Full Data-Type Coverage*
*Context gathered: 2026-06-13*
