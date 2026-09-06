---
baseline_commit: 1809690d2d
---

# Story 8.2: Technical indicator computation via `nautilus_trader.indicators`

Status: ready-for-dev

<!-- Depends on nothing per epics.md — backend-only, additive. Story 8.1 (dashboard.py chart
     consolidation) is still status: review / uncommitted in the working tree at the time
     this story was created, but 8.2 does not touch the Lines/Candles/Ticks rendering path
     8.1 changed, only adds new endpoints and a new module, so there is no ordering conflict. -->

## Story

As a strategy developer/builder,
I want every indicator in `nautilus_trader.indicators` computed once from the existing candle data using the real indicator classes, and selectable for the chart,
so that the chart offers the full built-in indicator toolkit with zero reimplementation and zero new dependency.

## Acceptance Criteria

1. **`ml_signals/chart_indicators.py` (new, dispatch/metadata only — DESIGN-02, no indicator math of its own) defines `INDICATOR_CATALOG: dict[str, IndicatorSpec]`** covering every concrete, OHLCV-drivable class in `nautilus_trader.indicators`. Each entry records: the class, its constructor parameter names/types/defaults, which OHLCV field(s) and in what order feed its `update_raw` (per-indicator, not assumed uniform — see Dev Notes for the confirmed signature of every entry), which attribute(s) hold its output, and a panel classification (`"overlay"` for price-scale indicators vs `"oscillator"` for bounded/differently-scaled ones).
2. **`replay_indicator(candles: list[dict], name: str, params: dict) -> dict[str, list[float | None]]`** in `chart_indicators.py`: instantiates the real class from the catalog with the given params, replays it by calling `update_raw` once per candle in chronological order with that indicator's registered OHLCV feed, and returns each configured output attribute as a list of values aligned 1:1 with the input candles — `None` for every candle before `indicator.initialized` becomes true (the indicator's own warm-up state, never a hand-computed guess).
3. **`GET /data/coin/{id}/indicators?bar=&start=&end=&spec=SimpleMovingAverage:period=20,RelativeStrengthIndex:period=14`** in `dashboard.py` (mirrors `coin_candles_handler`'s pattern): fetches candles for the window/bar via the existing candle-building path (reused, not duplicated — see Task 0 on the volume gap this requires closing first), calls `replay_indicator` once per requested spec entry, and returns a JSON object keyed by a stable id (e.g. `"SimpleMovingAverage_period=20"`) each mapping to `{t, value}`-shaped points per output attribute. A name not present in `INDICATOR_CATALOG` returns HTTP 400 with a clear error message.
4. **`GET /data/indicators/catalog`**: returns `INDICATOR_CATALOG` serialized to JSON (name, params with defaults, panel classification) for every registered indicator.
5. **Tests** (`ml_signals/tests/test_chart_indicators.py`, new, TEST-01 — financial calculation): `replay_indicator` asserted against a known small candle series for at least one indicator from each output-shape category present in the catalog — single-value (`SimpleMovingAverage`, hand-computable expected values), banded (`BollingerBands`, all three of `upper`/`middle`/`lower`), dual-line (`Stochastics`, both `value_k`/`value_d`) — plus a warm-up test confirming `None`-padding matches the indicator's own `initialized` transition. Every catalog entry must additionally be confirmed importable and instantiable with its default params in one smoke-test loop over `INDICATOR_CATALOG` (exhaustive per-indicator value tests are not required — the mechanism is generic and class-driven).

## Tasks / Subtasks

- [ ] Task 0 — Add volume to the candle-building path (blocks AC #1/#3 for volume-fed indicators)
  - [ ] Confirmed gap (see Dev Notes "Volume gap"): `ml_signals/candles.py`'s `Candle` dataclass and `build_candles()` carry no volume field, and neither does `dashboard._live_candles_json`/`_historical_candles_json`'s output dict (`{t,o,h,l,c}` only). `OnBalanceVolume`, `VolumeWeightedAveragePrice`, `KlingerVolumeOscillator`, `Pressure` need a `v` field per candle.
  - [ ] Extend `Candle`/`build_candles()` to accept `(ts_event, price, size)` rows and emit a summed `volume` field per bucket.
  - [ ] Extend `_historical_candles_json` to pass `t.size.as_double()` alongside price (trade size is already read the same way in `_historical_ticks_json`) and include `v` in each candle dict.
  - [ ] Extend `_live_candles_json` to sum `s["buy_volume"] + s["sell_volume"]` per bucket from `_second_rolling` snapshots (same fields `_price_series_rows` already reads) and include `v`.
  - [ ] Do this as a minimal, additive field append — do not touch Candles-mode rendering/pagination behavior (Story 8.1 territory), only the data shape.
- [ ] Task 1 — Build `INDICATOR_CATALOG` (AC: #1)
  - [ ] New file `ml_signals/chart_indicators.py`. Define `IndicatorSpec` (a small dataclass: `cls`, `params: dict[str, ParamSpec]`, `feed: tuple[str, ...]`, `outputs: tuple[str, ...]`, `panel: Literal["overlay", "oscillator"]`).
  - [ ] Populate `INDICATOR_CATALOG` for the confirmed set in Dev Notes' "Confirmed indicator inventory" table only — do not include the six excluded classes (see Dev Notes "Exclusions"), and flag (comment, don't silently include) `FuzzyCandlesticks` and `Swings` as excluded too (non-float outputs — see Dev Notes).
- [ ] Task 2 — `replay_indicator` (AC: #2)
  - [ ] Instantiate `spec.cls(**params)`, feed `getattr`-based lookup of each candle's `feed` fields per candle, in order, via `update_raw(*values)`.
  - [ ] Collect `getattr(indicator, attr)` for each `spec.outputs` attr after each `update_raw` call; use `None` while `not indicator.initialized`.
  - [ ] Keep under ~30 lines (READ-01) — split the per-candle step into a small helper if needed.
- [ ] Task 3 — Endpoints (AC: #3, #4)
  - [ ] `coin_indicators_handler` mirroring `coin_candles_handler`'s live/historical dispatch on `start`/`end` presence; parse `spec=Name:param=val,param2=val2|Name2:...` into `(name, params)` pairs; 400 on unknown name via `web.Response(status=400, ...)`, not an unhandled exception.
  - [ ] `indicators_catalog_handler` — static JSON dump of `INDICATOR_CATALOG` metadata (name, params+defaults, panel). No candle fetch involved.
  - [ ] Register both in `setup_routes`: `GET /data/coin/{id}/indicators`, `GET /data/indicators/catalog` (same block as the existing `/data/coin/{id}/candles` etc. registrations, dashboard.py:1480 area).
- [ ] Task 4 — Tests (AC: #5)
  - [ ] `ml_signals/tests/test_chart_indicators.py`: hand-computable `SimpleMovingAverage` case, `BollingerBands` upper/middle/lower, `Stochastics` value_k/value_d, a warm-up/`None`-padding test, and the `INDICATOR_CATALOG` smoke-test loop (instantiate every entry with its default params, assert no exception).
  - [ ] `cd troll && python -m pytest ml_signals/tests/test_chart_indicators.py ml_signals/tests/test_dashboard_chart.py -q` passes.

## Dev Notes

- **Read before touching anything:** `dashboard.py`'s `coin_candles_handler`/`_live_candles_json`/`_historical_candles_json` (candle-building/endpoint pattern to mirror) and `ml_signals/candles.py`'s `Candle`/`build_candles` (needs the volume extension in Task 0). Line numbers shift release to release — search by name, don't trust any cited here.

### Volume gap (confirmed via direct code read this session, not assumed)

`ml_signals/candles.py`'s `Candle` dataclass is `ts_open, open, high, low, close` — **no volume**. `build_candles()` only ever aggregates price. `dashboard._historical_candles_json`/`_live_candles_json` both emit `{t,o,h,l,c}` dicts with no `v`. Epics.md's AC #3 (and this story's AC #1/#3) assume a `v` field is available per candle for VWAP/OBV/KVO/Pressure — it doesn't exist yet. Task 0 closes this gap the same way `_historical_ticks_json` already sources trade size (`t.size.as_double()`) and `_price_series_rows` already sources `buy_volume`/`sell_volume` — no new data source, just a new field threaded through the existing aggregation.

### Confirmed indicator inventory (introspected directly against this repo's pinned `nautilus_trader` this session — do not re-derive from memory, the epics.md list undercounts the exclusions below)

`nautilus_trader.indicators` exposes 45 public PascalCase names. Of those, **6 are not usable by this story's `update_raw`-replay design** and must be excluded from `INDICATOR_CATALOG`:

- `CandleBodySize`, `CandleDirection`, `CandleSize`, `CandleWickSize` — these are `IntEnum`-style value classes (candle-shape classification constants), not `Indicator` subclasses at all. No `update_raw`, no `initialized`.
- `FuzzyCandle` — the *output value type* `FuzzyCandlesticks` produces per candle (a small namedtuple-like object with `body_size`/`direction`/`upper_wick_size`/`lower_wick_size` fields), not an indicator itself.
- `SpreadAnalyzer` — a real `Indicator`, but driven by `handle_quote_tick`/`handle_trade_tick` (bid/ask ticks), has no `update_raw` and cannot be fed from OHLCV candles. Out of scope for this story's candle-replay mechanism (it's a candidate for a future story reading from `DydxSecondSnapshot`/Lines-mode data instead, not this one).

One further class is technically `update_raw`-callable but produces **non-float output** that doesn't fit this story's `dict[str, list[float | None]]` contract — exclude it too, don't force it in:

- `FuzzyCandlesticks(period, ...)` — `update_raw(open, high, low, close)` works, but its outputs are `.value` (a `FuzzyCandle`, not a float) and `.vector`. Would need a bespoke mapping this story doesn't scope. Skip.
- `Swings(period)` — `update_raw(high, low, timestamp: datetime)` needs a `datetime`, not a bar-aligned float, and its outputs (`high_datetime`, `low_datetime`, `direction`, `changed`) are a mix of `datetime`/`int`/`bool`, not a uniform float series. Skip.

That leaves **38 confirmed catalog entries**. Constructor signatures below are copied verbatim from each class's own docstring (Cython embeds the real signature there — `inspect.signature()` on the compiled `__init__` itself just shows `(*args, **kwargs)`, don't rely on that). `update_raw` signatures and output attributes were confirmed via `dir()` diffed against the base `Indicator` class and, where ambiguous, by feeding synthetic values and inspecting the result — both done directly against the installed package this session, not assumed from documentation.

| Class | Constructor (from docstring) | `update_raw` feed | Output attr(s) | Panel |
|---|---|---|---|---|
| SimpleMovingAverage | `(int period, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| ExponentialMovingAverage | `(int period, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| WeightedMovingAverage | `(int period, weights=None, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| HullMovingAverage | `(int period, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| AdaptiveMovingAverage | `(int period_er, int period_alpha_fast, int period_alpha_slow, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| DoubleExponentialMovingAverage | `(int period, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| VariableIndexDynamicAverage | `(int period, PriceType price_type=LAST, MovingAverageType cmo_ma_type=SIMPLE)` | `value` (close) | `value` | overlay |
| WilderMovingAverage | `(int period, PriceType price_type=LAST)` | `value` (close) | `value` | overlay |
| BollingerBands | `(int period, double k, MovingAverageType ma_type=SIMPLE)` | high, low, close | `upper`, `middle`, `lower` | overlay |
| KeltnerChannel | `(int period, double k_multiplier, ma_type=EXPONENTIAL, ma_type_atr=SIMPLE, bool use_previous=True, double atr_floor=0)` | high, low, close | `upper`, `middle`, `lower` | overlay |
| DonchianChannel | `(int period)` | high, low | `upper`, `middle`, `lower` | overlay |
| KeltnerPosition | `(int period, double k_multiplier, ma_type=EXPONENTIAL, ma_type_atr=SIMPLE, bool use_previous=True, double atr_floor=0)` | high, low, close | `value` | oscillator |
| VolumeWeightedAveragePrice | `()` | price, volume, timestamp — **note 3-arg feed incl. timestamp, not just OHLCV** | `value` | overlay |
| RelativeStrengthIndex | `(int period, ma_type=None)` | `value` (close) | `value` | oscillator |
| MovingAverageConvergenceDivergence | `(int fast_period, int slow_period, ma_type=EXPONENTIAL, PriceType price_type=LAST)` | `value` (close) | `value` | oscillator |
| Stochastics | `(int period_k, int period_d, int slowing=1, ma_type=None, str d_method='ratio')` | high, low, close | `value_k`, `value_d` | oscillator |
| CommodityChannelIndex | `(int period, double scalar=0.015, ma_type=None)` | high, low, close | `value` | oscillator |
| AverageTrueRange | `(int period, ma_type=SIMPLE, bool use_previous=True, double value_floor=0)` | high, low, close | `value` | oscillator |
| VolatilityRatio | `(int fast_period, int slow_period, ma_type=SIMPLE, bool use_previous=True, double value_floor=0)` | high, low, close | `value` | oscillator |
| AroonOscillator | `(int period)` | high, low | `aroon_up`, `aroon_down`, `value` | oscillator |
| DirectionalMovement | `(int period, ma_type=EXPONENTIAL)` | high, low | `pos`, `neg`, `value` | oscillator |
| RateOfChange | `(int period, bool use_log=False)` | `price` (close) | `value` | oscillator |
| ChandeMomentumOscillator | `(int period, ma_type=None)` | `value` (close) | `value` | oscillator |
| OnBalanceVolume | `(int period=0)` | open, close, volume | `value` | oscillator |
| Pressure | `(int period)` — *(volume-fed, see Task 0)* | high, low, close, volume | `value`, `value_cumulative` | oscillator |
| KlingerVolumeOscillator | `(int fast_period, int slow_period, int signal_period)` — *(volume-fed)* | high, low, close, volume | `value` | oscillator |
| ArcherMovingAveragesTrends | `(int fast_period, int slow_period, int signal_period, ma_type=EXPONENTIAL)` | `close` | `long_run`, `short_run` (ints 0/1 — cast to float) | oscillator |
| IchimokuCloud | `(int tenkan_period=9, int kijun_period=26, int senkou_period=52, int displacement=26)` | high, low, close | `tenkan_sen`, `kijun_sen`, `senkou_span_a`, `senkou_span_b`, `chikou_span` | overlay |
| LinearRegression | `(int period)` | `close` | `value`, `slope`, `intercept`, `degree`, `cfo`, `R2` | oscillator |
| EfficiencyRatio | `(int period)` | `price` (close) | `value` | oscillator |
| PsychologicalLine | `(int period)` | `close` | `value` | oscillator |
| Bias | `(int period)` | `close` | `value` | oscillator |
| RelativeVolatilityIndex | `(int period)` | `close` | `value` | oscillator |
| VerticalHorizontalFilter | `(int period)` | `close` | `value` | oscillator |

*(Confirm each entry's exact constructor param name/type at implementation time by printing `cls.__doc__` directly, as done this session — this table is a verified starting point, not a substitute for a final check while writing `INDICATOR_CATALOG`, since a param default expressed as an enum member (`MovingAverageType.SIMPLE` etc.) needs a JSON-safe default value for AC #4's catalog endpoint, e.g. the enum member's `.name`.)*

- **`params` JSON-safety (AC #4):** several constructors default to enum members (`MovingAverageType`, `PriceType`). `INDICATOR_CATALOG`'s serialized defaults must be JSON-safe (e.g. the enum's `.name` string) — Story 8.4's picker renders these as editable fields, so a raw enum object in the JSON response would break `json.dumps`.
- **Bar dict shape:** candles already flow through this codebase as `{t, o, h, l, c}` (see `_historical_candles_json`/`_live_candles_json`); this story adds `v` (Task 0). `replay_indicator`'s `feed` lookup should map `"open"→"o"`, `"high"→"h"`, `"low"→"l"`, `"close"→"c"`, `"volume"→"v"`, `"price"→"c"` (RateOfChange/EfficiencyRatio's `price` arg is just closing price) — don't invent a second candle shape.
- **`VolumeWeightedAveragePrice.update_raw` takes a `timestamp` arg** (its rolling window resets daily) — use the candle's `t` (already ms; VWAP wants a `datetime`, so convert once: `datetime.fromtimestamp(t/1000, tz=UTC)`).
- **Follow troll/CLAUDE.md:** DESIGN-02 (this module is dispatch/metadata only, no indicator math — the indicator classes do the math), READ-01 (functions under ~30 lines), TEST-01 (financial calc — tests required), MEM-01 (this story replays over a caller-bounded candle window from an existing bounded endpoint, no new unbounded read introduced).
- **Story 8.1 is uncommitted in the working tree** at the time this story was created (large diff on `dashboard.py`/`chart_data.py`/`test_dashboard_chart.py`, no corresponding commit yet, status `review`). This story's changes are additive (new file, new endpoints, new field on the candle path) and should not conflict, but re-check `dashboard.py`'s current `setup_routes`/`coin_candles_handler` state before editing since it may have moved since this story was drafted.

### Project Structure Notes

- New file: `troll/ml_signals/chart_indicators.py`.
- Modified: `troll/ml_signals/dashboard.py` (two new handlers + route registrations, volume field added to the two candle-JSON builders).
- Modified: `troll/ml_signals/candles.py` (`Candle`/`build_candles` gain a volume field).
- New tests: `troll/ml_signals/tests/test_chart_indicators.py`.
- No changes to `troll/dydx_collector/`, `troll/bot_tui/`, `troll/live_paper/`, `troll/ranking_engine/`.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 8, Story 8.2] — authoritative AC source.
- [Source: troll/ml_signals/dashboard.py] — `coin_candles_handler`, `_live_candles_json`, `_historical_candles_json`, `setup_routes` (pattern to mirror for the two new endpoints).
- [Source: troll/ml_signals/candles.py] — `Candle`, `build_candles` (needs the Task 0 volume extension).
- [Source: troll/ml_signals/tests/test_dashboard_chart.py] — existing catalog-backed test pattern (temp `ParquetDataCatalog`, `_write_trades_to_catalog`-style helpers) to mirror if `test_chart_indicators.py` needs catalog-backed fixtures.
- [Source: nautilus_trader.indicators, installed package] — `INDICATOR_CATALOG` inventory, constructor signatures, `update_raw` signatures, output attributes: all confirmed via direct `inspect`/`dir()`/docstring introspection against this repo's pinned version this session (not assumed from upstream docs, which may not match the pinned 1.229.0 build).
- [Source: troll/CLAUDE.md] — DESIGN-02 (module boundaries), READ-01 (function length), TEST-01 (financial calc tests required), MEM-01 (bounded reads).
- [Source: _bmad-output/implementation-artifacts/8-1-consolidate-the-interactive-chart-widget-onto-chart-id-only.md] — sibling story establishing the `{rows/points, truncated}`-style endpoint conventions and the `catalog.query()`→`CustomData`→`.data` unwrap gotcha (not needed by this story, which reads via `trade_ticks()`/the existing candle path, not `catalog.query`, but useful context if a future story adds catalog-backed indicator replay for a historical-only data type).

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
