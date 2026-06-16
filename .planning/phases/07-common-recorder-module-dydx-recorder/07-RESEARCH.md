# Phase 7: Common Recorder Module + dYdX Recorder - Research

**Researched:** 2026-06-16
**Domain:** Python recorder refactor (extract shared infra) + dYdX v4 perpetuals data recorder wiring on NautilusTrader
**Confidence:** HIGH (every claim traced to source read in this session: `scripts/bybit_recorder/*.py`, `nautilus_trader/adapters/dydx/*.py`, `crates/adapters/dydx/src/common/enums.rs`, runtime checks)

## Summary

Phase 7 is two coupled tasks. First, a **pure refactor**: extract the exchange-agnostic recorder machinery currently living entirely inside `scripts/bybit_recorder/strategy.py` + `config.py` into a new `scripts/common_recorder/` module, then re-point `scripts/bybit_recorder/` at it with zero behavior change (verified by the existing 61-test recorder suite staying green). Second, a **wiring + config task**: build `scripts/dydx_recorder/` that reuses the common module and swaps the Bybit factory/config/enum for the dYdX equivalents to record all 7 perpetual data types. Prior stack/feature research (`.planning/research/STACK.md`, `FEATURES.md`) already confirmed: **no new packages, no Rust changes** — the dYdX adapter is bundled in this fork and is data-path complete.

The single most important architectural finding: **the existing `RecorderStrategy` is ~95% exchange-agnostic.** It never imports `bybit` anywhere in `strategy.py` (verified — the only adapter coupling is the `set_data_client(client)` reference used by the hot-reload ADD branch, which reaches the base-class `instrument_provider.load/find` API that `DydxInstrumentProvider` also inherits). The Bybit-specific surface is confined to: (a) `config.py`'s linear/spot product-type split and `_LINEAR_VALID_DEPTHS`/`_SPOT_MAX_DEPTH` tables, (b) `recorder.py`'s factory/enum/`CUSTOM_ENCODINGS` wiring, and (c) the hardcoded `LAST-EXTERNAL` bar-type suffix and the linear-only gating of mark/index/funding. The hot-reload logic is **embedded in `strategy.py`** (there is no separate `hot_reload.py` file) and is itself venue-agnostic.

**Primary recommendation:** Extract `RecorderStrategy` + `RecorderStrategyConfig` into `scripts/common_recorder/strategy.py` essentially verbatim, and the path-resolution + streaming-config helpers + threshold validators into `scripts/common_recorder/config.py`. Keep venue-specific config parsing (instrument-list shape, depth tables, bar-interval whitelist) in each recorder's own `config.py`. The dYdX recorder subclasses nothing for the strategy — it reuses `RecorderStrategy` as-is and only writes a thin venue config module + a `recorder.py` wiring file. Use **composition/reuse, not inheritance**, for the strategy. This minimizes change to the proven Bybit recorder (it keeps its own `config.py` parsing, just imports shared helpers).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Catalog conversion (feather→parquet) | common_recorder (strategy) | — | Pure Nautilus catalog mechanics; no venue knowledge (`_run_conversion`, `_convert_finalized_feather_files`) |
| Heartbeat / stale-stream detection | common_recorder (strategy) | — | Keyed by `(stream_label, instrument_id)`; venue-agnostic |
| Graceful SIGTERM flush+convert | common_recorder (strategy) | — | `on_stop` → `_run_conversion`; no venue knowledge |
| Funding dedup | common_recorder (strategy) | — | Keys on `(instrument_id, rate)` only; `FundingRateUpdate.rate` is a core type field |
| Hot-reload diff/apply | common_recorder (strategy) | venue instrument provider | Diff engine is venue-agnostic; ADD branch reaches `instrument_provider.load/find` (base-class API both adapters inherit) |
| Restart-gap logging | common_recorder (strategy) | — | Reads `catalog.trade_ticks`; venue-agnostic |
| Path resolution + StreamingConfig build | common_recorder (config) | — | `_resolve_catalog_path`, `build_streaming_config`, threshold validators |
| Instrument-list TOML parsing | venue recorder (config) | — | Bybit has linear/spot split + depth tables; dYdX has flat perp list + interval whitelist |
| Factory / enum / CUSTOM_ENCODINGS wiring | venue recorder (recorder.py) | — | `BybitLiveDataClientFactory`+`BybitEnvironment`/`BybitProductType` vs `DydxLiveDataClientFactory`+`DydxNetwork` |
| Bar-type subscription string | common (parameterized) | venue recorder | `LAST-EXTERNAL` suffix is currently hardcoded; both venues use venue-native EXTERNAL klines so it can stay shared |

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| DYDX-01 | Extract exchange-agnostic infra into `scripts/common_recorder/`; Bybit imports it with no behavior change | `strategy.py` is venue-agnostic (no `bybit` import); extraction boundary mapped below. Existing 61-test suite (`tests/unit_tests/persistence/recorder/`) is the no-behavior-change gate. |
| DYDX-02 | `scripts/dydx_recorder/` using `DydxDataClientConfig`+`DydxLiveDataClientFactory`+`DydxNetwork` (CUSTOM_ENCODINGS), TOML instrument list, `InstrumentProviderConfig(load_ids=...)` | Factory wiring confirmed (`factories.py:97-153`); `recorder.py` Bybit pattern at lines 49-50,67-123 is the mirror. `DydxNetwork.MAINNET.name=='mainnet'` (runtime-verified). |
| DYDX-03 | Subscribe/record trades, synthesized quotes, L2 deltas, bars, funding, mark, index | All 7 subscribe methods confirmed in `data.py:376-487`. Same Nautilus data types already in `include_types`. |
| DYDX-04 | Funding dedup (adapter does not dedup) reusing Bybit mechanism, verified `(instrument_id, rate)` + exchange-agnostic | `on_funding_rate`/`_persist_funding_rate` keys on `(instrument_id, rate)`; `FundingRateUpdate.rate` is a core field (verified). Directly reusable. |
| DYDX-05 | Relaxed heartbeat for synthesized quotes (event-driven; quiet markets) | Per-type `stale_threshold_seconds` dict already exists. dYdX quotes fire only on top-of-book change (`data.py:342-351`). Recommend relaxed quote threshold + monitor `deltas` stream. |
| DYDX-06 | Validate bar intervals against dYdX resolution set; fail fast at config load | Supported set confirmed in `enums.rs:679-734`: 1/5/15/30-MINUTE, 1/4-HOUR, 1-DAY. New `_DYDX_VALID_INTERVALS` whitelist in dYdX config loader. |
| DYDX-07 | 24/7 reliability parity: SIGTERM flush+convert, adapter reconnect, stale heartbeats, restart-gap logging | All four already implemented in the (now-shared) strategy. Reused as-is. Hot-reload is also reusable but is HOT-01-scoped, not a DYDX-07 requirement (see Q8). |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `nautilus_trader.adapters.dydx` | bundled (`nautilus_trader==1.229.0`) | dYdX v4 data client/factory/config/provider | Already in this fork; data path validated. NEVER modify (CLAUDE.md). [VERIFIED: source read `factories.py`, `data.py`, `config.py`] |
| `DydxDataClientConfig` | same | Python config for dYdX data client | Drop-in for `BybitDataClientConfig` (`config.py:28-67`). [VERIFIED: source] |
| `DydxLiveDataClientFactory` | same | Builds `DydxDataClient` | `node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)` mirrors Bybit (`factories.py:97`). [VERIFIED: source] |
| `DydxNetwork` (pyo3 enum) | same | `MAINNET`/`TESTNET` selection | Replaces `BybitEnvironment`. `.name` is **lowercase** (`'mainnet'`/`'testnet'`). [VERIFIED: runtime executed this session] |
| `DYDX` venue constant | same | `nautilus_trader.adapters.dydx.DYDX` | Client-id key, mirrors `BYBIT`. [CITED: STACK.md `constants.py:23-24`] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `DydxInstrumentProvider` | bundled | Loads dYdX instruments from Indexer API | Wired automatically by factory; extends base `InstrumentProvider` so inherits `load`/`find` (verified `providers.py:30`, `common/providers.py:244,376`) — the same API the hot-reload ADD branch uses. |
| `InstrumentProviderConfig` | core | `load_ids=frozenset(instrument_ids)` | Identical to Bybit usage. No change. |
| `CUSTOM_ENCODINGS` | `nautilus_trader.common.config` | JSON encoder for `DydxNetwork` pyo3 enum | REQUIRED — streaming serializes `TradingNodeConfig.json()`; pyo3 enums have no default encoder. `CUSTOM_ENCODINGS[DydxNetwork] = lambda v: v.name`. [VERIFIED: matches Bybit pattern `recorder.py:49-50`] |
| `StreamingConfig` / `StreamingFeatherWriter` | core | Persistence path | Unchanged from Bybit milestone; same `include_types`. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Reuse `RecorderStrategy` as-is (composition) | Subclass a `CommonRecorderStrategy` base in dydx_recorder | Subclassing adds zero value — the strategy has no venue-specific methods to override. Composition (import + instantiate) is simpler and changes the Bybit recorder less. **Recommended: reuse, no subclass.** |
| Shared `config.py` helpers + per-venue parsers | One mega config module parsing both venues | Bybit's linear/spot split and dYdX's flat-perp+whitelist differ enough that a single parser becomes a conditional mess. Keep parsers separate; share only helpers. |
| Pass network as plain string | `CUSTOM_ENCODINGS[DydxNetwork]` | The factory needs the enum on the config object AND streaming serializes it — encoder registration is unavoidable. [CITED: STACK.md] |

**Installation:**
```bash
# Nothing to install. Verify the adapter is importable:
python -c "from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory; \
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig; \
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork; \
print('dydx OK', DydxNetwork.MAINNET.name, DydxNetwork.TESTNET.name)"
# Expect: dydx OK mainnet testnet
```

**Version verification:** No external packages added. `nautilus_trader==1.229.0` already pinned; the dYdX client ships in the same wheel. Verified importable + `DydxNetwork.MAINNET.name == 'mainnet'` at runtime this session.

## Package Legitimacy Audit

> Not applicable — this phase installs **no external packages**. All imports are from the in-repo `nautilus_trader` package (already a dependency) and the Python standard library (`tomllib`, `pathlib`, `logging`, `datetime`). No npm/PyPI/crates additions.

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
                       recorder.toml (dydx)
                              │  parse + validate (interval whitelist, InstrumentId.from_str)
                              ▼
   scripts/dydx_recorder/config.py  ──(shared helpers)──> scripts/common_recorder/config.py
       (flat perp list, _DYDX_VALID_INTERVALS)              (_resolve_catalog_path,
                              │                               build_streaming_config,
                              │                               threshold validators)
                              ▼
   scripts/dydx_recorder/recorder.py  (main)
       CUSTOM_ENCODINGS[DydxNetwork] = lambda v: v.name
       TradingNodeConfig(data_clients={DYDX: DydxDataClientConfig(environment=...)})
       node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)
       node.build();  client = node.kernel.data_engine._clients[ClientId(DYDX)]
                              │  inject client (for hot-reload ADD branch)
                              ▼
   scripts/common_recorder/strategy.py : RecorderStrategy  (reused as-is)
       on_start ─> validate cache ─> _subscribe_instrument(per id):
                     trades, quotes(→implicit book), deltas(L2_MBP),
                     bars(whitelisted), [is_linear]→mark/index/funding
       timers: convert-stream, heartbeat, config-reload
                              │
       live data ── DydxDataClient (WS markets/v4_orderbook/v4_trades/v4_candles)
                              │  Nautilus data types on msgbus "*"
                              ▼
       kernel StreamingFeatherWriter (Trade/Quote/Deltas/Bar/Mark/Index)
       + strategy-owned funding writer (deduped FundingRateUpdate)
                              │  rotated-out feather files
                              ▼  _run_conversion (timer + on_stop)
                  ParquetDataCatalog  (catalog/streaming, day-partitioned)
```

### Recommended Project Structure
```
scripts/
├── common_recorder/          # NEW — shared, exchange-agnostic infra
│   ├── __init__.py
│   ├── strategy.py           # RecorderStrategy + RecorderStrategyConfig (moved from bybit_recorder)
│   └── config.py             # _resolve_catalog_path, build_streaming_config, threshold validators
├── bybit_recorder/           # EXISTING — re-pointed to import from common_recorder
│   ├── config.py             # keeps Bybit linear/spot parsing + depth tables; imports shared helpers
│   ├── recorder.py           # unchanged wiring (Bybit factory/enums)
│   └── recorder.toml
└── dydx_recorder/            # NEW — thin venue wiring
    ├── __init__.py
    ├── config.py             # flat perp parsing + _DYDX_VALID_INTERVALS whitelist; imports shared helpers
    ├── recorder.py           # DydxNetwork CUSTOM_ENCODINGS + factory wiring (mirrors bybit_recorder/recorder.py)
    └── recorder.toml         # no linear/spot split, no depth, environment field, bar_intervals whitelist
```

### Pattern 1: Extract strategy verbatim, re-export for back-compat
**What:** Move `RecorderStrategy` + `RecorderStrategyConfig` into `scripts/common_recorder/strategy.py` unchanged. In `scripts/bybit_recorder/strategy.py`, replace the class bodies with re-exports so existing imports (`from scripts.bybit_recorder.strategy import RecorderStrategy`) and the 61-test suite keep working with no edits.
**When to use:** When the goal is zero behavior change and the moved code has no caller-visible API change.
**Example:**
```python
# scripts/bybit_recorder/strategy.py (after extraction)
# Source: refactor pattern — re-export to preserve existing import paths/tests
from scripts.common_recorder.strategy import RecorderStrategy
from scripts.common_recorder.strategy import RecorderStrategyConfig

__all__ = ["RecorderStrategy", "RecorderStrategyConfig"]
```
**Note:** One coupling to resolve — `common_recorder/strategy.py` currently imports `from scripts.bybit_recorder.config import load_recorder_config` (used by `_on_config_reload`). After extraction this becomes a circular/inverted dependency. Resolution: the strategy must accept a **config-loader callable** (or the parser is passed via config), so the common strategy never imports a venue config module. See Pitfall 1.

### Pattern 2: dYdX recorder.py mirrors bybit_recorder/recorder.py
**What:** Same structure, swap the 3 venue symbols and the enum encoder.
**Example:**
```python
# scripts/dydx_recorder/recorder.py
# Source: mirrors scripts/bybit_recorder/recorder.py:49-50,67-123 (verified pattern)
from nautilus_trader.adapters.dydx import DYDX
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.common.config import CUSTOM_ENCODINGS

# Streaming serializes TradingNodeConfig.json(); pyo3 enum has no default encoder.
# NOTE: DydxNetwork.MAINNET.name == "mainnet" (lowercase) — round-trip accordingly.
CUSTOM_ENCODINGS[DydxNetwork] = lambda value: value.name

# ... in main():
data_clients={
    DYDX: DydxDataClientConfig(
        environment=DydxNetwork.MAINNET,
        instrument_provider=InstrumentProviderConfig(load_ids=frozenset(instrument_ids)),
    ),
},
# ...
node.add_data_client_factory(DYDX, DydxLiveDataClientFactory)
node.build()
data_client = node.kernel.data_engine._clients[ClientId(DYDX)]  # mirrors recorder.py:138
strategy.set_data_client(data_client)
```

### Pattern 3: dYdX bar-interval whitelist in venue config loader
**What:** Validate each configured interval against the supported resolution set before the node starts (DYDX-06 fail-fast), mirroring Bybit's `_LINEAR_VALID_DEPTHS` pattern in `config.py`.
**Example:**
```python
# scripts/dydx_recorder/config.py
# Source: supported set from crates/adapters/dydx/src/common/enums.rs:679-734 (from_bar_spec)
_DYDX_VALID_INTERVALS = {
    "1-MINUTE", "5-MINUTE", "15-MINUTE", "30-MINUTE",
    "1-HOUR", "4-HOUR", "1-DAY",
}
for interval in entry_raw["bar_intervals"]:
    if interval not in _DYDX_VALID_INTERVALS:
        raise ValueError(
            f"Invalid dYdX bar interval {interval!r} for {instrument_id}: "
            f"must be one of {sorted(_DYDX_VALID_INTERVALS)}",
        )
```

### Anti-Patterns to Avoid
- **Subclassing `RecorderStrategy` for dYdX:** No venue-specific behavior to override. Reuse the class directly; only the config/wiring differ.
- **Editing `nautilus_trader/adapters/dydx/`:** Forbidden by CLAUDE.md. All new code under `scripts/`.
- **Recording mark and index as if they diverge:** On dYdX both derive from the same `oracle_price` (`data.rs:1322-1345`) — they are identical. Record both for parity but document the equality. [CITED: FEATURES.md Q4]
- **Carrying the Bybit `depth`/product-type config into dYdX:** dYdX is full-depth L2 with no level cap and no spot. The TOML must not have a `depth` knob or `linear/spot` split. [CITED: FEATURES.md Q1]
- **Common strategy importing a venue config module:** Inverts the dependency. Inject the loader (Pitfall 1).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Quote synthesis from book | Custom top-of-book tracker | dYdX adapter's synthesized quote (`_subscribe_quote_ticks` auto-subscribes book) | Adapter already dedups + handles crossed books (`data.py:394-405`, `data.rs:1547+`) |
| WS reconnect/resubscribe | Custom reconnect loop | dYdX adapter built-in (`max_retries`, `retry_delay_*`) | DYDX-07 parity = adapter-driven, zero custom code (matches REL-04 Bybit decision) |
| Funding dedup | New dedup for dYdX | Existing `on_funding_rate` `(instrument_id, rate)` gate | Already venue-agnostic; `FundingRateUpdate.rate` verified a core field |
| Feather→parquet conversion | Custom writer | `RecorderStrategy._convert_finalized_feather_files` (existing) | Uses Nautilus catalog internals correctly; proven across Bybit phases |
| Bar interval validation | Try/catch at subscribe | Config-load whitelist (`_DYDX_VALID_INTERVALS`) | Fail fast before node start (DYDX-06); same UX as Bybit depth validation |
| Instrument runtime load (hot-add) | Custom loader | `instrument_provider.load/find` (base-class API) | Both adapters inherit it from `common/providers.py`; Bybit ADD branch reuses it verbatim |

**Key insight:** The Bybit milestone already solved every hard problem venue-agnostically. Phase 7 is a mechanical extraction plus a config/wiring swap, not new engineering. The risk is in the *refactor not changing behavior*, not in dYdX feature complexity.

## Runtime State Inventory

> This phase includes a refactor (extraction). Inventory of state that survives a code move:

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | The Bybit catalog at `catalog/streaming` is keyed by `instance_id` UUID + instrument id, not by module path. Moving Python code does not touch on-disk feather/parquet. dYdX writes to the **same shared catalog root** (per phase goal "shared `ParquetDataCatalog`"). | None for Bybit data. For dYdX: confirm dYdX instrument ids (`*-USD-PERP.DYDX`) never collide with Bybit ids in the shared catalog (they won't — venue suffix differs). |
| Live service config | No external service stores the module path. The recorder reads `recorder.toml` by the path passed to `main()`. dYdX gets its own `recorder.toml`. | None — each recorder has its own TOML. |
| OS-registered state | systemd unit (Phase 5, OPS-02) references `scripts/bybit_recorder/recorder.py` by path. NOT YET created (Phase 5 pending). A future dYdX systemd unit will reference `scripts/dydx_recorder/recorder.py`. | None now — no systemd unit exists yet for either recorder. |
| Secrets/env vars | dYdX **data** client needs NO env vars (wallet/key vars are execution-only, `execution.py:197-214`). Bybit recorder uses `BYBIT_API_KEY`/`SECRET` (public data may not even need them). Refactor changes no env var names. | None. |
| Build artifacts | `scripts/bybit_recorder/__pycache__/` will have stale `.pyc` for moved classes. New `scripts/common_recorder/` and `scripts/dydx_recorder/` need `__init__.py`. No installed package (`scripts/` is run as a module path, not pip-installed). | Add `__init__.py` to new dirs; stale pyc is harmless (regenerated). |

**The canonical question — after the code move, what still references the old location?** The pytest suite (`tests/unit_tests/persistence/recorder/`) imports `from scripts.bybit_recorder.strategy import ...` and `from scripts.bybit_recorder.config import ...`. The re-export shim (Pattern 1) keeps these working unchanged, OR the tests are updated to import from `common_recorder`. **Recommendation: keep the re-export shim so the no-behavior-change gate (existing tests green) is unambiguous.**

## Common Pitfalls

### Pitfall 1: Inverted dependency — common strategy imports venue config
**What goes wrong:** `strategy.py` currently does `from scripts.bybit_recorder.config import load_recorder_config` (line 38), used inside `_on_config_reload`. If moved verbatim to `common_recorder`, the common module imports a venue module — a layering violation and a circular import if Bybit config imports common helpers.
**Why it happens:** The hot-reload callback re-parses the TOML using the venue-specific parser.
**How to avoid:** Inject the loader. Add a config field (e.g. `config_loader` is not msgspec-serializable, so instead pass the loader via `set_config_loader(callable)` like `set_data_client`, OR store the parsed-entry shape generically). Simplest: give `RecorderStrategyConfig` a venue-neutral hook and have each `recorder.py` call `strategy.set_config_loader(load_recorder_config)` after build. The common strategy then calls `self._config_loader(path)`.
**Warning signs:** `ImportError` / circular import at module load; `common_recorder` importing anything under `bybit_recorder`/`dydx_recorder`.

### Pitfall 2: dYdX synthesized-quote stale-stream false positives (DYDX-05)
**What goes wrong:** dYdX quotes fire only on top-of-book change (`data.py:342-351`); a calm market emits no quotes. A Bybit-tuned quote stale threshold (90s) WARNs constantly.
**Why it happens:** Event-driven synthesis vs. Bybit's near-continuous native quote stream.
**How to avoid:** Two options, both supported by the existing per-type `stale_threshold_seconds` dict: (a) set a relaxed `quote` threshold for dYdX (e.g. 300–600s), and/or (b) rely on the `deltas` stream heartbeat (the book updates far more often than top-of-book changes) as the liveness signal and set the `quote` threshold high. **Recommended: relax `quote` to a large value AND keep a tight `deltas` threshold** — deltas going quiet is the real fault signal; quote silence is normal. This needs only TOML values, no code change.
**Warning signs:** Repeated "Stale stream: quote ... idle" WARNINGs on a connected, healthy dYdX feed.

### Pitfall 3: `CUSTOM_ENCODINGS[DydxNetwork]` lowercase round-trip
**What goes wrong:** `DydxNetwork.MAINNET.name == "mainnet"` (lowercase, verified) — unlike Bybit's uppercase `.name`. If the TOML loader maps an uppercase `"MAINNET"` string back to the enum it must lowercase-normalize, and the encoder emits lowercase.
**Why it happens:** pyo3 enum `.name` reflects the Rust serde rename, not Python convention.
**How to avoid:** Encoder `lambda v: v.name` (emits lowercase). Loader: `DydxNetwork.MAINNET if env.lower()=="mainnet" else DydxNetwork.TESTNET` (case-insensitive). Default to MAINNET (matches 24/7 archival intent).
**Warning signs:** `TypeError` during `TradingNodeConfig.json()` if encoder missing; `KeyError`/`AttributeError` mapping the string back if case mismatched.

### Pitfall 4: Bar-type suffix assumption for dYdX
**What goes wrong:** The shared `_subscribe_instrument` builds `BarType.from_str(f"{id}-{interval}-LAST-EXTERNAL")`. dYdX bars come from the native `v4_candles` channel — `EXTERNAL` aggregation source is correct (venue-native, not internally aggregated), and `LAST` price type is correct.
**Why it happens:** Both venues use venue-native external klines, so the hardcoded suffix is actually correct for both — but it's an implicit assumption.
**How to avoid:** Keep `LAST-EXTERNAL` (verified correct for dYdX native candles, `data.py:421-425` passes the `BarType` straight through to `subscribe_bars`). Add a code comment noting both venues use EXTERNAL klines. If a future venue needs internal aggregation, parameterize then.
**Warning signs:** `ValueError` from `from_bar_spec` at subscribe time for an unsupported step (caught earlier by the whitelist in Pitfall 3 territory / Pattern 3).

### Pitfall 5: No-behavior-change verification gap
**What goes wrong:** The extraction silently changes behavior (e.g. a moved constant, a changed default) and tests still pass because they were testing the old path.
**Why it happens:** Large verbatim move with subtle edits.
**How to avoid:** The 61 existing recorder tests import via `scripts.bybit_recorder.*`. Keep the re-export shim so those exact imports still resolve to the moved code — then green tests prove behavior parity. Do NOT rewrite the tests in the same task. Add dYdX tests separately.
**Warning signs:** Tests pass but import from a path that no longer exercises the moved code.

## Code Examples

### dYdX recorder.toml shape (Q6)
```toml
# scripts/dydx_recorder/recorder.toml
# dYdX data client needs NO credentials (data-only; wallet/key are execution-only).
[recorder]
trader_id = "DYDX-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"   # SAME shared catalog root as Bybit
conversion_interval_minutes = 60
heartbeat_interval_seconds = 30
stale_threshold_default_seconds = 90
environment = "mainnet"                # → DydxNetwork.MAINNET (case-insensitive)

# DYDX-05: quote relaxed (event-driven, silent on calm markets); deltas tight.
[recorder.stale_threshold_seconds]
trade = 90
quote = 600        # relaxed — synthesized quotes only fire on top-of-book change
deltas = 60        # tight — book updates often; silence here = real fault
bar = 90
mark = 30
index = 30
funding = 30

# Flat perpetual list — NO linear/spot split, NO depth knob (dYdX is full-depth L2).
[[instruments]]
id = "BTC-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]

[[instruments]]
id = "ETH-USD-PERP.DYDX"
bar_intervals = ["1-MINUTE"]
```
*Open design choice (planner/discuss): whether dYdX uses `[[instruments]]` (flat) vs reusing the `[[instruments.linear]]` table name. Flat is cleaner since dYdX has no product-type axis — but the shared strategy expects per-instrument `is_linear`/depth. Resolution: the dYdX config loader maps every perp to `is_linear=True` (all dYdX perps get mark/index/funding) and supplies a dummy `depth` (ignored by the adapter). See Q1/Q5 below.*

### Funding dedup reuse verification (Q3)
```python
# scripts/common_recorder/strategy.py (unchanged from current bybit_recorder)
# Keys on (instrument_id, rate) ONLY — no Bybit fields. FundingRateUpdate.rate
# is a core model field (verified: dir(FundingRateUpdate) includes 'rate').
def on_funding_rate(self, funding_rate: FundingRateUpdate) -> None:
    self._last_seen[("funding", funding_rate.instrument_id)] = self.clock.timestamp_ns()
    last_rate = self._last_funding_rate.get(funding_rate.instrument_id)
    if last_rate is not None and last_rate == funding_rate.rate:
        return  # drop unchanged
    self._last_funding_rate[funding_rate.instrument_id] = funding_rate.rate
    self._persist_funding_rate(funding_rate)
```
Confirmed exchange-agnostic. dYdX emits `next_funding_rate` on every markets update with NO adapter dedup (`data.rs:1415-1434`), so this gate is exactly what DYDX-04 needs. Moves to `common_recorder` as-is.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Bybit-only recorder under `scripts/bybit_recorder/` | Shared `scripts/common_recorder/` + per-venue thin wiring | This phase (v1.1) | Adding venue N+1 becomes a config+wiring task, not a fork |
| Per-instrument `depth` config (Bybit) | dYdX: no depth (full-depth L2) | dYdX adapter design | dYdX TOML drops the depth knob |
| Distinct mark/index streams (Bybit) | dYdX: mark ≡ index ≡ oracle price | dYdX adapter design | Both recorded but identical; document it |

**Deprecated/outdated:** none — the Bybit recorder is current and stays in use.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | dYdX config maps every perp to `is_linear=True` (all perps get mark/index/funding) and supplies an ignored dummy `depth` to satisfy the shared `_subscribe_instrument` signature | Q1/Q5, Code Examples | If some dYdX perps lack funding/mark, those subscribes no-op harmlessly (adapter gates internally); low risk. The dummy depth is ignored by the adapter (`data.rs:830-832`). Low risk. |
| A2 | The dYdX recorder writes into the SAME shared catalog root as Bybit (phase goal says "shared `ParquetDataCatalog`") | Runtime State Inventory, recorder.toml | If a separate root is wanted instead, only `streaming_path` differs — trivial. Low risk; confirm with user in discuss. |
| A3 | Keeping the `LAST-EXTERNAL` bar-type suffix shared is correct for dYdX (native candles = EXTERNAL aggregation) | Pitfall 4 | Verified correct (`data.py:421-425` passes BarType straight to native candle subscribe). Very low risk. |
| A4 | A relaxed `quote` stale threshold (e.g. 600s) + tight `deltas` threshold satisfies DYDX-05 without code change | Pitfall 2, recorder.toml | Threshold value is a tuning choice; the *mechanism* (per-type dict) already exists and is verified. Exact seconds may need live tuning. Low risk. |
| A5 | dYdX recorder does NOT need hot-reload to meet DYDX-07 (hot-reload is HOT-01-scoped, not in DYDX-07's list) | Q8, Open Questions | DYDX-07 enumerates flush/reconnect/heartbeat/restart-gap — NOT hot-reload. But the shared strategy ALWAYS registers the config-reload timer; if `reload_config_path` is set it just works. Reusing it is free. Confirm scope with user. Low risk. |

## Open Questions

1. **TOML instrument-table shape for dYdX — flat `[[instruments]]` vs Bybit's `[[instruments.linear]]`?**
   - What we know: dYdX has no product-type axis and no spot; a flat list is cleanest. The shared strategy needs per-instrument `is_linear`, `depth`, `bar_intervals`.
   - What's unclear: whether to reuse the Bybit nested table name (less new code) or introduce a flat table (cleaner config UX).
   - Recommendation: flat `[[instruments]]` in the dYdX loader; map all to `is_linear=True`, `depth=<ignored dummy>`. Decide in discuss-phase.

2. **Does the dYdX recorder need config hot-reload (HOT-01 parity)?**
   - What we know: DYDX-07 lists flush/reconnect/heartbeat/restart-gap — NOT hot-reload. The shared strategy registers the `config-reload` timer unconditionally; with `reload_config_path` set it works for dYdX for free (the ADD branch uses the base-class `instrument_provider.load/find`, which `DydxInstrumentProvider` inherits — verified).
   - What's unclear: whether the user wants live config reload for dYdX in this phase or treats it as out-of-scope-for-now.
   - Recommendation: wire `reload_config_path` so hot-reload works (zero extra cost), but scope the *live mainnet hot-add smoke* as optional/deferred like HOT-01 was. Confirm in discuss.

3. **Shared catalog root vs separate dYdX catalog?**
   - What we know: phase goal says "shared `ParquetDataCatalog`"; venue suffixes prevent id collisions.
   - Recommendation: single shared root (`catalog/streaming`). Confirm with user.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `nautilus_trader.adapters.dydx` | dYdX wiring | ✓ | bundled in fork wheel | — |
| `DydxNetwork` pyo3 enum | network selection | ✓ | bundled | — |
| dYdX Indexer mainnet (`indexer.dydx.trade`) | live smoke test | ✗ (not probed — network) | — | testnet, or defer live smoke like HOT-01 |
| `pytest` 7.4.4 | unit tests | ✓ | 7.4.4 (pinned) | — |
| `uv` | run tests (`uv run pytest`) | ✓ (assumed per scripts/test.sh) | — | direct `pytest` |

**Missing dependencies with no fallback:** none (the adapter is bundled and import-verified this session).
**Missing dependencies with fallback:** live mainnet connectivity for the smoke test — can use testnet or defer the live run (precedent: HOT-01 live smoke was deferred then run separately).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 (pinned `<8.0.0`) |
| Config file | `pyproject.toml` (`[tool.pytest...]`) |
| Quick run command | `uv run --no-sync pytest tests/unit_tests/persistence/recorder/ -x` |
| Full suite command | `uv run --no-sync pytest tests/unit_tests/persistence/recorder/` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| DYDX-01 | Bybit recorder behavior unchanged after extraction | unit (regression) | `uv run pytest tests/unit_tests/persistence/recorder/ -x` | ✅ (61 tests across 6 files) |
| DYDX-02 | dYdX config loads; CUSTOM_ENCODINGS round-trips; node wiring builds | unit | `uv run pytest tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py -x` | ❌ Wave 0 |
| DYDX-03 | dYdX strategy subscribes all 7 feeds per instrument | unit | `uv run pytest tests/unit_tests/persistence/recorder/test_dydx_recorder_strategy.py -x` | ❌ Wave 0 |
| DYDX-04 | Funding dedup keys on `(id, rate)`, drops unchanged | unit | reuse `test_recorder_strategy.py` funding tests against shared class | ✅ (exists for Bybit; verify still green post-move) |
| DYDX-05 | Relaxed quote / tight deltas thresholds applied from TOML | unit | `test_dydx_recorder_config.py::test_stale_thresholds` | ❌ Wave 0 |
| DYDX-06 | Unsupported bar interval fails fast at config load | unit | `test_dydx_recorder_config.py::test_invalid_interval_raises` | ❌ Wave 0 |
| DYDX-07 | flush+convert on stop; restart-gap; heartbeat (shared) | unit (regression) + manual live | existing conversion/heartbeat tests + live smoke | ✅ unit / manual live deferred |

### Sampling Rate
- **Per task commit:** `uv run --no-sync pytest tests/unit_tests/persistence/recorder/ -x`
- **Per wave merge:** full recorder suite (above, no `-x`)
- **Phase gate:** full recorder suite green + (optional/deferred) live dYdX mainnet smoke before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py` — covers DYDX-02/05/06 (network round-trip, interval whitelist, threshold parsing)
- [ ] `tests/unit_tests/persistence/recorder/test_dydx_recorder_strategy.py` — covers DYDX-03 (7-feed subscribe), reusing the existing `_build_strategy`-style harness adapted for dYdX ids
- [ ] Re-export shim in `scripts/bybit_recorder/strategy.py` + `config.py` so existing 61 tests stay green (DYDX-01 gate)
- [ ] `__init__.py` for `scripts/common_recorder/` and `scripts/dydx_recorder/`

## Security Domain

> `security_enforcement: true`, ASVS level 1. This is a local data-recorder reading public market data; attack surface is narrow.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | dYdX **data** client uses no credentials (verified: wallet/key vars are execution-only, `execution.py:197-214`) |
| V3 Session Management | no | No user sessions |
| V4 Access Control | no | Single-process local archival |
| V5 Input Validation | yes | TOML is operator-controlled but validate: `InstrumentId.from_str` (raises on malformed), `_DYDX_VALID_INTERVALS` whitelist, positive-threshold checks (mirrors existing Bybit `config.py` validation) |
| V6 Cryptography | no | No crypto operations in the data path |
| V7 Error/Logging | yes | Hot-reload re-reads TOML mid-edit (TOCTOU) — already wrapped in `try/except` that logs once and keeps last-good config (`_on_config_reload`); log instrument counts, never credentials |

### Known Threat Patterns for {Python local recorder}
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed/half-written TOML on hot-reload poll | DoS (component fault) | Whole `_on_config_reload` body wrapped in try/except (existing) — bad reload skipped, recorder keeps running |
| Absurd threshold value spamming logs | DoS (log flood) | Fail-fast `> 0` validation at config load (existing pattern in `config.py:273-299`) |
| Unsupported bar interval crashing node at runtime | Availability | Config-load whitelist (DYDX-06) fails before node start, not mid-run |

## Sources

### Primary (HIGH confidence)
- `scripts/bybit_recorder/strategy.py` (full read) — RecorderStrategy/Config, conversion, heartbeat, funding dedup, hot-reload all embedded here (no separate hot_reload.py); only venue coupling is `set_data_client` + `load_recorder_config` import
- `scripts/bybit_recorder/config.py` (full read) — RecorderConfig, `load_recorder_config`, `_resolve_catalog_path`, `build_streaming_config`, depth tables, threshold validators
- `scripts/bybit_recorder/recorder.py` (full read) — CUSTOM_ENCODINGS + factory wiring + `_clients` injection pattern (lines 49-50, 138-139)
- `nautilus_trader/adapters/dydx/data.py:370-499` — all 7 subscribe + unsubscribe methods, L2-only enforcement, quote synthesis
- `nautilus_trader/adapters/dydx/factories.py` (full read) — `DydxLiveDataClientFactory.create` wiring
- `nautilus_trader/adapters/dydx/config.py:28-67` — `DydxDataClientConfig` fields
- `nautilus_trader/adapters/dydx/providers.py:30` + `nautilus_trader/common/providers.py:244,376` — `DydxInstrumentProvider` inherits base `load`/`find` (hot-reload reuse)
- `crates/adapters/dydx/src/common/enums.rs:679-734` — `DydxCandleResolution` supported set + `from_bar_spec` validation
- `tests/unit_tests/persistence/recorder/conftest.py` + `test_recorder_strategy.py` (read) — existing 61-test harness, `_build_strategy` pattern
- Runtime checks (executed this session): `DydxNetwork.MAINNET.name == 'mainnet'`; `FundingRateUpdate` has `rate` attribute

### Secondary (MEDIUM confidence)
- `.planning/research/STACK.md` — prior dYdX stack research (field reference, endpoints, instrument-id format)
- `.planning/research/FEATURES.md` — prior dYdX feature research (per-data-type adapter behavior, dedup, quote synthesis, mark≡index)

### Tertiary (LOW confidence)
- none

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all classes/fields source-read + import-verified this session
- Architecture (extraction boundary): HIGH — strategy.py read in full; venue coupling enumerated precisely
- Pitfalls: HIGH — each traced to a specific source line or runtime check
- DYDX-05 threshold values: MEDIUM — mechanism verified, exact seconds are a tuning choice (A4)

**Research date:** 2026-06-16
**Valid until:** 2026-07-16 (stable — in-repo fork; only changes if the dYdX adapter or recorder code is modified)
