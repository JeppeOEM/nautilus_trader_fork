# Phase 6: Hot-Reload Config Changes - Research

**Researched:** 2026-06-15
**Domain:** Live runtime reconfiguration of a NautilusTrader `Strategy` (instrument hot-add/remove/param-swap) against the Bybit adapter, without restart and without modifying `nautilus_trader/` core.
**Confidence:** HIGH (all core mechanisms verified by reading the fork's own source; the one risk area — runtime instrument loading for Bybit — is resolved with a concrete, source-grounded answer that overturns the D-09 *preferred* mechanism).

## Summary

The phase adds a periodic config-diff reload to `scripts/bybit_recorder/strategy.py`: a `clock.set_timer` callback re-reads `recorder.toml`, diffs the parsed instrument set/params against the running set, and applies additions (load + subscribe), removals (unsubscribe all feeds + cleanup `_last_seen`), and depth/bar-interval param changes (clean unsubscribe→resubscribe). Every subscribe/unsubscribe primitive the phase needs already exists on `Strategy`/`Actor` (inherited, in `nautilus_trader/common/actor.pyx`) and is fully implemented in the Bybit data client — so removals and param-swaps are low-risk and can be built entirely from existing public methods.

The single hard problem is **D-09 (loading a brand-new instrument into the cache at runtime)**. The CONTEXT *preferred* mechanism — `Actor.request_instrument()` → `on_instrument` callback — **does not work for the Bybit adapter**: `BybitDataClient` (via `LiveMarketDataClient`) does not implement `_request_instrument`/`_request_instruments`; the base raises `NotImplementedError`, which `create_task` swallows as a logged error. The instrument never lands in `self.cache`, so the D-10 "wait until cached" guard would silently never fire and every hot-added instrument would be permanently skipped (collapsing into D-11). This is a VERIFIED finding from reading the fork source, not an assumption. The viable fallback is to reach the Bybit data client's `InstrumentProvider` at runtime and call `load_async(instrument_id)` (which fetches the single symbol from Bybit and `add()`s it to the provider), then push it into the cache + WS-client instrument cache. The provider is reachable but **not through the public Actor API** (`Strategy` exposes only `msgbus`, `cache`, `clock`) — it requires a documented composition workaround (holding a reference to the data client, injected at `recorder.py` wiring time) that stays entirely inside `scripts/bybit_recorder/` per the no-core-edit constraint.

**Primary recommendation:** Build removals and param-swaps from existing `unsubscribe_*`/`subscribe_*` calls (trivial, fully supported). For new-instrument hot-add, do NOT rely on `request_instrument()` for Bybit — instead inject a reference to the Bybit data client (or its `instrument_provider`) into `RecorderStrategy` at `recorder.py` build time, and on hot-add call `await provider.load_async(id)` → `cache.add_instrument(...)` → the WS client's `cache_instrument(...)` before issuing subscriptions. Gate the whole new-instrument path behind a `checkpoint:human-verify` live smoke test, because the cross-component wiring is the only part not exercisable by pure unit tests.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Re-read & parse `recorder.toml` | Config (`config.py::load_recorder_config`) | — | Already idempotent/re-callable; pure parse + validate |
| Diff running set vs. parsed config | Strategy (`RecorderStrategy`) | — | Strategy owns the bookkeeping of what is currently subscribed |
| Subscribe/unsubscribe feeds | Strategy (`Actor` subscribe API) → DataEngine → DataClient | Bybit WS client | Public `subscribe_*`/`unsubscribe_*` route a command via msgbus to the adapter |
| Load a NEW instrument at runtime | Bybit `InstrumentProvider.load_async` | Cache + WS client `cache_instrument` | Provider is the only component that fetches a symbol from Bybit; cache + WS-client caches must be populated for parsing |
| Heartbeat/stale tracking of hot instruments | Strategy (`_last_seen`) | — | Existing REL-03 machinery; hot instruments join/leave the dict |
| Periodic reload trigger | Strategy (`clock.set_timer`) | — | Mirrors existing `convert-stream`/`heartbeat` timers (D-01/D-02) |

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Reload trigger mechanism**
- **D-01:** Detection is via periodic polling: a `clock.set_timer` (same pattern as the existing `convert-stream`/`heartbeat` timers) periodically re-reads `recorder.toml` and diffs the parsed instrument list/params against the currently-running set.
- **D-02:** Poll cadence reuses the existing heartbeat-style cadence (e.g. `heartbeat_interval_seconds`, 30-60s) rather than introducing a new dedicated `reload_interval_seconds` knob.

**Scope: full diff (additions + removals + param changes)**
- **D-03:** Scope covers the FULL diff between the running instrument set and the freshly-parsed `recorder.toml`: new instruments (additions), instruments no longer present (removals), and parameter changes (`depth`, `bar_intervals`) for instruments that remain configured but with different values.
- **D-04:** Newly hot-added instruments are fully integrated into existing reliability machinery — they participate in `_last_seen` heartbeat/stale-stream tracking (REL-03) going forward, exactly like startup instruments. `_log_restart_gaps()` does NOT apply to hot-added instruments (no restart occurred, so there's no prior catalog gap to report for them).
- **D-05:** For an instrument whose `depth` or `bar_intervals` changes in `recorder.toml`: apply a clean swap. Order book depth change → unsubscribe at old depth, subscribe at new depth. Bar interval changes → unsubscribe intervals removed from the list, subscribe newly-added intervals. Trade/quote/mark/index/funding subscriptions are unaffected by depth/interval changes (no resubscribe needed for those feeds).
- **D-06:** For an instrument removed from `recorder.toml` entirely: unsubscribe ALL feeds for that `instrument_id` (trade, quote, deltas, bars, and for linear: mark/index/funding). Already-recorded catalog data is left untouched — conversion/streaming continues to flush whatever was already buffered. The instrument stops appearing in future heartbeat/stale-stream checks (i.e. remove its entries from `_last_seen`).

**New-instrument / reload failure handling**
- **D-07:** If a config reload finds a NEW instrument entry that is invalid (e.g. bad `depth`/`bar_intervals` per the existing fail-fast validation rules) or that Bybit's instrument provider can't load: log an ERROR, skip that instrument, and do NOT retry it on subsequent poll cycles — track which instrument_ids have already failed for the current config snapshot so the reload loop doesn't repeatedly attempt (and re-log) the same failed load every cycle. Only re-attempt if `recorder.toml` changes again (e.g. the entry is edited/re-added).
- **D-08:** Add a configurable guardrail knob (new `recorder.toml` key, validated `> 0` like sibling thresholds — Claude picks a sensible default during planning) for the maximum number of instruments that can be hot-added over the recorder's lifetime. When the count of hot-added instruments exceeds this threshold, log a WARNING (does not block further hot-adds — informational only).

**Loading new instruments into the cache at runtime**
- **D-09:** Use Nautilus's built-in actor/data-engine instrument request mechanism (`request_instrument`/`request_instruments` → `on_instrument`/`on_instruments` callback path on `LiveDataClient`/Actor) to load a newly-discovered `instrument_id` into `self.cache`, rather than calling the adapter's `InstrumentProvider.load_ids_async()` directly. Exact request-API signatures (sync request method on Actor/Strategy vs. message-bus topic, callback shape) must be confirmed during phase research — this decision locks the PREFERRED mechanism (consistent with "prefer Nautilus built-ins"), not the exact call.
- **D-10:** Subscriptions for a newly-discovered instrument must wait until the instrument-load is confirmed (the instrument appears in `self.cache`, e.g. via the `on_instrument`/`on_instruments` callback) before calling `subscribe_trade_ticks`/`subscribe_quote_ticks`/etc. — mirrors `on_start`'s existing missing-instrument check (D-07 in Phase 1 / on_start's `RuntimeError` for missing instruments), just non-fatal here.
- **D-11:** If the instrument request/load for a new `instrument_id` comes back empty (Bybit doesn't recognize it), this maps to the SAME failure handling as D-07: log an error, skip, and don't retry until `recorder.toml` changes again.

### Claude's Discretion
- Exact mechanism/API for D-09 (request_instrument vs request_instruments, sync call vs async task, exact callback signature) — confirm during research against `nautilus_trader/live/data_client.py` and Actor-level request methods. **[RESEARCH RESULT: the D-09 preferred path does NOT work for Bybit — see Pitfall 1 and the Architecture Patterns section. The fallback path is recommended.]**
- Default value for the new instrument-count WARNING threshold (D-08). **[RESEARCH RECOMMENDATION: see Open Question 1.]**
- Internal bookkeeping structures for tracking "currently subscribed instruments + their params" vs. "previously-failed instrument_ids for this config snapshot" (e.g. dict/set fields on `RecorderStrategy`, parallel to `_last_seen`). **[RESEARCH RECOMMENDATION: see Architecture Patterns Pattern 2.]**
- Whether the reload-diff timer is a new `clock.set_timer` or piggybacks on the existing heartbeat timer callback (D-02 only fixes cadence, not implementation). **[RESEARCH RECOMMENDATION: separate named timer `"config-reload"` for testability and log clarity — see Pattern 1.]**
- Exact wording/key name for the new `recorder.toml` config knobs (instrument-count warning threshold; reload cadence if a separate knob ends up being needed). **[RESEARCH RECOMMENDATION: `max_hot_added_instruments` — see Open Question 1.]**

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| HOT-01 | While running, the recorder periodically detects changes to `recorder.toml`'s instrument list/params (additions, removals, depth/bar_interval changes) and applies them live — loading new instruments and subscribing/unsubscribing the affected feeds — without restarting the process or disrupting recording for unaffected instruments | Timer-diff pattern (Pattern 1), runtime instrument load (Pattern 3 + Pitfall 1), per-feed unsubscribe/subscribe (Code Examples), clean-swap ordering (Pitfall 2), bookkeeping (Pattern 2). All subscribe/unsubscribe primitives verified present & implemented for Bybit. |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **NEVER modify `nautilus_trader/` core.** All hot-reload code is NEW code in `scripts/bybit_recorder/` (+ its tests under `tests/unit_tests/persistence/recorder/`). The runtime-instrument-load workaround (Pattern 3) must therefore be done via composition/injection in `scripts/bybit_recorder/recorder.py` + `strategy.py`, never by editing the Bybit adapter or core.
- **Prefer Nautilus built-ins; extend later if needed.** D-09 encodes this preference. Research shows the literal built-in (`request_instrument`) is non-functional for Bybit, so the recommended fallback is the *minimal* composition that still uses Nautilus's own `InstrumentProvider.load_async` + `cache.add_instrument` rather than any hand-rolled HTTP/WS code.
- **Tech stack:** Python only, no new Rust. `ruff format` line-length 100, target Python 3.12+, type hints required (mypy `disallow_incomplete_defs`). Module-level `logger = logging.getLogger(__name__)` already in place.
- **Config validation fail-fast `> 0`** pattern is established in `config.py` for every threshold knob — the new D-08 knob MUST follow it.
- **Test conventions:** `test_*.py` in `tests/unit_tests/persistence/recorder/`, AAA pattern, `pytest-mock` (`mocker.patch.object`), real `Cache`/`MessageBus`/`TestClock` (Cython `cdef` methods cannot be monkeypatched — register "present" instruments via `cache.add_instrument`, leave others out to simulate "missing").

## Standard Stack

### Core (all already in the repo — no new packages)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `nautilus_trader` (this fork) | local | `Strategy`/`Actor` subscribe/unsubscribe API, `clock.set_timer`, `Cache`, Bybit adapter | The entire phase is built on existing framework classes (CLAUDE.md constraint) |
| `tomllib` | stdlib (3.11+) | Re-parse `recorder.toml` each poll | Already used by `load_recorder_config` |
| `pandas` | per `uv.lock` | `pd.Timedelta` for timer interval | Already used for existing timers |
| `pytest` | 7.4.4 (held at 7.x) | Test runner | Repo standard (`pyproject.toml`) |
| `pytest-mock` | >=3.15.1,<4.0.0 | `mocker.patch.object` for subscribe spies | Used by all existing recorder tests |
| `pytest-asyncio` | 0.23.8 (pinned) | If any `async` provider-load test is needed | Repo standard |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `nautilus_trader.common.config.PositiveInt` | local | Type for the new D-08 knob on `RecorderStrategyConfig` | Mirror existing threshold fields |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Polling timer (D-01) | `watchdog`/inotify file-watch | Rejected by D-01 (polling locked); also adds a dependency and OS-specific behavior |
| Provider `load_async` fallback (Pattern 3) | `Actor.request_instrument()` (D-09 preferred) | **Non-functional for Bybit** — `_request_instrument` unimplemented (Pitfall 1). Keep `request_instrument` only if a future Bybit adapter version implements it. |
| Provider `load_async` fallback | Bybit `update_instruments_interval_mins` config | That polling task calls `initialize(reload=True)` → `load_ids_async(self._load_ids_on_start)` — only the ORIGINAL configured ids; never picks up newly-added ids. Does not solve hot-add. |

**Installation:** No new packages. (No `## Package Legitimacy Audit` section required — phase installs nothing.)

## Architecture Patterns

### System Architecture Diagram

```
                    recorder.toml (edited live by operator)
                              │
                              ▼  (re-read every poll)
   clock.set_timer("config-reload")  ──fires──►  _on_config_reload(event)
                                                        │
                          load_recorder_config(path)  ──┤ (parse + fail-fast validate)
                                                        │
                                                  diff vs. running set
                                                        │
                 ┌──────────────────────┬───────────────┴───────────────┐
                 ▼                      ▼                                ▼
            ADDITIONS               REMOVALS                      PARAM CHANGES
                 │                      │                                │
       (load instrument)      unsubscribe_* ALL feeds        unsubscribe old → subscribe new
                 │              + del _last_seen entries       (depth swap / bar-interval delta)
                 ▼                      │                                │
   ┌─ skip if invalid (D-07) ─┐        ▼                                ▼
   │  or load empty (D-11)    │   data untouched               subscribe_* (Actor API)
   ▼                          │   (catalog flush continues)            │
 provider.load_async(id) ─────┘                                        ▼
   │  (Pattern 3 / Pitfall 1)                              DataEngine ──► BybitDataClient
   ▼                                                            │            (WS sub/unsub)
 cache.add_instrument(inst)                                     ▼
 ws_client.cache_instrument(inst_pyo3)                   _last_seen updated by on_* handlers
   │
   ▼
 subscribe_* (same 5-7 feeds as on_start)  ──► joins _last_seen (D-04)
```

Data flow note: subscribe/unsubscribe are fire-and-forget commands routed through the message bus to the adapter; they do not return a result. The only request/response round-trip in scope is the *instrument load*, which (for Bybit) must go through the provider directly rather than the `request_instrument` msgbus path.

### Recommended Project Structure
```
scripts/bybit_recorder/
├── config.py        # + max_hot_added_instruments knob + validation (D-08); load_recorder_config reused as-is
├── strategy.py      # + _on_config_reload timer cb, diff logic, subscribe/unsubscribe helpers, bookkeeping
└── recorder.py      # + inject Bybit data-client/provider reference into RecorderStrategy (Pattern 3 wiring)
tests/unit_tests/persistence/recorder/
├── test_recorder_config.py      # + tests for the new D-08 knob validation
└── test_recorder_hot_reload.py  # NEW: diff/add/remove/swap/failure-skip/threshold-warn tests
```

### Pattern 1: Periodic config-reload timer (D-01/D-02)
**What:** Register a dedicated named timer in `on_start` alongside the existing two, reusing the heartbeat cadence.
**When to use:** Always — this is the reload trigger.
**Why a separate named timer (vs. piggybacking heartbeat):** testability (`assert "config-reload" in clock.timer_names`), independent log identity, and clean separation of concerns. The cadence is shared (D-02) but the callback is distinct.
```python
# scripts/bybit_recorder/strategy.py — on_start, after the heartbeat timer
self.clock.set_timer(
    name="config-reload",
    interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),  # D-02: reuse cadence
    callback=self._on_config_reload,
)
```
Source: existing `convert-stream`/`heartbeat` registrations in `strategy.py` (verified).

### Pattern 2: Bookkeeping structures (Claude's Discretion)
**What:** Track the running param-set and the per-snapshot failed ids, parallel to `_last_seen`.
```python
# __init__
# What params each instrument is currently subscribed at (drives swap detection).
self._subscribed_params: dict[InstrumentId, tuple[int, frozenset[str]]] = {}
#                                              ^depth   ^bar_intervals
# Instrument ids that failed to load/validate for the CURRENT toml snapshot (D-07/D-11).
self._failed_instrument_ids: set[InstrumentId] = set()
# Hash/signature of the last successfully-applied toml so failed-set resets only when toml changes.
self._last_config_signature: int | None = None
# Count of instruments hot-added over the process lifetime (D-08 threshold).
self._hot_added_count: int = 0
```
**Key:** `_failed_instrument_ids` must be cleared whenever the parsed config changes (D-07: "Only re-attempt if `recorder.toml` changes again"). Compute a stable signature of the parsed instrument entries (e.g. `hash(tuple(sorted(...)))`) and reset the failed-set on change. Seed `_subscribed_params` from `self.config` in `on_start` so the first reload diffs against the real startup state.

### Pattern 3: Runtime instrument load for Bybit (the D-09 fallback — REQUIRED)
**What:** Because `request_instrument()` is a no-op-with-error for Bybit (Pitfall 1), inject a reference to the Bybit data client into the strategy at build time and load via its provider.
**When to use:** Only on the ADDITION branch, for an id not yet in `self.cache`.
**Wiring (in `recorder.py`, after `node.build()`):**
```python
# scripts/bybit_recorder/recorder.py — AFTER node.build()
# WHY: BybitDataClient does not implement _request_instrument, so Actor.request_instrument()
# cannot load a runtime-added instrument (verified: nautilus_trader/live/data_client.py:1099
# raises NotImplementedError; BybitDataClient does not override it). Hand the strategy a
# reference to the live data client so it can call provider.load_async() directly.
data_client = node.kernel.data_engine.get_client(ClientId(BYBIT))  # confirm exact accessor at plan time
strategy.set_data_client(data_client)  # NEW setter on RecorderStrategy, stores self._bybit_client
```
**Load (in the strategy ADDITION branch, async-safe):**
```python
# Strategy side — load then populate caches before subscribing (D-10)
provider = self._bybit_client.instrument_provider
# load_async fetches the single symbol from Bybit and provider.add()s it (Bybit override).
# It does NOT raise on an unknown symbol — it simply adds nothing, so verify via find().
await provider.load_async(instrument_id)
instrument = provider.find(instrument_id)
if instrument is None:
    # D-11: Bybit doesn't recognize it -> same as D-07 (log error, skip, mark failed)
    ...
else:
    self.cache.add_instrument(instrument)        # so Actor/on_start-style cache check passes (D-10)
    self._bybit_client._cache_instruments()      # repopulate WS-client precision cache for parsing
    # then subscribe the same feeds as on_start
```
**Note on async:** the reload timer callback is synchronous. `provider.load_async` is a coroutine. The Bybit client runs on the live event loop; the cleanest invocation is to schedule the load on that loop (e.g. `data_client.create_task(...)` or `asyncio.run_coroutine_threadsafe`-style) and perform `cache.add_instrument`+subscribe in the load's completion, OR use the synchronous `provider.load(instrument_id)` wrapper (`providers.py:244`) which schedules a task on the running loop. **Plan must pin the exact invocation against the live loop and cover it in the human-verify smoke test** — this is the riskiest seam.
Source: `nautilus_trader/common/providers.py:127` (`load_async`), `:194` (Bybit override), `nautilus_trader/adapters/bybit/data.py:239` (`_cache_instruments`), `:182` (`instrument_provider` property). [VERIFIED: fork source]

### Anti-Patterns to Avoid
- **Calling `subscribe_*` for a new instrument before it is in `self.cache`** — violates D-10; for an unknown symbol the data would arrive but fail precision-parsing in the WS client. Always load+cache first.
- **Subscribing the new depth before unsubscribing the old depth (param swap)** — the DataEngine dedups order-book subscriptions by `instrument_id` only, so the new-depth subscribe is silently dropped if the old subscription is still active. ALWAYS unsubscribe-then-subscribe. (Pitfall 2.)
- **Iterating the full instrument list for mark/index/funding hot-adds** — these are linear-only (D-04); spot hot-adds must skip them, mirroring `on_start`'s `linear_instrument_ids` gating.
- **Re-attempting a failed load every poll cycle** — DoS-of-logs / API hammering. Honor D-07: track failed ids per snapshot.
- **Editing the Bybit adapter to add `_request_instrument`** — forbidden by CLAUDE.md (core is untouchable). Use the composition wiring in Pattern 3.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Fetch a Bybit instrument definition | Custom HTTP call to Bybit `/v5/market/instruments-info` | `BybitInstrumentProvider.load_async(id)` | Provider already parses pyo3 → Nautilus `Instrument`, handles product-type routing, precision |
| Stop/start a WS feed | Raw WS subscribe/unsubscribe frames | `Actor.subscribe_*` / `unsubscribe_*` | Routed via msgbus → adapter, which manages ticker ref-counting and channel names |
| Per-instrument depth channel mgmt | Track Bybit `orderbook.{depth}.{sym}` channels yourself | `unsubscribe_order_book_deltas` + `subscribe_order_book_deltas(depth=...)` | Adapter stores `_depths[id]` and unsubscribes the correct old channel |
| TOML parse/validate | New parser | `load_recorder_config(path)` (re-callable) | Already does fail-fast `> 0` + depth-set validation; reuse verbatim |
| Stale-stream tracking for hot instruments | New tracking dict | Existing `_last_seen` | D-04 mandates hot instruments join the same machinery |

**Key insight:** Every primitive this phase needs already exists and is implemented for Bybit. The ONLY genuinely new logic is (a) the diff, (b) the bookkeeping, and (c) the small composition wiring to reach the provider for runtime loads. Resist building anything that touches Bybit's wire protocol.

## Runtime State Inventory

> This is a feature-add phase, not a rename/refactor. The "runtime state" relevant here is the live in-memory subscription state the reload must mutate — documented for completeness.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None — the catalog/feather files are append-only and untouched by reload (D-06 leaves recorded data intact). | None |
| Live service config | `recorder.toml` is the live config being diffed. Bybit WS subscription state lives in the adapter (`_depths`, `_ticker_subscriptions`, `_quote_depths`) — mutated indirectly via subscribe/unsubscribe commands, never directly. | Code: issue subscribe/unsubscribe through the Actor API only |
| OS-registered state | None (systemd unit unchanged; no new tasks/timers at OS level — the reload timer is in-process via `clock.set_timer`). | None |
| Secrets/env vars | None — no new secrets; Bybit API keys already loaded at node build. | None |
| Build artifacts | None — Python-only, no compiled artifacts; no `pyproject.toml`/package rename. | None |
| In-memory strategy state | `_last_seen` (must gain hot-added entries, lose removed ones — D-04/D-06); NEW `_subscribed_params`, `_failed_instrument_ids`, `_hot_added_count`. | Code: maintain in the reload path |
| Adapter instrument caches | Bybit provider `_instruments`, HTTP/WS client instrument caches — a runtime-added instrument must be inserted into ALL of these (`load_async` handles the provider; `_cache_instruments()` handles HTTP+WS clients). | Code: call both on hot-add (Pattern 3) |

**Nothing found in OS-registered / secrets / build-artifacts categories — verified by reading `recorder.py` (no new files/services), CLAUDE.md (Python-only), and the systemd scope (unchanged).**

## Common Pitfalls

### Pitfall 1: `Actor.request_instrument()` is non-functional for Bybit (overturns D-09 preferred path)
**What goes wrong:** Planning to load new instruments via `self.request_instrument(id)` → `on_instrument` callback (the D-09 *preferred* mechanism). For Bybit, the callback NEVER fires; the instrument never enters the cache; with the D-10 "wait until cached" guard, every hot-added instrument is silently skipped forever (collapses into D-11's "skip + don't retry").
**Why it happens:** `Actor.request_instrument` (`nautilus_trader/common/actor.pyx:3113`) builds a `RequestInstrument`, routes it through `DataEngine._handle_request_instrument` (`engine.pyx:2017`) → `client.request_instrument(request)`. The Bybit `LiveMarketDataClient.request_instrument` schedules `self._request_instrument(request)` as a task (`data_client.py:830`), but `_request_instrument` is **not overridden by `BybitDataClient`** and the base raises `NotImplementedError` (`data_client.py:1099`). `create_task` catches and logs the exception (`data_client.py:211` `self._log.exception(...)`) — so it's a silent (log-only) failure, not a crash.
**How to avoid:** Use the Pattern 3 provider fallback (`provider.load_async(id)` + `cache.add_instrument` + `_cache_instruments`). Keep D-10's "confirm in cache before subscribing" guard — but the confirmation must check after the *provider load*, not after an `on_instrument` callback.
**Warning signs:** A new instrument added to `recorder.toml` is logged as failed/skipped on every machine, and journald shows `Error on 'request: instrument ...'` with a `NotImplementedError` traceback.
[VERIFIED: fork source — `data_client.py:830,1099`; `actor.pyx:3113`; `engine.pyx:2017`; Bybit `data.py` has no `_request_instrument` override (grep returned nothing).]

### Pitfall 2: Depth-change clean-swap dropped because DataEngine dedups by instrument_id only
**What goes wrong:** On a depth change you call `subscribe_order_book_deltas(id, depth=new)` first (or without unsubscribing): the new subscription is silently dropped and the feed stays at the OLD depth.
**Why it happens:** `DataEngine._handle_subscribe_order_book` (`engine.pyx:1029`) forwards to the client ONLY `if not client.is_subscribed_order_book_deltas(command.instrument_id)` — tracked by `instrument_id`, not by depth. An already-subscribed instrument's new-depth subscribe is a no-op at the engine.
**How to avoid:** ALWAYS `unsubscribe_order_book_deltas(id)` FIRST (which clears the subscription flag and unsubscribes the old Bybit channel using the adapter-stored `_depths[id]`), THEN `subscribe_order_book_deltas(id, book_type=BookType.L2_MBP, depth=new)`. Order is load-bearing.
**Warning signs:** After editing depth in the toml, order-book deltas keep arriving at the old granularity; no new-depth WS subscribe frame in adapter debug logs.
[VERIFIED: fork source — `engine.pyx:1021-1035`; Bybit `data.py:471-481` (`_unsubscribe_order_book_deltas` uses `_depths` then pops).]

### Pitfall 3: Bybit `load_ids_async` overwrites the provider's pyo3 instrument list (do NOT use it for incremental load)
**What goes wrong:** Using `provider.load_ids_async([new_id])` (or `load_ids`) to add ONE instrument replaces `provider._instruments_pyo3` with only that symbol (`providers.py:186` `self._instruments_pyo3 = all_pyo3_instruments`). A subsequent `_cache_instruments()` (which iterates `instruments_pyo3()`) would then only re-cache the single new symbol — though existing `_instruments` dict entries survive via `add()`. Subtle inconsistency.
**Why it happens:** Bybit's `load_ids_async` is written for the startup bulk-load, not incremental add; it reassigns the pyo3 list rather than appending.
**How to avoid:** Prefer `load_async(instrument_id)` (`providers.py:194` Bybit override → delegates to `load_ids_async([id])`). This still reassigns `_instruments_pyo3`, so when re-caching for the WS client, prefer caching the single newly-loaded pyo3 instrument explicitly rather than relying on a full `_cache_instruments()` sweep, OR accept that `_cache_instruments()` re-caches only the new one (the WS/HTTP clients keep previously-cached instruments — `cache_instrument` is additive on the Rust side). Confirm the additive behavior in the human-verify smoke (existing instruments must keep parsing after a hot-add).
**Warning signs:** After a hot-add, a previously-running instrument's deltas/trades stop parsing (precision lookup miss) — would indicate the WS cache was clobbered.
[VERIFIED: fork source — `nautilus_trader/adapters/bybit/providers.py:138-196`; `data.py:239-250,256` (`_cache_instruments` iterates `instruments_pyo3()`).] [ASSUMED: that the Rust-side `cache_instrument` is additive and does not evict prior instruments — must be confirmed in smoke test, see Assumptions Log A1.]

### Pitfall 4: Removing an instrument must also remove ALL its `_last_seen` keys
**What goes wrong:** After a removal you unsubscribe the feeds but leave `_last_seen[("trade", id)]` etc. in place; `_heartbeat` then perpetually WARNs "stale stream" for an instrument that was intentionally removed.
**Why it happens:** `_last_seen` is keyed by `(stream_label, instrument_id)`; nothing auto-prunes it.
**How to avoid:** On removal, delete every `(_, id)` entry from `_last_seen` for that instrument (all of `trade/quote/deltas/bar/mark/index/funding`). D-06 explicitly requires this.
**Warning signs:** journald fills with stale-stream WARNINGs for a symbol no longer in `recorder.toml`.
[VERIFIED: fork source — `strategy.py:289-313` `_heartbeat` iterates `_last_seen`; `:144` key shape.]

### Pitfall 5: Linear-only feeds on hot-add/remove must mirror `on_start`'s gating
**What goes wrong:** Subscribing `mark/index/funding` for a hot-added SPOT instrument, or unsubscribing them for a removed spot instrument that never had them.
**Why it happens:** The Bybit adapter merely warns-and-returns for spot mark/index/funding (`data.py:377,398,420`), so it's not fatal, but it pollutes logs and muddies bookkeeping.
**How to avoid:** Determine the new/removed instrument's product type from its parsed `InstrumentEntry.product_type` and only touch mark/index/funding for `"linear"`, exactly like `on_start` iterates `linear_instrument_ids`.
[VERIFIED: fork source — `strategy.py:195-198` linear gating; `data.py:377-432` spot warn-return.]

## Code Examples

Verified primitives (all inherited by `RecorderStrategy` from `Strategy`/`Actor`, all implemented in the Bybit data client).

### Subscribe feeds for a hot-added instrument (mirror on_start exactly)
```python
# Source: scripts/bybit_recorder/strategy.py on_start (lines 177-198) — reuse verbatim per-instrument
def _subscribe_instrument(self, instrument_id: InstrumentId, depth: int,
                          bar_intervals: list[str], is_linear: bool) -> None:
    self.subscribe_trade_ticks(instrument_id)
    self.subscribe_quote_ticks(instrument_id)
    self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=depth)
    for interval in bar_intervals:
        self.subscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
    if is_linear:  # D-04 gating
        self.subscribe_mark_prices(instrument_id)
        self.subscribe_index_prices(instrument_id)
        self.subscribe_funding_rates(instrument_id)
```

### Unsubscribe ALL feeds for a removed instrument (D-06)
```python
# Source: verified unsubscribe_* signatures in nautilus_trader/common/actor.pyx
#   unsubscribe_trade_ticks(instrument_id)              :2505
#   unsubscribe_quote_ticks(instrument_id)              :2458
#   unsubscribe_order_book_deltas(instrument_id)        :2324  (NO depth param)
#   unsubscribe_bars(bar_type)                          :2673  (full BarType)
#   unsubscribe_mark_prices(instrument_id)              :2547
#   unsubscribe_index_prices(instrument_id)             :2589
#   unsubscribe_funding_rates(instrument_id)            :2631
def _unsubscribe_instrument(self, instrument_id, depth, bar_intervals, is_linear):
    self.unsubscribe_trade_ticks(instrument_id)
    self.unsubscribe_quote_ticks(instrument_id)
    self.unsubscribe_order_book_deltas(instrument_id)
    for interval in bar_intervals:
        self.unsubscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
    if is_linear:
        self.unsubscribe_mark_prices(instrument_id)
        self.unsubscribe_index_prices(instrument_id)
        self.unsubscribe_funding_rates(instrument_id)
    # D-06: prune _last_seen
    for stream in ("trade", "quote", "deltas", "bar", "mark", "index", "funding"):
        self._last_seen.pop((stream, instrument_id), None)
```

### Depth clean-swap (unsubscribe-then-subscribe; order is load-bearing — Pitfall 2)
```python
self.unsubscribe_order_book_deltas(instrument_id)                 # clears engine flag + old Bybit channel
self.subscribe_order_book_deltas(instrument_id, book_type=BookType.L2_MBP, depth=new_depth)
```

### Bar-interval delta swap (only add/remove the changed intervals — D-05)
```python
added   = set(new_intervals) - set(old_intervals)
removed = set(old_intervals) - set(new_intervals)
for interval in removed:
    self.unsubscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
for interval in added:
    self.subscribe_bars(BarType.from_str(f"{instrument_id}-{interval}-LAST-EXTERNAL"))
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `request_instrument` assumed universal (D-09) | Provider `load_async` fallback for Bybit | This research | D-09 preferred path is non-functional for Bybit; planner must adopt Pattern 3 |
| Static `InstrumentProviderConfig(load_ids=...)` set once at build | Runtime per-id load via provider reference | This phase | Works around the fixed startup load list (the core constraint named in CONTEXT `<specifics>`) |

**Deprecated/outdated:** None relevant. (Bybit `update_instruments_interval_mins` exists but only reloads the originally-configured ids — not a hot-add mechanism.)

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Rust-side `ws_client.cache_instrument(inst)` / `http_client.cache_instrument(inst)` is ADDITIVE — caching a newly-loaded instrument does not evict previously-cached ones. | Pitfall 3, Pattern 3 | If it clobbers, existing instruments' deltas/trades stop parsing after any hot-add — high impact; MUST be confirmed in the human-verify live smoke before relying on a single-instrument `cache_instrument` call. |
| A2 | The exact accessor to get the live Bybit data client from the built node (`node.kernel.data_engine.get_client(ClientId(BYBIT))` or equivalent) exists and returns the `BybitDataClient` instance. | Pattern 3 wiring | If the accessor differs, the injection wiring changes — low risk (the registry is `engine.pyx:208 self._clients`), but the public path must be pinned at plan time. |
| A3 | Scheduling `provider.load_async` onto the running live event loop from the synchronous `clock` timer callback (via `provider.load(id)` sync wrapper or `data_client.create_task`) executes correctly without blocking the single-threaded strategy loop. | Pattern 3 (async note) | If the load blocks or races the subscribe, new-instrument data may be missed on first poll — covered by the human-verify smoke. |
| A4 | Default `max_hot_added_instruments` of 50 is "sensible" for this collector. | Open Question 1 | Purely informational WARNING (D-08 never blocks); wrong default only changes when an info-WARNING fires. Low risk. |

## Open Questions

1. **Default value & key name for the D-08 instrument-count WARNING threshold.**
   - What we know: must be a new `recorder.toml` key, validated `> 0` like siblings, default chosen by Claude, WARNING-only (never blocks).
   - What's unclear: a "right" number is operator-dependent.
   - Recommendation: key `max_hot_added_instruments`, default `50`. Rationale: a single-process collector multiplexing all subscriptions over one WS connection; tens of hot-adds over a lifetime is normal, hundreds suggests config thrash worth flagging. Add the field to `RecorderStrategyConfig` (`PositiveInt = 50`) and `RecorderConfig`, validated in `load_recorder_config` with the existing `<= 0` raise pattern, and threaded through `recorder.py`.

2. **Exact event-loop invocation for the runtime provider load (sync timer → async load).**
   - What we know: timer callback is sync; `load_async` is a coroutine; the Bybit client owns the live loop; `providers.py:244 load()` is a sync wrapper that schedules a task on the running loop.
   - What's unclear: whether to use `provider.load(id)` (fire-and-forget task; subscribe must then happen in a follow-up poll once `find(id)` is non-None) or `data_client.create_task(...)` with an explicit completion that does cache.add + subscribe.
   - Recommendation: use the two-phase approach — poll N schedules the load; poll N+1 (or a completion callback) sees the instrument in `provider.find(id)`/`cache`, then subscribes (naturally satisfies D-10's "wait until confirmed"). Pin the exact call in the plan and verify in smoke.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | All recorder code | ✓ | 3.12+ | — |
| `nautilus_trader` fork (built) | Strategy/adapter API | ✓ | local build | — |
| `pytest` + `pytest-mock` | Unit tests | ✓ | 7.4.4 / >=3.15.1 | — |
| Live Bybit mainnet WS | Human-verify hot-add smoke (Pattern 3) | ✓ (used in prior phases' smokes) | — | Unit tests cover diff/sub/unsub logic with mocks; the runtime-load wiring CANNOT be fully unit-tested and needs a live smoke |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** Live Bybit connectivity for the runtime-load smoke is the only path not unit-testable; mocked unit tests cover everything else.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 (+ pytest-mock, pytest-asyncio 0.23.8) |
| Config file | `pyproject.toml` (repo root) |
| Quick run command | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -x` |
| Full suite command | `pytest tests/unit_tests/persistence/recorder/ -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| HOT-01 | reload timer registered in on_start | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -k timer_registered -x` | ❌ Wave 0 |
| HOT-01 | diff detects an addition | unit | `... -k detects_addition -x` | ❌ Wave 0 |
| HOT-01 | addition subscribes all feeds after cache-confirm (D-10) | unit | `... -k addition_subscribes -x` | ❌ Wave 0 |
| HOT-01 | removal unsubscribes all feeds + prunes _last_seen (D-06) | unit | `... -k removal_unsubscribes -x` | ❌ Wave 0 |
| HOT-01 | depth change = unsubscribe-then-subscribe in order (D-05/Pitfall 2) | unit | `... -k depth_swap_order -x` | ❌ Wave 0 |
| HOT-01 | bar-interval delta only touches changed intervals (D-05) | unit | `... -k bar_interval_delta -x` | ❌ Wave 0 |
| HOT-01 | invalid/unknown new instrument skipped + not retried (D-07/D-11) | unit | `... -k failed_not_retried -x` | ❌ Wave 0 |
| HOT-01 | failed-set resets when toml changes (D-07) | unit | `... -k failed_resets_on_change -x` | ❌ Wave 0 |
| HOT-01 | hot-add count over threshold logs WARNING (D-08) | unit | `... -k threshold_warning -x` | ❌ Wave 0 |
| HOT-01 | linear-only gating for mark/index/funding on hot-add/remove (D-04/Pitfall 5) | unit | `... -k linear_gating -x` | ❌ Wave 0 |
| HOT-01 | new D-08 knob validated `> 0` | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_config.py -k max_hot_added -x` | ❌ Wave 0 (extend existing file) |
| HOT-01 | end-to-end live hot-add against Bybit mainnet | manual smoke | human-verify checkpoint | N/A (live) |

### Sampling Rate
- **Per task commit:** `pytest tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py -x`
- **Per wave merge:** `pytest tests/unit_tests/persistence/recorder/ -q`
- **Phase gate:** full recorder suite green + the live hot-add smoke (human-verify) before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py` — covers HOT-01 diff/add/remove/swap/failure/threshold/gating
- [ ] Extend `tests/unit_tests/persistence/recorder/test_recorder_config.py` — `max_hot_added_instruments` validation
- [ ] Reuse existing `conftest.py` fixtures (`mock_cache`, `sample_toml`, `_build_strategy` helper) — extend `_build_strategy` to inject a mock data-client reference for Pattern 3 tests
- [ ] Framework install: none — pytest stack already present

## Security Domain

> `security_enforcement: true`, ASVS level 1. This is a backend live-reload of a local config file consumed by a single-process collector — no auth/session/network-input surfaces are added.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No auth surface added; Bybit API keys already loaded at build |
| V3 Session Management | no | No sessions |
| V4 Access Control | no | Single local process; `recorder.toml` access governed by filesystem perms (systemd `WorkingDirectory`) |
| V5 Input Validation | yes | Re-parsed `recorder.toml` re-runs the existing fail-fast validation (`InstrumentId.from_str`, depth-set, `> 0` thresholds, new `max_hot_added_instruments`). New ids are validated BEFORE any load/subscribe (D-07). |
| V6 Cryptography | no | None |
| V7 Errors/Logging | yes | Log instrument COUNT and ids only — never credentials (existing `config.py:302` precedent). Failed loads logged once per snapshot (D-07) to avoid log-flooding DoS. |

### Known Threat Patterns for {Python live config-reload}
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/hostile `recorder.toml` triggers crash mid-run | Denial of Service | Per-reload parse wrapped so a parse/validation error is logged and the reload is skipped (recorder keeps running on the last-good config); never let a bad reload fault the strategy component (mirror the `_run_conversion` swallow pattern). |
| Repeated failed-load re-attempts hammer Bybit API / flood logs | Denial of Service | D-07 failed-id tracking: attempt each id once per toml snapshot. |
| Unbounded hot-adds exhaust connection/subscription budget | Denial of Service | D-08 `max_hot_added_instruments` WARNING surfaces runaway config thrash (informational; operator-actionable). |
| TOCTOU on the toml file (read while operator mid-edit) | Tampering | `tomllib.load` reads the whole file atomically per poll; a half-written file raises a parse error → caught → reload skipped → retried next poll. |

## Sources

### Primary (HIGH confidence — fork source, read directly this session)
- `nautilus_trader/common/actor.pyx` — `request_instrument`:3113, `request_instruments`:3222, `handle_instrument`:4394, `_handle_instruments_response`:4947, all `subscribe_*`/`unsubscribe_*` signatures (1722, 2324, 2458, 2505, 2547, 2589, 2631, 2673), `subscribe_order_book_deltas` depth param:1450
- `nautilus_trader/live/data_client.py` — `request_instrument`:830, `_request_instrument` NotImplementedError:1099, `create_task` error swallow:211
- `nautilus_trader/data/engine.pyx` — `_handle_request_instrument`:2017, order-book subscribe dedup-by-id:1029, `_handle_instrument`→`cache.add_instrument`:2573/2588, client registry:208
- `nautilus_trader/common/providers.py` — `load_ids_async`:88, `load_async`:127, `initialize(reload)`:152, sync `load`:244, `add`:286, `find`:376
- `nautilus_trader/adapters/bybit/providers.py` — `load_all_async`:117, `load_ids_async`:138 (reassigns `_instruments_pyo3`:186), `load_async`:194
- `nautilus_trader/adapters/bybit/data.py` — `instrument_provider`:182, `_cache_instruments`:239, `_send_all_instruments_to_data_engine`:252, `_update_instruments`:273, all `_subscribe_*`/`_unsubscribe_*` (322-569), ticker ref-counting, depth tracking (`_depths` use in unsubscribe:471)
- `scripts/bybit_recorder/strategy.py`, `config.py`, `recorder.py` — current patterns (on_start subscribe loop, `_last_seen`, timers, fail-fast validation, `InstrumentProviderConfig(load_ids=...)` one-time set)
- `tests/unit_tests/persistence/recorder/conftest.py` + `test_recorder_strategy.py` — test fixtures, `_build_strategy`, mock-subscribe pattern
- `docs/concepts/adapters.md:139`, `docs/concepts/actors.md:190`, `docs/integrations/{ib,derive,polymarket}.md` — confirm `request_instrument` is real but adapter-dependent (implemented for IB/Binance/Derive/Polymarket, NOT Bybit)

### Secondary (MEDIUM confidence)
- (none required — all claims grounded in fork source)

### Tertiary (LOW confidence)
- (none)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new packages; all primitives exist & verified in fork source.
- Architecture (diff/sub/unsub, removals, param-swaps): HIGH — every method read and confirmed implemented for Bybit.
- Runtime instrument load (D-09): HIGH on the *negative* finding (request_instrument unusable for Bybit — directly verified) and the *recommended fallback shape*; MEDIUM on the exact async-loop invocation and additive-cache behavior (A1/A2/A3 — flagged for human-verify smoke).
- Pitfalls: HIGH — each traced to a specific source line.

**Research date:** 2026-06-15
**Valid until:** 2026-07-15 (stable fork; the Bybit adapter's lack of `_request_instrument` is the only thing that could change with an upstream merge — re-grep `nautilus_trader/adapters/bybit/data.py` for `_request_instrument` before relying on D-09 if the fork is updated).
