# Nautilus indicator and backtest catalog

Lookup-only map of every indicator, example strategy and backtest pattern of the pinned
`nautilus_trader` 1.229.0, and how each is reached from `platform/research`. It is not loaded
automatically. Read it when choosing what to run in a notebook, or when asking "does Nautilus
already have this?" (platform/CLAUDE.md: use Nautilus built-ins first).

Measured on: the notebooks were run on the host's `nautilus_trader` 1.228.0; the pin is 1.229.0,
and every cited line number is 1.229's (a few statistics below exist only there).

The fork rule applies: `nautilus_trader/` and `crates/` are never modified. Upstream facts below
carry `file:line` citations into `nautilus_trader/`, `examples/` and `docs/` of this checkout, so
a later bump can re-verify them.

"Reached here by" in the tables names one of these entry points:

| Entry point | What it is |
|-------------|-----------|
| `research.strategies.ma_cross_strategy:MACrossStrategy` | Two moving averages crossing; axes `ma_type` (every `MovingAverageType` name) and `exit` (`cross`, `atr_stop`, `trailing_atr`). |
| `research.strategies.indicator_signal_strategy:IndicatorSignalStrategy` | One indicator turned into a +1/0/-1 rule; axes `signal` (one name per bar indicator) and `filter` (`none`, `vhf`, `volatility_ratio`). |
| notebook `07_indicator_atlas` | Every indicator replayed over archive bars and drawn. |
| notebook `08_strategy_gallery` | Upstream example strategies and the two family strategies run through `NodeRunner`, as a leaderboard, plus the four liquidation cascade runs (Story 33.14) and the four OFI forced-flow runs -- baseline, filter, `fade`, `follow` (Story 33.13). |
| notebook `09_liquidations` | The liquidation study (Story 33.13): cascade episodes and their forward returns, implied leverage, forced share, trade match, organic delta, liquidations against OI, cross venue. |
| `RunSpec` | Fields `fill_model`, `fee_model`, `latency_ms` or `latency`, `exec_algorithms`, and the `data` kinds `seconds`, `trades`, `bars:<spec>`, `liquidations` (Bybit LINEAR ids only: the derived quotes plus the archived `custom_liquidation` rows), `seconds_liquidations` (Bybit LINEAR ids only: `seconds` plus those rows, Story 33.13). |
| `research.strategies.ofi_strategy:OFIStrategy` | Snapshot OFI/OBI strategy (platform indicators, section 2). Story 33.13 adds `forced_flow_filter` (default False: the cumulative delta without each second's liquidated size, `kernel.indicators.organic_delta_units`) and `liquidation_cascade_mode` (`off` default, `follow`, `fade`: gate OFI's entries on the phase of `LiquidationCascade`, `research.strategies.cascade_rules.next_phase`/`cascade_allows`), with the detector's `cascade_window_s` (30, also the fade window), `cascade_baseline_s` (3600), `cascade_intensity_threshold` (3.0) and `cascade_decay_ratio` (0.5). Either option needs a Bybit LINEAR id and `data="seconds_liquidations"`; both off, the strategy is unchanged. |
| `research.strategies.candle_pattern_strategy:CandlePatternStrategy` | The 22 candlestick patterns (section 2, notebook 06). |

Every backtest goes through `research.application.backtest_runner.NodeRunner` (NAUT-03). A
strategy is named by string path, never imported.

---

## 1. Indicators (`nautilus_trader.indicators`)

`nautilus_trader/indicators/__init__.py` exports 47 names in `__all__`. Constructors are
`.pyx` signatures; line numbers point at the class (`cdef class`) in the named file. Outputs are
the `cdef readonly` attributes of the matching `.pxd`.

### 1.1 The `Indicator` base API

| Member | Meaning | Source |
|--------|---------|--------|
| `initialized` | True once enough inputs arrived to produce a valid value. | `base.pxd:28`, `base.pyx:40` |
| `has_inputs` | True once any input arrived (set before `initialized`). | `base.pyx:39` |
| `reset()` | Calls the subclass `_reset()`, clears both flags. | `base.pyx:66-68` |
| `name` | Class name; `repr` prints the constructor params. | `base.pyx:37,44` |
| `handle_bar(bar)` | Feed a `Bar`. | `base.pyx:56` |
| `handle_quote_tick(tick)` | Feed a `QuoteTick`. | `base.pyx:48` |
| `handle_trade_tick(tick)` | Feed a `TradeTick`. | `base.pyx:52` |

The base `handle_*` methods raise `NotImplementedError`. Which class overrides which:

| Handlers implemented | Classes |
|----------------------|---------|
| `handle_bar`, `handle_quote_tick`, `handle_trade_tick` | Every moving average (all 8 in 1.2), `MovingAverageConvergenceDivergence` (`trend.pyx:402-431`), `BollingerBands` (`volatility.pyx:209-241`), `DonchianChannel` (`volatility.pyx:337-368`). |
| `handle_quote_tick` only | `SpreadAnalyzer` (`spread_analyzer.pyx:56`). |
| `handle_bar` only | Every other class in sections 1.3 to 1.7, including `KeltnerChannel`, `AverageTrueRange`, `Stochastics`. |

`register_indicator_for_bars(bar_type, indicator)` on a `Strategy` routes bars to `handle_bar`,
so it works for every bar-capable class above. `update_raw` is the explicit alternative and is
what the atlas replay and the `IndicatorSignalStrategy` signal table use as a fallback.

Warm-up is per class: before `initialized`, `value` is a placeholder (0.0 or the first input) and
must not be read as a signal. Every strategy here gates on `indicators_initialized()` or
`initialized`.

### 1.2 Moving averages (`averages.pyx`)

All take `price_type=PriceType.LAST` (what a quote or trade tick contributes) and expose `value`,
`period`, `count`, `alpha` where defined. `update_raw(value)` takes one price.

| Class | Constructor | `update_raw` inputs | Outputs | Reached here by |
|-------|-------------|--------------------|---------|-----------------|
| `SimpleMovingAverage` (`:96`) | `(period, price_type)` | `value` | `value` | MACross `ma_type=SIMPLE`, atlas |
| `ExponentialMovingAverage` (`:182`) | `(period, price_type)` | `value` | `value`, `alpha` | MACross `EXPONENTIAL`, atlas, upstream `EMACross*` |
| `DoubleExponentialMovingAverage` (`:268`) | `(period, price_type)` | `value` | `value` | MACross `DOUBLE_EXPONENTIAL`, atlas |
| `WeightedMovingAverage` (`:364`) | `(period, weights=None, price_type)` | `value` | `value`, `weights` | MACross `WEIGHTED` (default weights), atlas |
| `HullMovingAverage` (`:475`) | `(period, price_type)` | `value` | `value` | MACross `HULL`, atlas |
| `AdaptiveMovingAverage` (`:582`) | `(period_er, period_alpha_fast, period_alpha_slow, price_type)`; slow must exceed fast | `value` | `value`, `alpha_fast`, `alpha_slow` | MACross `ADAPTIVE` (built explicitly, see 5), atlas |
| `WilderMovingAverage` (`:704`) | `(period, price_type)` | `value` | `value`, `alpha` | MACross `WILDER`, atlas |
| `VariableIndexDynamicAverage` (`:790`) | `(period, price_type, cmo_ma_type=SIMPLE)`; `cmo_ma_type` may not be `VARIABLE_INDEX_DYNAMIC` | `value` | `value`, `alpha`, `cmo_pct` | MACross `VARIABLE_INDEX_DYNAMIC`, atlas |

`MovingAverage` (`:34`) is the base class: `(period, params, price_type)`, fields `period`,
`price_type`, `value`, `count`.

`MovingAverageType` (`averages.pxd:25-33`), 8 values:

| Value | Name | Factory builds it |
|-------|------|-------------------|
| 0 | `SIMPLE` | yes |
| 1 | `EXPONENTIAL` | yes |
| 2 | `DOUBLE_EXPONENTIAL` | yes |
| 3 | `WILDER` | yes |
| 4 | `HULL` | yes |
| 5 | `ADAPTIVE` | **no, returns `None`** |
| 6 | `WEIGHTED` | yes (`**kwargs` reach the weights) |
| 7 | `VARIABLE_INDEX_DYNAMIC` | yes (`**kwargs` reach `price_type`, `cmo_ma_type`) |

`MovingAverageFactory.create(period, ma_type, **kwargs)` is `averages.pyx:898-950`. It is a
staticmethod returning one of the classes above; it raises only for a non-positive period.

### 1.3 Momentum (`momentum.pyx`)

All are `handle_bar` only. Several take `ma_type`, defaulting to the type shown.

| Class | Constructor | `update_raw` inputs | Outputs | Reached here by |
|-------|-------------|--------------------|---------|-----------------|
| `RelativeStrengthIndex` (`:52`) | `(period, ma_type=EXPONENTIAL)` | `value` (close) | `value` in 0..1 | signal `rsi`, upstream `BBMeanReversion`, atlas |
| `RateOfChange` (`:152`) | `(period, use_log=False)` | `price` | `value` | signal `roc`, atlas |
| `ChandeMomentumOscillator` (`:220`) | `(period, ma_type=WILDER)` | `close` | `value` | signal `cmo`, atlas |
| `Stochastics` (`:333`) | `(period_k, period_d, slowing=1, ma_type=None, d_method="ratio")` | `high, low, close` | `value_k`, `value_d` | signal `stochastics`, atlas |
| `CommodityChannelIndex` (`:539`) | `(period, scalar=0.015, ma_type=SIMPLE)` | `high, low, close` | `value` | signal `cci`, atlas |
| `EfficiencyRatio` (`:653`) | `(period)` | `price` | `value` | signal `efficiency_ratio`, atlas |
| `RelativeVolatilityIndex` (`:731`) | `(period, scalar=100.0, ma_type=EXPONENTIAL)` | `close` | `value` | signal `rvi`, atlas |
| `PsychologicalLine` (`:851`) | `(period, ma_type=SIMPLE)` | `close` | `value` | signal `psl`, atlas |

`StochasticsDMethod` (`momentum.pyx:317`) is a string holder: `"ratio"` (Nautilus original, range
weighted) or `"moving_average"` (cTrader/MetaTrader compatible). Any other `d_method` raises
(`:385-390`). `Stochastics` with `ma_type=None` uses the exponential default.

### 1.4 Trend (`trend.pyx`)

| Class | Constructor | `update_raw` inputs | Outputs | Reached here by |
|-------|-------------|--------------------|---------|-----------------|
| `ArcherMovingAveragesTrends` (`:58`) | `(fast_period, slow_period, signal_period, ma_type=EXPONENTIAL)` | `close` | `long_run`, `short_run` (0/1 ints) | signal `archer`, atlas |
| `AroonOscillator` (`:164`) | `(period)` | `high, low` | `aroon_up`, `aroon_down`, `value` | signal `aroon`, atlas |
| `DirectionalMovement` (`:251`) | `(period, ma_type=EXPONENTIAL)` | `high, low` | `pos`, `neg`, `value` | signal `directional_movement`, atlas |
| `MovingAverageConvergenceDivergence` (`:351`) | `(fast_period, slow_period, ma_type=EXPONENTIAL, price_type=LAST)`; slow must exceed fast | `close` | `value` (fast MA minus slow MA) | signal `macd`, atlas |
| `IchimokuCloud` (`:471`) | `(tenkan_period=9, kijun_period=26, senkou_period=52, displacement=26)` | `high, low, close` | `tenkan_sen`, `kijun_sen`, `senkou_span_a`, `senkou_span_b`, `chikou_span` | signal `ichimoku`, atlas |
| `LinearRegression` (`:616`) | `(period=0)` | `close` | `slope`, `intercept`, `degree`, `cfo`, `R2`, `value` | signal `linear_regression`, atlas |
| `Bias` (`:715`) | `(period, ma_type=SIMPLE)` | `close` | `value` | signal `bias`, atlas |
| `Swings` (`:785`) | `(period)` | `high, low, timestamp` (a `datetime`) | `direction`, `changed`, `high_price`, `low_price`, `high_datetime`, `low_datetime`, `length`, `duration`, `since_high`, `since_low` | signal `swings`, atlas |

`MovingAverageConvergenceDivergence` has no signal line: `value` is the fast MA minus the slow MA
only (`trend.pyx:445-458`). `signal_period` belongs to `ArcherMovingAveragesTrends` and
`KlingerVolumeOscillator`. `IchimokuCloud` requires `kijun_period >= tenkan_period` and
`senkou_period >= kijun_period`.

### 1.5 Volatility and bands (`volatility.pyx`)

| Class | Constructor | `update_raw` inputs | Outputs | Reached here by |
|-------|-------------|--------------------|---------|-----------------|
| `AverageTrueRange` (`:57`) | `(period, ma_type=SIMPLE, use_previous=True, value_floor=0)` | `high, low, close` | `value` | MACross exits `atr_stop`, `trailing_atr`; upstream bracket and trailing-stop strategies; atlas |
| `BollingerBands` (`:166`) | `(period, k, ma_type=SIMPLE)` | `high, low, close` | `upper`, `middle`, `lower`, `k` | signal `bollinger`, upstream `BBMeanReversion`, atlas |
| `DonchianChannel` (`:305`) | `(period)` | `high, low` | `upper`, `middle`, `lower` | signal `donchian`, atlas |
| `KeltnerChannel` (`:418`) | `(period, k_multiplier, ma_type=EXPONENTIAL, ma_type_atr=SIMPLE, use_previous=True, atr_floor=0)` | `high, low, close` | `upper`, `middle`, `lower` | signal `keltner`, atlas |
| `KeltnerPosition` (`:738`) | same as `KeltnerChannel` | `high, low, close` | `value` = (close - middle) / half channel width, 0 when the width is 0 (`volatility.pyx:842-846`) | atlas |
| `VerticalHorizontalFilter` (`:537`) | `(period, ma_type=SIMPLE)` | `close` | `value` | filter `vhf`, atlas |
| `VolatilityRatio` (`:622`) | `(fast_period, slow_period, ma_type=SIMPLE, use_previous=True, value_floor=0)`; fast must be below slow | `high, low, close` | `value` | filter `volatility_ratio`, atlas |

`BollingerBands.update_raw` takes `(high, low, close)` and averages the typical price, not the
close alone (`volatility.pyx:259`).

### 1.6 Volume (`volume.pyx`)

| Class | Constructor | `update_raw` inputs | Outputs | Reached here by |
|-------|-------------|--------------------|---------|-----------------|
| `OnBalanceVolume` (`:52`) | `(period=0)`; 0 means unbounded | `open, close, volume` | `value` | signal `obv`, atlas |
| `VolumeWeightedAveragePrice` (`:133`) | `()` | `price, volume, timestamp` (`datetime`; resets at the UTC day boundary) | `value` | signal `vwap`, atlas |
| `KlingerVolumeOscillator` (`:213`) | `(fast_period, slow_period, signal_period, ma_type=EXPONENTIAL)` | `high, low, close, volume` | `value` | signal `kvo`, atlas |
| `Pressure` (`:333`) | `(period, ma_type=EXPONENTIAL, atr_floor=0)` | `high, low, close, volume` | `value`, `value_cumulative` | signal `pressure`, atlas |

### 1.7 Other

| Class | Constructor | Inputs | Outputs | Reached here by |
|-------|-------------|--------|---------|-----------------|
| `SpreadAnalyzer` (`spread_analyzer.pyx:28`) | `(instrument_id, capacity)` | quote ticks only (no `update_raw`) | `current`, `average` (spread) | atlas only (needs quotes; the `seconds` kind derives them) |
| `FuzzyCandlesticks` (`fuzzy_candlesticks.pyx:82`) | `(period, threshold1=0.5, threshold2=1.0, threshold3=2.0, threshold4=3.0)`; thresholds strictly increasing | `open, high, low, close` | `vector` (list of ints), `value` (a `FuzzyCandle`) | signal `fuzzy_candle`, atlas |
| `FuzzyCandle` (`fuzzy_candlesticks.pyx:34`) | `(direction, size, body_size, upper_wick_size, lower_wick_size)` | n/a, a value object | the five fields | via `FuzzyCandlesticks.value` |
| `CandleDirection`, `CandleSize`, `CandleBodySize`, `CandleWickSize` (`fuzzy_enums.pyx`) | enums | n/a | `FuzzyCandle`'s field types | via `FuzzyCandle` |
| `BookImbalanceRatio` (`nautilus_trader.core.nautilus_pyo3`, `crates/indicators/src/book/imbalance.rs`) | `()` | `handle_book(OrderBook)` or `update(best_bid_qty, best_ask_qty)` | `value`, `count` | not reached yet: needs an L2 book data kind (section 6) |

`nautilus_trader/examples/indicators/ema_python.py:33` shows how to write a pure-Python indicator
(`PyExponentialMovingAverage(Indicator)`, implementing the three handlers). The platform's own
precedent is section 2.

---

## 2. Platform indicators (`kernel/indicators.py`, `kernel/candle_patterns.py`, `kernel/ta.py`)

Subclasses of the Nautilus `Indicator` in the `kernel` context, fed from the 1 s snapshot rows or
bars (platform/CLAUDE.md SIGNAL-01). Only a custom indicator with no Nautilus built-in lives here.
Line numbers are as of Story 33.11.

| Name | Constructor | Inputs and outputs | Reached here by |
|------|-------------|--------------------|-----------------|
| `OnlineLogisticTrend` (`:73`) | `(lookback=5, learning_rate=0.05)` | `update_raw(close)` or `handle_bar`; `value` = P(next return > 0), 0.5 until initialized | signal `logistic_trend` |
| `Microprice` (`:148`) | `()` | `update_raw(bid_price, bid_size, ask_price, ask_size)` or quote tick; `value` | `OFIStrategy`, atlas snapshot section |
| `OrderFlowImbalance` (`:189`) | `(window=50)` | `update_raw(bid_price, bid_size, ask_price, ask_size)`; `value` | atlas snapshot section |
| `RollingZScore` (`:266`) | `(window)` | `update_raw(value)`; population z-score over the window, 0.0 while fewer than 2 readings | `MultiLevelOFI(zscore_window=...)`, research OBI z-score |
| `MultiLevelOBI` (`:322`) | `(levels=10)` | `update_raw(bid_sizes, ask_sizes)`; `value` in 0..1 | `OFIStrategy`, atlas |
| `MultiLevelOFI` (`:360`) | `(levels=10, window=50, usd_notional=False, zscore_window=None)` | `update_raw(bid_prices, bid_sizes, ask_prices, ask_sizes)`; `value`; `clear_prev_state()` | `OFIStrategy`, atlas |
| `LiquidationCascade` (`:508`, Story 33.14) | `(window_s, baseline_s, intensity_threshold, decay_ratio)` | `update_liquidation(side, notional_units, ts_ns)` (a `Liquidation`'s `ts_init` and integer notional) and `advance(ts_ns)`; `rate_long`/`rate_short` (units/s over `window_s`), `baseline` (the continuous-time EMA of the window rate, integrated analytically between breakpoints and bias-corrected by `1 - exp(-elapsed / baseline_s)` since the first update, so it is the weighted mean rate from the first second of the warm-up on; floored at `BASELINE_FLOOR` (`:505`) = 1 unit/s, a division-by-zero guard only), `intensity`, `active`, `direction` (-1: longs liquidated), `rising`, `peak_rate`, `spent`, `episode_*` | `LiquidationCascadeStrategy` (backtest and paper bot), `research.application.liquidations.replay_cascade`, notebook 08 |

`OFI_GAP_NS = 3_000_000_000` (`:70`): a gap between two consecutive snapshots longer than 3 s
makes the OFI discard its previous book (`clear_prev_state`), so a hole in the archive never
produces a fake flow spike.

Stateless helpers (plain functions over a decoded snapshot dict, or a bar's integer units):
`microprice` (`:731`), `spread` (`:754`), `mid_price` (`:775`), `volume_delta` (`:784`),
`trade_aggregates` (`:789`), `organic_delta_units` (`:813`), `units_ratio` (`:827`), `bar_vwap`
(`:842`), `snapshot_depth` (`:886`, returns a `DepthProfile` (`:867`)), `cumulative_depth` (`:905`),
`depth_within_bps` (`:931`), `basis_bps` (`:964`), `funding_annualised` (`:977`) and `pct_change`
(`:989`). `views/` calls them (SSOT-01); a notebook never re-implements them. (`liquidity_distance`
was deleted in Story 33.11: nothing called it.)

`kernel/candle_patterns.py`: `CandlePattern(pattern, *, body_ratio, shadow_ratio,
doji_body_ratio, marubozu_shadow_ratio, tweezer_ratio, trend_bars, star_gap)` (`:521`) is one
`Indicator` per pattern, `value` is `BULLISH = 100`, `BEARISH = -100` or `NO_PATTERN = 0`
(`:129-131`). `PatternName` (`:136`) has 22 members. `CandlePatternSet(thresholds=None)` (`:617`)
runs all 22 over one bar stream and `fired` lists the non-zero ones. `MAX_PATTERN_BARS = 3`.
Reached by `CandlePatternStrategy` and notebook 06. On the chart a pattern's hits draw as markers
on the candles by default, or as the +-100 pane (`style.value.display`, `docs/DATA_DICTIONARY.md`
§2.19).

### 2.1 Custom, no built-in: `kernel/ta.py` (Story 33.11)

The indicators TradingView users reach for first that `nautilus_trader.indicators` lacks (checked:
`DirectionalMovement` gives the smoothed +-DM only and never sets its `value`). Each reuses the
Nautilus piece that exists rather than re-implementing it. Formulas and sources: the module
docstring; tests: `kernel/tests/test_ta.py`; read models: `docs/DATA_DICTIONARY.md` §2.19.

| Name | Constructor | Inputs and outputs | Reuses | Reached here by |
|------|-------------|--------------------|--------|-----------------|
| `Supertrend` (`:185`) | `(period=10, multiplier=3.0)` | `update_raw(high, low, close)` or `handle_bar`; `value`, `direction` (+1 up / -1 down, starting at -1 as TradingView's `ta.supertrend`), `upper`, `lower` | `AverageTrueRange(WILDER)` | picker `Supertrend` (`up`/`down`), signal `supertrend` |
| `ParabolicSAR` (`:255`) | `(step=0.02, max_step=0.2)` | `update_raw(high, low)` or `handle_bar`; `value` (the bar's stop), `is_long` | -- | picker (plot `points`), signal `parabolic_sar` |
| `AverageDirectionalIndex` (`:354`) | `(period=14)` | `update_raw(high, low, close)` or `handle_bar`; `adx`, `plus_di`, `minus_di` | `DirectionalMovement(WILDER)`, `AverageTrueRange(WILDER)`, `MovingAverageFactory` WILDER | picker, signal `adx` |
| `WilliamsPercentR` (`:409`) | `(period=14)` | `update_raw(high, low, close)` or `handle_bar`; `value` in -100..0 | -- | picker |
| `PivotPoints` (`:477`) | `(kind="standard")`, `kind` one of `PIVOT_KINDS` | `update_raw(high, low, close, session)` (an opaque session key; no `handle_bar`); `pp`, `r1`..`r4`, `s1`..`s4`, `levels()`; `pivot_levels(kind, h, l, c)` (`:452`) the one formula | -- | picker `PivotPoints` (store-seeded, `session` `D`/`W`) |
| `MoneyFlowIndex` (`:545`) | `(period=14)` | `update_raw(high, low, close, volume)` or `handle_bar`; `value` in 0..100 | -- | picker, signal `mfi` |
| `ChaikinMoneyFlow` (`:600`) | `(period=20)` | `update_raw(high, low, close, volume)` or `handle_bar`; `value` in -1..1 | -- | picker, signal `cmf` |
| `AwesomeOscillator` (`:648`) | `(fast=5, slow=34)` | `update_raw(high, low)` or `handle_bar`; `value` | `SimpleMovingAverage` | picker (histogram), signal `awesome_oscillator` |
| `ZigZag` (`:696`) | `(deviation_pct=5.0)` | `update_raw(high, low)` or `handle_bar`; `pivot_price`/`pivot_bar`/`confirmed` (a pivot confirmed by this update), `extreme_price`/`extreme_bar` (the repainting last leg), `direction` | -- | picker `ZigZag` (plot `swing`, note "repaints last leg"); not a signal (it repaints) |

`LiquidationCascade` has no Nautilus counterpart: no built-in indicator takes liquidations, an
event stream rather than bars or quotes, and its value must not depend on how often it is updated:
the EMA integrates every window expiry in order, analytically, so it is independent of the update
frequency and a backtest's and a live bot's 1 s timer agree up to float rounding (bot parity
compares floats with `REL_TOL`; audit D-176). Without the bias correction the EMA, started at 0,
would hold ~63 % of the mean rate when `initialized` turns on after one `baseline_s`, inflating
`intensity` ~1.6x and opening false episodes after every start or restart; with it a steady rate
reads intensity ~1 right after the warm-up. `BASELINE_FLOOR` is one unit (`10^-(price_precision +
size_precision)` of the quote) per second: it only keeps `intensity` finite after a long silence and
is not below every real rate (one unit in a 30 s window is 1/30 unit/s), but it is negligible in
quote terms; the guard against a tiny liquidation after silence opening an episode is the strategy's
`min_episode_notional`. Its strategy, `research/strategies/liquidation_cascade_strategy.py`, takes
every decision through the pure `cascade_rules.should_enter`/`should_exit` (a follow entry needs the
current `direction` to be the episode's; a fade entry needs the episode spent and the rate not
`rising` again), runs in a backtest through `RunSpec(data="liquidations")` (the derived quotes plus
the archive's `custom_liquidation` rows, `BacktestDataConfig(client_id="LIQUIDATIONS")`;
`research/strategies/backtest_liquidation_cascade.py`, the four gallery runs of notebook 08) and as
a paper bot (`strategy = "liquidation_cascade"`, fed live by the bots' `LIQUIDATIONS` data client).
Bybit LINEAR ids only.

---

## 3. Upstream example strategies (`nautilus_trader/examples/strategies/`)

18 strategy files. Every config that has `instrument_id`, `bar_type` and `trade_size` receives
exactly what `research.application.backtest_runner.build_run_config` injects
(`backtest_runner.py:193-195`): `instrument_id` and `order_id_tag` (inherited from
`StrategyConfig`) for every data kind, plus `bar_type` (`<iid>-<spec>-LAST-INTERNAL`) for a
`bars:<spec>` kind. So they run by string path as
`strategy_path="nautilus_trader.examples.strategies.<file>:<Class>"`,
`config_path="...:<Class>Config"`, `data="bars:1-MINUTE"`, `params={"trade_size": "0.01", ...}`
(`trade_size` is a string, `Decimal` in the config, NAUT-01).

The `bars:<spec>` kind streams `TradeTick`s only (`backtest_runner.py:155`) and Nautilus builds
the bars; the `seconds` kind streams `DydxSecondSnapshot` plus quotes derived from the top of
book (`backtest_runner.py:139-153`) and injects no `bar_type`. No single kind gives bars and
quotes together (follow-up in section 6), which decides the last column.

| File | Class / config (`:line`) | Indicators | Subscribes | Logic | Run here as |
|------|--------------------------|-----------|-----------|-------|-------------|
| `ema_cross.py` | `EMACross` / `EMACrossConfig` (`:46`) | 2 EMA | bars, trade ticks (quotes optional) | Market buy on fast over slow, sell on under; flips. | `bars:1-MINUTE`; required `trade_size`; optional `fast_ema_period=10`, `slow_ema_period=20` |
| `ema_cross_long_only.py` | `EMACrossLongOnly` (`:45`) | 2 EMA | bars, trade ticks | Buy on fast over slow, close on under; never short. | `bars:1-MINUTE`; required `trade_size` |
| `ema_cross_bracket.py` | `EMACrossBracket` (`:45`) | 2 EMA, ATR | bars, quotes | Bracket order list: limit-if-touched entry at the last close (30 s GTD), stop loss and take profit at `bracket_distance_atr` x ATR (`:77`, `:206-220`). | `bars:1-MINUTE`; required `trade_size`; optional `atr_period=20`, `bracket_distance_atr=3.0` |
| `ema_cross_trailing_stop.py` | `EMACrossTrailingStop` (`:52`) | 2 EMA, ATR | bars, quotes | Market entry, then a trailing stop at `ATR x trailing_atr_multiple`; reads `cache.quote_tick` (`:303`) and refuses to place the stop without a quote. | needs bars **and** quotes: not runnable on either kind today (the stop is never placed on `bars:`); required `trade_size`, `atr_period`, `trailing_atr_multiple`, `trailing_offset_type`, `trigger_type` |
| `ema_cross_stop_entry.py` | `EMACrossStopEntry` (`:51`) | 2 EMA, ATR | bars, quotes, trade ticks | Market-if-touched entry two ticks beyond the last bar's high or low (IOC, `:264-270`), then a trailing stop. | `bars:1-MINUTE`; required `trade_size`, `atr_period`, `trailing_atr_multiple`, `trailing_offset_type`, `trailing_offset`, `trigger_type` |
| `ema_cross_twap.py` | `EMACrossTWAP` (`:48`) | 2 EMA | bars, quotes | Market orders handed to the `TWAP` exec algorithm (`twap_horizon_secs=30`, `twap_interval_secs=3`, `:78-79`). | `bars:1-MINUTE` plus `RunSpec.exec_algorithms=("nautilus_trader.examples.algorithms.twap:TWAPExecAlgorithm",)`; required `trade_size` |
| `ema_cross_bracket_algo.py` | `EMACrossBracketAlgo` (`:48`) | 2 EMA, ATR | bars, quotes | The bracket of `ema_cross_bracket` with an exec algorithm per leg (`entry_/sl_/tp_exec_algorithm_id` and `_params`, `:96-101`). | like TWAP: `exec_algorithms=(...)` plus `entry_exec_algorithm_id` etc. in `params`; required `trade_size` |
| `ema_cross_hedge_mode.py` | `EMACross` / `EMACrossConfig` (`:45`) | 2 EMA | bars, trade ticks | Same cross, but submits with explicit `-LONG` / `-SHORT` position ids (`:259,273`). | not runnable: needs a HEDGING venue; `build_run_config` creates a NETTING one (`backtest_runner.py:215`) |
| `bb_mean_reversion.py` | `BBMeanReversion` (`:78`) / config (`:40`) | `BollingerBands`, `RelativeStrengthIndex` | bars | Buy at the lower band with RSI below `rsi_buy_threshold`, sell at the upper band with RSI above `rsi_sell_threshold`, exit at the middle band. | `bars:1-MINUTE`; required `trade_size`; optional `bb_period=20`, `bb_std=2.0`, `rsi_period=14`, `rsi_buy_threshold=0.30`, `rsi_sell_threshold=0.70` |
| `volatility_market_maker.py` | `VolatilityMarketMaker` (`:49`) | ATR | bars, quotes, trade ticks | Cancels and re-quotes a limit buy and sell at bid minus and ask plus `ATR x atr_multiple` on every quote (`cache.quote_tick`, `:272,343`). | needs bars **and** quotes: neither kind; required `trade_size`, `atr_period`, `atr_multiple` |
| `grid_market_maker.py` | `GridMarketMaker` (`:53`) | none | quotes | Post-only limit grid of `num_levels` around the mid, re-quoted past `requote_threshold_bps`, inventory skew. | `seconds` kind (quotes, no `bar_type` needed); required `max_position` (a `Quantity`, pass a string) |
| `simpler_quoter.py` | `SimpleQuoterStrategy` (`:27`) | none | quotes | One limit buy and sell at the top of book, offset by `tob_offset_ticks`. | `seconds` kind; no required fields besides `instrument_id` (`order_qty=1`) |
| `orderbook_imbalance.py` | `OrderBookImbalance` (`:40`) | none | L2 deltas, or quotes when `use_quote_ticks=True` (`:73`) | FOK limit when the smaller/larger top size ratio reaches `trigger_imbalance_ratio`. | `seconds` kind with `use_quote_ticks=True`; required `max_trade_size`; the L2 form is a follow-up |
| `market_maker.py` | `MarketMaker` | none | L2 deltas (`:80`) | Quotes around the book mid with an inventory adjustment. Plain constructor args (`instrument_id`, `trade_size`, `max_size`), no config class. | not runnable: needs an L2 book and cannot be built by `ImportableStrategyConfig` |
| `market_buy_on_start.py` | `MarketBuyOnStart` (`:38`) | none | quotes | One market buy on the first quote; optional rebuy after close. Custom `__init__` config (`instrument_id`, `trade_size` int, `rebuy_after_close`). | a smoke test, not a strategy to compare; excluded from the gallery |
| `signal_strategy.py` | `SignalStrategy` (`:28`) | none | trade ticks, quotes | Publishes a counter signal on every tick; never trades. | demonstration of `publish_signal`; excluded |
| `subscribe.py` | `SubscribeStrategy` (`:36`) | none | configurable (book, trades, quotes, bars, index prices) | Subscribes and logs; never trades. | data-plumbing check; excluded |
| `blank.py` | `MyStrategy` (`:41`) | none | none | Template with no logic. | template; excluded |

`ema_cross_hedge_mode.py` defines its own `EMACross` and `EMACrossConfig`, the same names as
`ema_cross.py`, so a `strategy_path` and `config_path` must always name the same file.

Algorithms: `nautilus_trader/examples/algorithms/twap.py:53` (`TWAPExecAlgorithm`, config
`TWAPExecAlgorithmConfig` at `:35`) and `blank.py`. `RunSpec.exec_algorithms` takes the
`module:Class` path and resolves the `<Class>Config` beside it.

---

## 4. Backtest patterns (`docs/concepts/backtesting.md`, `examples/backtest/`)

### 4.1 API levels and runs

| Pattern | Upstream example | Here |
|---------|------------------|------|
| Low-level `BacktestEngine`: add venue, instruments, wrangled data, strategies; `engine.run()` | `examples/backtest/fx_ema_cross_audusd_ticks.py`, `crypto_ema_cross_ethusdt_trade_ticks.py`, `docs/getting_started/backtest_low_level.py` | Excluded by platform/CLAUDE.md NAUT-03 (no custom simulation script). |
| Repeated runs on one engine: `engine.reset()` then `clear_strategies()` and re-add (`backtesting.md:231-320`; 18 of the example scripts call `engine.reset()`) | the optimisation loops of the `fx_*` and `databento_*` scripts | `NodeRunner.sweep`: one node, one `BacktestRunConfig` per grid point. The docs themselves recommend `BacktestNode` for this (`backtesting.md:262`). |
| High-level `BacktestNode` + `BacktestRunConfig` + `BacktestDataConfig` + `ImportableStrategyConfig` | `docs/getting_started/backtest_high_level.py`, `examples/backtest/model_configs_example.py`, `tardis_option_chain.py:339` | Covered: `NodeRunner` builds exactly this (`backtest_runner.py:178-225`). |
| Parameter sweep | none upstream (grid loops in the low-level scripts) | `NodeRunner.sweep`, notebook 04. |
| Walk-forward, Monte Carlo | none upstream | Notebooks 04 and 05. |
| Wranglers (`QuoteTickDataWrangler`, `TradeTickDataWrangler`, `BarDataWrangler`, `OrderBookDeltaDataWrangler`) | the `crypto_*`, `fx_*` scripts | Not used: the archive is already a `ParquetDataCatalog` of Nautilus types. |

### 4.2 Execution models

Latency (`LatencyModelConfig`, `backtest/config.py:530-551`): four non-negative ints in
nanoseconds, `base_latency_nanos` (default one second), `insert_`, `update_` and `cancel_latency_nanos`
(default 0). Example: `examples/backtest/model_configs_example.py:65-69`.
Here: `RunSpec.latency_ms` is the shortcut (sets `base_latency_nanos`); `RunSpec.latency` takes
any of the four and replaces the shortcut.

Fill models, 11 classes in `backtest/models/fill.pyx`. Only the base `FillModel` has a
`config=` constructor argument (`:58-64`); only `FillModelConfig` exists as a config class
(`backtest/config.py:457`). `CompetitionAwareFillModel` also has `liquidity_factor` (`:876`).

| `RunSpec.fill_model["name"]` | Class (`fill.pyx`) | Behaviour | Own config class |
|------------------------------|--------------------|-----------|------------------|
| `fill` | `FillModel` (`:34`) | Probabilistic queue position (`prob_fill_on_limit`) and one-tick slippage (`prob_slippage`), optional `random_seed`. | `FillModelConfig` |
| `best_price` | `BestPriceFillModel` (`:170`) | Fills at the best price with unlimited liquidity. | `FillModelConfig` (shared) |
| `one_tick_slippage` | `OneTickSlippageFillModel` (`:244`) | Exactly one tick of slippage on every order. | shared |
| `two_tier` | `TwoTierFillModel` (`:296`) | 10 contracts at best, remainder one tick worse. | shared |
| `three_tier` | `ThreeTierFillModel` (`:596`) | 50/30/20 across three levels. | shared |
| `probabilistic` | `ProbabilisticFillModel` (`:364`) | 50% best price, 50% one tick worse. | shared |
| `size_aware` | `SizeAwareFillModel` (`:431`) | Different execution for orders up to 10 and above 10. | shared |
| `limit_order_partial` | `LimitOrderPartialFillModel` (`:527`) | At most 5 contracts fill per price touch. | shared |
| `market_hours` | `MarketHoursFillModel` (`:686`) | Wider spreads in low-liquidity periods; the flag is a stub (`_is_low_liquidity = False`, `:701`). | none, own `__init__` (`:695`) |
| `volume_sensitive` | `VolumeSensitiveFillModel` (`:776`) | Depth from recent volume; `_recent_volume` fixed at 1000 until `set_recent_volume` (`:793`). | none, own `__init__` (`:785`) |
| `competition_aware` | `CompetitionAwareFillModel` (`:862`) | Only `liquidity_factor` (0.3) of visible liquidity is available. | none, own `__init__` (`:871`) |

"shared" means the model inherits the base `__init__` and accepts `FillModelConfig`. The last
three do not (pitfall 3 in section 5), so `RunSpec` builds them through
`research.application.fill_adapters`. The probabilistic parameters apply to every model that
calls the base constructor.

Fee models (`backtest/config.py:606-700`, classes in `backtest/models/fee.pyx`):

| `RunSpec.fee_model["name"]` | Class | Config | Notes |
|-----------------------------|-------|--------|-------|
| `maker_taker` (default) | `MakerTakerFeeModel` | `MakerTakerFeeModelConfig` (`:612`) | Uses the instrument's maker and taker fees; no parameters. |
| `fixed` | `FixedFeeModel` | `FixedFeeModelConfig` (`:621`) | `commission` (money string), `charge_commission_once=True`. |
| `per_contract` | `PerContractFeeModel` | `PerContractFeeModelConfig` (`:638`) | `commission` per contract. Used by `synthetic_data_pnl_test.py:165`. |

Exec algorithms: `RunSpec.exec_algorithms` (`ImportableExecAlgorithmConfig`,
`nautilus_trader/execution/config.py:131`). Upstream wires one with
`engine.add_exec_algorithm(TWAPExecAlgorithm())` (`crypto_ema_cross_ethusdt_trade_ticks.py:93`).

### 4.3 Book types and data

Venue `book_type` decides which data moves the simulated book (`backtesting.md:388-415`):

| Data | `L1_MBP` (default) | `L2_MBP` | `L3_MBO` |
|------|--------------------|----------|----------|
| `QuoteTick` | updates book | ignored | ignored |
| `TradeTick` | triggers matching | triggers matching | triggers matching |
| `Bar` | updates book | ignored | ignored |
| `OrderBookDelta` / `OrderBookDeltas` | ignored | updates book | updates book |
| `OrderBookDepth10` | updates book | updates book | updates book |

Strategies still receive every subscribed data type whatever the book type
(`backtesting.md:394-396`). Here the venue is L1 only (`build_run_config` sets no `book_type`):
quotes (`seconds` kind) or trades (`trades`, `bars:` kinds) move it. Upstream L2 examples:
`crypto_orderbook_imbalance.py:53`, `betfair_backtest_orderbook_imbalance.py:53`. The L2 kind is a
follow-up (section 6).

| Pattern | Upstream example | Here |
|---------|------------------|------|
| Internal bars from ticks: `-LAST-INTERNAL` bar types aggregated by the data engine | `fx_ema_cross_bracket_gbpusd_bars_internal.py:98` (5-minute internal bars from quotes) | `RunSpec.data="bars:<step>-<aggregation>"` (`-LAST-INTERNAL`, built from the raw trade archive). `DataEngineConfig.time_bars_build_delay` is documented at `backtesting.md:1183`. |
| External bars: `BacktestDataConfig.bar_spec` / `bar_types` (`config.py:251-253`), type `...-EXTERNAL` | `fx_ema_cross_bracket_gbpusd_bars_external.py:89-93` | Only `archive.backfill_bars` writes `data/bar/` (Bybit and Hyperliquid klines, f64 round trip, audit D-52). Not wired into `RunSpec`; the candle-store kind `store_bars:<seconds>` is a follow-up. |
| Quotes and trades together | `crypto_ema_cross_ethusdt_trade_ticks.py` (trades), `fx_*_ticks.py` (quotes) | One kind at a time. `fills="quotes"` beside trades is a follow-up (it would unlock the two bars-and-quotes strategies of section 3). |
| Streaming (`chunk_size`) | `tardis_option_chain.py:321`; `config.py:414,443`; `node.py:478-504`; `backtesting.md:100-137` | Not used. `Known limit:` the custom `DydxSecondSnapshot` cannot stream; upgrade path: stream the trades-only kinds, which are pure Nautilus types. |
| Multi-instrument | upstream strategy per instrument | `RunSpec.instrument_ids`: one strategy per instrument, `order_id_tag` = its position. One venue per run. |
| Large data loading (`add_data(sort=False)`, `add_data_iterator`) | `backtesting.md:41-135` | Not applicable: the node streams from the catalog. |

### 4.4 Reports and statistics

| Report | Upstream | Here |
|--------|----------|------|
| Orders | `Trader.generate_orders_report` (`trading/trader.py:843`) | `RunResult.orders`, `evaluation.orders_frame` |
| Order fills | `generate_order_fills_report` (`:854`) | `RunResult.fills`, `evaluation.fills_frame` (slippage distribution in notebook 08) |
| Positions | `generate_positions_report` (`:876`) | `RunResult.trades` (closed trades) |
| Account | `generate_account_report` (`:891`) | `RunResult.equity` |

`PortfolioAnalyzer` statistics: `nautilus_trader/analysis` exports 25 statistic classes (Rust, from
`nautilus_trader.core.nautilus_pyo3`). `portfolio.pyx:173-189` registers 17 by default: MaxWinner,
AvgWinner, MinWinner, MinLoser, AvgLoser, MaxLoser, Expectancy, WinRate, ReturnsVolatility,
ReturnsAverage, ReturnsAverageLoss, ReturnsAverageWin, SharpeRatio, SortinoRatio, ProfitFactor,
RiskReturnRatio, LongRatio. The other 8 are registered by the runner after `node.build()` and
before `node.run()`: `CAGR`, `Alpha`, `BetaRatio`, `CalmarRatio`, `InformationRatio`,
`MaxDrawdown`, `TrackingError`, `TreynorRatio`. They land in `nautilus_stats["returns"]` (`RunResult`;
e.g. `CAGR (252 days)`, `Calmar Ratio (252 days)`, `Max Drawdown`), not in `"general"`, which holds only `Long Ratio`;
shown by `evaluation.nautilus_stats_frame`. The platform `MetricReport` stays exactly
`kernel.performance_metrics.all_metrics`: one formula per metric (SSOT-02), the Nautilus numbers
are a cross-check beside it, never a replacement.

Tearsheet: `nautilus_trader.analysis.tearsheet.create_tearsheet` (`:283`) and
`create_tearsheet_from_stats` (`:584`), used at `crypto_ema_cross_ethusdt_trade_ticks.py:118`.
Wired as every backtest report's `tearsheet.html` (`research.application.backtest_report`,
2026-10-08: `NodeRunner(report_root=...)` calls `create_tearsheet` on the live engine before
`node.dispose()`; a report saved later uses `create_tearsheet_from_stats`). plotly is pinned at
6.8.0 in `uv.lock` (the `visualization` extra and the dev group), above the 6.3.1 the tearsheet
needs. Two upstream traps it
works around: `register_chart` + `TearsheetCustomChart` alone draws nothing (the tearsheet renders
only names in the internal `_TEARSHEET_CHART_SPECS`, filled by `_register_tearsheet_chart`, and
skips any other name silently), and the automatic layout has eight slots, dropping later panels,
so the report passes an explicit `GridLayout`.

### 4.5 Data loaders and examples with no data here

Excluded because the archive holds none of the data: Databento (`databento_*`, `notebooks/databento_*`),
Betfair, Polymarket (`polymarket_simple_quoter.py`), Tardis options (`tardis_option_chain.py`),
Architect (`architect_ax_*`), FX tick and bar CSVs (`fx_*`, `example_01`), and the FX rollover
module. BitMEX and Binance examples (`bitmex_grid_market_maker.py`,
`crypto_ema_cross_with_binance_provider.py`) need a live venue provider. `liquidation_demo.py` and
`synthetic_data_pnl_test.py` use synthetic data and test the engine, not strategies.

### 4.6 The tutorial series (`examples/backtest/example_01..11`)

| Example | Shows | Where the same thing lives here |
|---------|-------|---------------------------------|
| `example_01_load_bars_from_custom_csv` | Wrangle a CSV into bars. | Not needed: the archive is a catalog. |
| `example_02_use_clock_timer` | Strategy timers beside market data. | Strategy API, available to any research strategy. |
| `example_03_bar_aggregation` | Higher-timeframe bars from lower ones. | `bars:<spec>` data kind (internal bars). |
| `example_04_using_data_catalog` | Read a `ParquetDataCatalog` in a backtest. | `ParquetDataCatalog` via `NodeRunner`. |
| `example_05_using_portfolio` | `Portfolio` position and PnL queries. | Available in any strategy; the report is `RunResult`. |
| `example_06_using_cache` | Cache queries for bars, orders, positions. | Strategy API (`cache.quote_tick`, `orders_open`). |
| `example_07_using_indicators` | `register_indicator_for_bars` (`strategy.py:72`). | `MACrossStrategy` registers its MAs and ATR this way. |
| `example_08_cascaded_indicator` | An EMA fed by another EMA's values (`strategy.py:46-58`). | Pattern for a derived indicator; not a platform strategy yet. |
| `example_09_messaging_with_msgbus` | Custom events on the message bus. | Not used; the platform logs through `observability`. |
| `example_10_messaging_with_actor_data` | `publish_data(DataType(...))` (`strategy.py:132`). | Not used. |
| `example_11_messaging_with_actor_signals` | `publish_signal` (`strategy.py:76`). | Same API as `SignalStrategy` (section 3); not used. |

---

## 5. Upstream pitfalls found

1. **`MovingAverageFactory.create` has no `ADAPTIVE` branch.** `MovingAverageType.ADAPTIVE`
   (`averages.pxd:30`) is a valid enum value and `AdaptiveMovingAverage` exists, but the factory's
   if-chain (`averages.pyx:931-950`) ends after `VARIABLE_INDEX_DYNAMIC` with no `else`, so the call
   silently returns `None` and the first `register_indicator_for_bars` or `update_raw` on it fails
   far from the cause. Several indicators that take `ma_type` build it through this factory
   (`RelativeStrengthIndex`, `Stochastics`, `CommodityChannelIndex`, ...), so `ma_type=ADAPTIVE` is
   unusable there too. `MACrossStrategy` constructs `AdaptiveMovingAverage` explicitly.
2. **`examples/backtest/tardis_option_chain.py` passes `data_type=` to `BacktestDataConfig`**
   (`:303` and `:308`). The field is `data_cls` (`backtest/config.py:241`); `data_type` is a
   read-only property returning the resolved class (`:257`). The frozen config does not accept the
   keyword, so the example as shipped would not construct its data configs. Use `data_cls`.
3. **`FillModelFactory` cannot build three of the eleven fill models.** It always calls
   `fill_model_cls(config=config_obj)` (`backtest/config.py:527`), but `MarketHoursFillModel`
   (`fill.pyx:695`), `VolumeSensitiveFillModel` (`:785`) and `CompetitionAwareFillModel` (`:871`)
   define their own `__init__` without `config`, so an `ImportableFillModelConfig` naming them
   raises `TypeError`. The platform wraps them in `research/application/fill_adapters.py`.
   `CompetitionAwareFillModel.liquidity_factor` is then not reachable from a config.
4. **`MarketHoursFillModel` and `VolumeSensitiveFillModel` are demonstration stubs.** The first
   never leaves its fixed `_is_low_liquidity = False` (`fill.pyx:701`), the second holds
   `_recent_volume = 1000.0` (`:793`) until a caller sets it. Choosing either changes nothing
   unless the run feeds those values; do not read their leaderboard rows as evidence.
5. **Several shipped strategies need quotes the bars kind does not provide.**
   `EMACrossTrailingStop` (`:303`) and `VolatilityMarketMaker` (`:272,343`) read
   `cache.quote_tick` and skip the order when there is none, so on `bars:` data they run and never
   trade. Judge such a row by its trade count, never by a flat equity curve alone.
6. **At `trade_size=0.01` on an L1 trade-tick venue the tiered, size-aware and competition fill
   models are indistinguishable from each other.** `best_price`, `two_tier`, `three_tier`,
   `size_aware`, `limit_order_partial`, `market_hours`, `volume_sensitive` and `competition_aware`
   all produced identical slippage on the fixture (+0.104 bps mean): the synthetic book holds one
   level, and an order of 0.01 contracts never reaches a second tier. Only `fill`,
   `one_tick_slippage` and `probabilistic` differ. Identical gallery rows are therefore one
   modelling choice, not independent hypotheses.

---

## 6. Follow-ups (not built)

| Follow-up | Unlocks |
|-----------|---------|
| L2 `book` data kind from `kernel.snapshot_book.snapshot_deltas` (one `OrderBookDeltas` per stored row, `CLEAR` then the levels), venue `book_type=L2_MBP` | `orderbook_imbalance` (L2 form), `market_maker`, `BookImbalanceRatio`. |
| `store_bars:<seconds>` external bars read from the candle store | Backtests on the platform's own bars, not Nautilus-aggregated ones. |
| `fills="quotes"` beside trades (bars plus quotes in one run) | `EMACrossTrailingStop`, `VolatilityMarketMaker`. |
| `chunk_size` streaming for the trades-only kinds | Windows larger than memory. |
| A catalog-completeness test over `nautilus_trader.indicators.__all__` | Fails when a Nautilus bump adds an indicator this document and the atlas do not list. |
| Paper-bot registration of the family strategies in `bots/infrastructure/nautilus_host.py` `STRATEGIES` (`:117`) | Running `MACrossStrategy` and `IndicatorSignalStrategy` live-paper. |
