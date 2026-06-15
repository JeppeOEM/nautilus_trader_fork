# Phase 6: Hot-Reload Config Changes - Context

**Gathered:** 2026-06-15
**Status:** Ready for planning

<domain>
## Phase Boundary

While the recorder is running, it periodically re-reads `recorder.toml` and diffs the configured instrument list/parameters against what's currently subscribed. It reacts to the FULL diff — newly-added instruments (loaded via the instrument provider and subscribed to the same data feeds as startup instruments), removed instruments (unsubscribed, recorded data untouched), and parameter changes for already-running instruments (order book depth, bar intervals — clean unsubscribe/resubscribe at new values). All of this happens without restarting the process or disrupting recording for unaffected instruments.

Note: this is broader than the original "Hot-Reload New Instruments" framing in ROADMAP.md — the actual scope is a full config-diff reload (additions + removals + param changes), not just new-instrument additions. The ROADMAP Phase 6 title/goal should be updated accordingly (see Canonical References).

</domain>

<decisions>
## Implementation Decisions

### Reload trigger mechanism
- **D-01:** Detection is via periodic polling: a `clock.set_timer` (same pattern as the existing `convert-stream`/`heartbeat` timers) periodically re-reads `recorder.toml` and diffs the parsed instrument list/params against the currently-running set.
- **D-02:** Poll cadence reuses the existing heartbeat-style cadence (e.g. `heartbeat_interval_seconds`, 30-60s) rather than introducing a new dedicated `reload_interval_seconds` knob.

### Scope: full diff (additions + removals + param changes)
- **D-03:** Scope covers the FULL diff between the running instrument set and the freshly-parsed `recorder.toml`: new instruments (additions), instruments no longer present (removals), and parameter changes (`depth`, `bar_intervals`) for instruments that remain configured but with different values.
- **D-04:** Newly hot-added instruments are fully integrated into existing reliability machinery — they participate in `_last_seen` heartbeat/stale-stream tracking (REL-03) going forward, exactly like startup instruments. `_log_restart_gaps()` does NOT apply to hot-added instruments (no restart occurred, so there's no prior catalog gap to report for them).
- **D-05:** For an instrument whose `depth` or `bar_intervals` changes in `recorder.toml`: apply a clean swap. Order book depth change → unsubscribe at old depth, subscribe at new depth. Bar interval changes → unsubscribe intervals removed from the list, subscribe newly-added intervals. Trade/quote/mark/index/funding subscriptions are unaffected by depth/interval changes (no resubscribe needed for those feeds).
- **D-06:** For an instrument removed from `recorder.toml` entirely: unsubscribe ALL feeds for that `instrument_id` (trade, quote, deltas, bars, and for linear: mark/index/funding). Already-recorded catalog data is left untouched — conversion/streaming continues to flush whatever was already buffered. The instrument stops appearing in future heartbeat/stale-stream checks (i.e. remove its entries from `_last_seen`).

### New-instrument / reload failure handling
- **D-07:** If a config reload finds a NEW instrument entry that is invalid (e.g. bad `depth`/`bar_intervals` per the existing fail-fast validation rules) or that Bybit's instrument provider can't load: log an ERROR, skip that instrument, and do NOT retry it on subsequent poll cycles — track which instrument_ids have already failed for the current config snapshot so the reload loop doesn't repeatedly attempt (and re-log) the same failed load every cycle. Only re-attempt if `recorder.toml` changes again (e.g. the entry is edited/re-added).
- **D-08:** Add a configurable guardrail knob (new `recorder.toml` key, validated `> 0` like sibling thresholds — Claude picks a sensible default during planning) for the maximum number of instruments that can be hot-added over the recorder's lifetime. When the count of hot-added instruments exceeds this threshold, log a WARNING (does not block further hot-adds — informational only).

### Loading new instruments into the cache at runtime
- **D-09:** Use Nautilus's built-in actor/data-engine instrument request mechanism (`request_instrument`/`request_instruments` → `on_instrument`/`on_instruments` callback path on `LiveDataClient`/Actor) to load a newly-discovered `instrument_id` into `self.cache`, rather than calling the adapter's `InstrumentProvider.load_ids_async()` directly. Exact request-API signatures (sync request method on Actor/Strategy vs. message-bus topic, callback shape) must be confirmed during phase research — this decision locks the PREFERRED mechanism (consistent with "prefer Nautilus built-ins"), not the exact call.
- **D-10:** Subscriptions for a newly-discovered instrument must wait until the instrument-load is confirmed (the instrument appears in `self.cache`, e.g. via the `on_instrument`/`on_instruments` callback) before calling `subscribe_trade_ticks`/`subscribe_quote_ticks`/etc. — mirrors `on_start`'s existing missing-instrument check (D-07 in Phase 1 / on_start's `RuntimeError` for missing instruments), just non-fatal here.
- **D-11:** If the instrument request/load for a new `instrument_id` comes back empty (Bybit doesn't recognize it), this maps to the SAME failure handling as D-07: log an error, skip, and don't retry until `recorder.toml` changes again.

### Claude's Discretion
- Exact mechanism/API for D-09 (request_instrument vs request_instruments, sync call vs async task, exact callback signature) — confirm during research against `nautilus_trader/live/data_client.py` and Actor-level request methods.
- Default value for the new instrument-count WARNING threshold (D-08).
- Internal bookkeeping structures for tracking "currently subscribed instruments + their params" vs. "previously-failed instrument_ids for this config snapshot" (e.g. dict/set fields on `RecorderStrategy`, parallel to `_last_seen`).
- Whether the reload-diff timer is a new `clock.set_timer` or piggybacks on the existing heartbeat timer callback (D-02 only fixes cadence, not implementation).
- Exact wording/key name for the new `recorder.toml` config knobs (instrument-count warning threshold; reload cadence if a separate knob ends up being needed).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### ROADMAP / Requirements (need updating)
- `.planning/ROADMAP.md` (Phase 6 section) — title/goal currently says "Hot-Reload New Instruments" / additions-only; needs updating to reflect the full-diff scope (additions + removals + param changes), e.g. retitle to "Hot-Reload Config Changes"
- `.planning/REQUIREMENTS.md` — has no requirement covering this capability yet; needs a new requirement entry (e.g. `HOT-01`) with a traceability row for Phase 6

### Existing recorder patterns to extend
- `scripts/bybit_recorder/config.py` — `load_recorder_config(path)`, `RecorderConfig`/`InstrumentEntry`, existing fail-fast `> 0` validation pattern for threshold knobs (`heartbeat_interval_seconds`, `stale_threshold_*`, `restart_gap_threshold_seconds`) — mirror for the new instrument-count-warning knob
- `scripts/bybit_recorder/strategy.py` (`on_start`, ~lines 152-210) — per-instrument `subscribe_trade_ticks`/`subscribe_quote_ticks`/`subscribe_order_book_deltas`/`subscribe_bars`, linear-only `subscribe_mark_prices`/`subscribe_index_prices`/`subscribe_funding_rates`; the hot-reload subscribe/unsubscribe logic must mirror these exact calls (including `BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL")` for bars and `BookType.L2_MBP` for deltas)
- `scripts/bybit_recorder/strategy.py` — `self.clock.set_timer` pattern (existing "convert-stream" and "heartbeat" timers) — the reload-diff check follows this pattern (D-01/D-02)
- `scripts/bybit_recorder/strategy.py` — `self._last_seen: dict[tuple[str, InstrumentId], int]` (REL-03 heartbeat/stale tracking) — hot-added instruments must be added to this dict (D-04); removed instruments must have their entries cleaned up (D-06)
- `scripts/bybit_recorder/strategy.py` — `_log_restart_gaps()` — confirms hot-added instruments are explicitly OUT of scope for this method (D-04)
- `scripts/bybit_recorder/recorder.py` — `InstrumentProviderConfig(load_ids=frozenset(instrument_ids))` is set ONCE at `TradingNodeConfig` construction; this is the key constraint that D-09's runtime instrument-request mechanism must work around (the provider's initial load list is fixed at startup, so newly-added instruments need a separate runtime load path)

### Instrument loading mechanism (research target for D-09)
- `nautilus_trader/live/data_client.py` (~lines 830-845) — `LiveDataClient.request_instrument(request: RequestInstrument)` / `request_instruments(request: RequestInstruments)` — candidate built-in mechanism for D-09; research must confirm the Actor/Strategy-level call that triggers these and the `on_instrument`/`on_instruments` callback signatures
- `nautilus_trader/common/providers.py` (~lines 76-140) — `InstrumentProvider.load_ids_async()`/`load_async()` — fallback reference if the message-bus request path (D-09 preferred) doesn't cleanly fit the synchronous strategy callback model

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `self.clock.set_timer` (existing heartbeat/conversion timers in `strategy.py`) — directly reusable for the reload-diff polling timer (D-01/D-02)
- `self._last_seen` dict and its update/cleanup patterns — directly reusable for integrating/removing hot-reloaded instruments from heartbeat tracking (D-04, D-06)
- `RecorderConfig`'s fail-fast `> 0` validation pattern in `config.py` — directly reusable for the new instrument-count-warning knob (D-08)

### Established Patterns
- Per-instrument subscription loop in `on_start` (trades/quotes/deltas/bars, plus linear-only mark/index/funding) — the hot-reload add/remove/swap logic must reuse these exact `subscribe_*`/`unsubscribe_*` calls per-instrument rather than re-deriving them
- D-04 linear-only gating pattern (iterate `self.config.linear_instrument_ids`, never the full instrument list, for mark/index/funding) — applies identically to hot-added linear instruments

### Integration Points
- `strategy.py::on_start` — new reload-diff timer registration (alongside existing "convert-stream"/"heartbeat" timers)
- `strategy.py` — new method(s) for: re-reading `recorder.toml`, diffing against current state, requesting new instruments (D-09), subscribing/unsubscribing per the diff (D-03/D-05/D-06), updating `_last_seen` (D-04/D-06), tracking failed/skipped instrument_ids (D-07/D-11) and hot-add count (D-08)
- `config.py::load_recorder_config` — already callable repeatedly (used once at startup); reused as-is for re-reading `recorder.toml` during reload polls
- `config.py::RecorderConfig` — new instrument-count-warning threshold field + validation (D-08)

</code_context>

<specifics>
## Specific Ideas

No specific UI/UX references — this is a backend live-reload capability. The core constraint is `recorder.py`'s one-time `InstrumentProviderConfig(load_ids=...)` (fixed at `TradingNodeConfig` construction), which D-09's runtime instrument-request mechanism must work around without modifying `nautilus_trader/` core.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 6-Hot-Reload Config Changes*
*Context gathered: 2026-06-15*
