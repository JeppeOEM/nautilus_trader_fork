# Phase 6: Hot-Reload Config Changes - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-15
**Phase:** 6-Hot-Reload Config Changes
**Areas discussed:** Reload trigger mechanism, Scope: additions only vs. full diff, New-instrument failure handling, Loading new instruments into the cache at runtime

---

## Reload trigger mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Periodic poll on a timer (Recommended) | A `clock.set_timer` (like heartbeat/conversion) periodically re-reads `recorder.toml`, diffs the instrument list against the running set, and reacts to new entries | ✓ |
| Filesystem watch (inotify) | React immediately to file changes | |
| External signal (SIGHUP) | Operator explicitly triggers a reload | |

**User's choice:** Periodic poll on a timer (Recommended)

| Option | Description | Selected |
|--------|-------------|----------|
| Same cadence as heartbeat (e.g. 30-60s) | Reuse `heartbeat_interval_seconds`-style cadence rather than a new dedicated knob | ✓ |
| Configurable, separate knob (e.g. `reload_interval_seconds`) | Independent tuning | |
| You decide (Claude's discretion) | | |

**User's choice:** Same cadence as heartbeat (e.g. 30-60s)

---

## Scope: additions only vs. full diff

| Option | Description | Selected |
|--------|-------------|----------|
| Additions only (Recommended) | Only handle new instruments appearing | |
| Additions + removals | | |
| Additions + param changes for new instruments only | | |

**User's choice:** Free text — "i want to check for params change, and additions + removals"
**Notes:** Interpreted as the broadest "full diff" scope: additions, removals, AND param changes (depth/bar_intervals) for already-running instruments. This expands the phase scope from the original "Hot-Reload New Instruments" framing — ROADMAP retitled to "Hot-Reload Config Changes" and a new requirement HOT-01 added to REQUIREMENTS.md to cover the broadened scope.

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, fully integrate (Recommended) | Hot-reloaded instruments are treated identically to startup instruments for heartbeat/stale tracking going forward | ✓ |
| Minimal -- just subscriptions | | |

**User's choice:** Yes, fully integrate (Recommended)
**Notes:** `_log_restart_gaps` doesn't apply to hot-added instruments (no restart happened, no prior catalog gap to report).

| Option | Description | Selected |
|--------|-------------|----------|
| Clean swap (Recommended) | Unsubscribe affected feed(s) at old parameters, subscribe at new ones | ✓ |
| Additive only | | |

**User's choice:** Clean swap (Recommended)
**Notes:** Order book depth change → unsub+resub at new depth; bar intervals → unsub removed intervals, sub newly-added ones. Trade/quote/mark/index/funding subscriptions unaffected by depth/interval changes.

| Option | Description | Selected |
|--------|-------------|----------|
| Unsubscribe all feeds, keep recorded data (Recommended) | Unsubscribe all feeds for the removed instrument_id; already-recorded catalog data untouched | ✓ |
| You decide (Claude's discretion) | | |

**User's choice:** Unsubscribe all feeds, keep recorded data (Recommended)
**Notes:** Conversion/streaming continues to flush whatever was already buffered. Instrument stops appearing in future heartbeat/stale-stream checks.

---

## New-instrument failure handling

| Option | Description | Selected |
|--------|-------------|----------|
| Log error, skip that instrument, keep running (Recommended) | Retry on subsequent reload cycles | |
| Log error, skip, and do NOT retry until config changes again | Track failed instrument_ids for current config snapshot | ✓ |
| Crash the process (fail-fast like startup) | | |

**User's choice:** Log error, skip, and do NOT retry until config changes again

| Option | Description | Selected |
|--------|-------------|----------|
| No explicit limit -- rely on adapter (Recommended) | | |
| Log a WARNING above some threshold | | ✓ |

**User's choice:** Log a WARNING above some threshold

| Option | Description | Selected |
|--------|-------------|----------|
| Configurable knob with sensible default (Recommended) | New recorder.toml key, validated like other thresholds | ✓ |
| Hardcoded constant | | |

**User's choice:** Configurable knob with sensible default (Recommended)

---

## Loading new instruments into the cache at runtime

| Option | Description | Selected |
|--------|-------------|----------|
| Built-in request_instrument(s) via message bus (Recommended) | Use Nautilus's existing actor/data-engine request mechanism (request_instrument/request_instruments -> on_instrument/on_instruments callback) | ✓ |
| Direct InstrumentProvider.load_ids_async() call | Schedule an asyncio task that calls the adapter's InstrumentProvider directly | |
| You decide (Claude's discretion during research) | | |

**User's choice:** Built-in request_instrument(s) via message bus (Recommended)
**Notes:** Exact API (Actor-level request method, on_instrument/on_instruments callback signatures) to be confirmed during phase research against `nautilus_trader/live/data_client.py`. Locks the preferred mechanism, consistent with "prefer Nautilus built-ins."

| Option | Description | Selected |
|--------|-------------|----------|
| Wait for load confirmation (Recommended) | Only subscribe once the instrument is confirmed present in self.cache | ✓ |
| Fire immediately, rely on buffering | | |

**User's choice:** Wait for load confirmation (Recommended)

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, same as Area 3 (Recommended) | Treat an empty/failed instrument load like the invalid-instrument case: log error, skip, don't retry until config changes again | ✓ |
| Different handling -- explain | | |

**User's choice:** Yes, same as Area 3 (Recommended)

---

## Claude's Discretion

- Exact mechanism/API for loading new instruments (request_instrument vs request_instruments, sync vs async, callback signature) — confirm during research
- Default value for the instrument-count WARNING threshold
- Internal bookkeeping structures (running-subscription state, failed-instrument tracking) — parallel to existing `_last_seen`
- Whether the reload-diff timer is new or piggybacks on the heartbeat timer callback
- Exact recorder.toml key names for new config knobs

## Deferred Ideas

None — discussion stayed within phase scope.
