# Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and `live_paper`

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1, 27.5 and 27.7. Touches `live_paper/` (Bot Operations, spine AD-8/AD-10/AD-11 and AD-D15). Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.8".

## Story

As a trader,
I want a strategy that trades candlestick pattern signals with a trend filter and a defined exit, runnable by string path in a backtest, evaluated by the 27.5 notebook, and startable as a paper bot,
so that a pattern I found in the scanner is one config file away from a backtest and one more from a paper bot, on the same detector the scanner used.

## Acceptance Criteria

1. **Given** `kernel.candle_patterns.CandlePattern` and the `StrategyConfig` + `Strategy` conventions in `research/BACKTESTING.md`
**When** the story ships
**Then** `research/strategies/candle_pattern_strategy.py` holds `CandlePatternStrategyConfig(StrategyConfig, frozen=True)` (`instrument_id`, `bar_type` as a Nautilus bar-spec string, `long_patterns` and `short_patterns` as tuples of pattern names, `trend_ema_period` and `trend_condition` `above | below | any` matching the scanner's filter, `trade_size`, `exit_bars`, `stop_atr_multiple` with `atr_period`, `allow_short`) and `CandlePatternStrategy(Strategy)`, which subscribes the bar type, feeds one `CandlePattern` per configured pattern plus the EMA and ATR through `handle_bar`/`update_raw`, enters at the next bar's open on a fired pattern that passes the trend filter, exits after `exit_bars` bars or on the ATR stop or on an opposite-direction pattern, holds at most one position, and never imports collector, views or `data_api` code (DESIGN-02, `test_boundaries.py`); internal bar aggregation from `TradeTick` is the default (so every venue with a raw trade archive works), with `EXTERNAL` catalog bars used when the config's bar type says so

2. **Given** `BacktestRunner` and the evaluation notebook from 27.5
**When** `research/strategies/backtest_candle_pattern.py` runs (`run()` returning a `RunResult`, `__main__` printing the `MetricReport`)
**Then** it runs `CandlePatternStrategy` by `ImportableStrategyConfig` string path over a bounded window on `BacktestNode` + `BacktestDataConfig(data_cls=TradeTick)` (NAUT-03), `04_backtest_evaluation`'s parameters cell lists it as its second worked example (with a `long_patterns = ("HAMMER", "ENGULFING")`, `trend_condition = "above"` default), and `research/tests/test_candle_pattern_strategy.py` proves on a synthetic trade-tick catalog with one planted hammer that exactly one long entry occurs at the bar after the hammer, exits after `exit_bars`, and that no entry occurs when the trend filter fails; real Nautilus objects only (TEST-03)

3. **Given** `live_paper`'s one hard-wired `DummyStrategy` per bot (`live_paper/node.py`, `BotConfig`)
**When** the story ships
**Then** `BotConfig` gains `strategy: str = "dummy"` and `params: dict[str, Any] = {}` (optional keys with defaults, so every existing `config.toml` parses unchanged; the frozen key set is extended, not altered, and `live_paper/README.md` documents the two keys), `node.py` resolves `strategy = "candle_pattern"` to `CandlePatternStrategy` through Nautilus's `StrategyFactory.create(ImportableStrategyConfig(...))` from a string path (the mechanism `BacktestNode` uses; no `live_paper → research` import edge, and `test_boundaries.py` states why), `bot_id` still maps to `order_id_tag`, `bot_status`/`trade_history` need no change because they are strategy-scoped `Cache` reads (AD-11), `live_paper.dockerfile` `COPY`s `research` and `kernel` and `test_images.py` gains an explicit entry for the string-path import (its `ast` walk cannot see a string), `make test-live-paper` covers a `candle_pattern` bot constructing on the sandbox venue, and `docs/BOT_OPERATIONS.md` gains the config example; the story parks `awaiting-operator` with "start one `candle_pattern` paper bot on the VPS and confirm a fill in `bot_tui`" as the operator action

## Tasks / Subtasks

- [ ] Task 1 — strategy (AC: #1)
  - [ ] `research/strategies/candle_pattern_strategy.py`: config fields per AC #1 (`bar_type` default `"<instrument>-1-MINUTE-LAST-INTERNAL"` built from `instrument_id` when omitted; `long_patterns=("HAMMER","ENGULFING","MORNING_STAR")`, `short_patterns=("SHOOTING_STAR","ENGULFING","EVENING_STAR")`, `trend_ema_period=50`, `trend_condition="above"`, `trade_size=Decimal("0.01")`, `exit_bars=10`, `atr_period=14`, `stop_atr_multiple=2.0`, `allow_short=True`); `on_start` subscribes bars (`subscribe_bars(BarType.from_str(...))`), builds one `CandlePattern` per configured name (bullish set and bearish set), an `ExponentialMovingAverage` and an `AverageTrueRange` (Nautilus built-ins), registers them with `register_indicator_for_bars` so Nautilus feeds them; `on_bar` evaluates fired patterns on the closed bar and submits a market order on the next bar (state: `pending_side`), tracks `bars_in_position` and the ATR stop level; exits by bars, stop or opposite pattern; one position at a time; `on_reset` clears state. Every rule ≤ 30 lines (READ-01).
  - [ ] Bar source: `INTERNAL` aggregation from `TradeTick` (the `backtest_dydx.py` pattern) by default; `EXTERNAL` when the bar type says so (the 22.9 catalog bars for Bybit/Hyperliquid).
  - [ ] `test_boundaries.py`: `research.strategies.candle_pattern_strategy` imports `kernel` and `nautilus_trader` only.
- [ ] Task 2 — runner, notebook example, tests (AC: #2)
  - [ ] `research/strategies/backtest_candle_pattern.py`: `run(symbol, start, end, **params) -> RunResult` over `BacktestRunner` (27.1) with `data="trades"`; `__main__` prints `MetricReport.as_table()`; documented in `research/BACKTESTING.md`'s copy table ("Bars from trades, pattern entries → `backtest_candle_pattern.py`").
  - [ ] `04_backtest_evaluation` Parameters cell: second worked example block (commented alternative values for `STRATEGY`/`STRATEGY_CONFIG`/`PARAMS`).
  - [ ] `research/tests/test_candle_pattern_strategy.py`: synthetic `TradeTick` catalog (fixture helpers from 27.2) producing 1 m bars with a planted hammer after a 3-bar down move followed by closes above the EMA (so the trend filter passes) → exactly one long entry at the next bar, exit after `exit_bars`; the same data with `trend_condition="below"` → no entry; one `BacktestNode` construction per test file (sprint action item on the native-crash mitigation).
- [ ] Task 3 — `live_paper` (AC: #3)
  - [ ] `live_paper/config.py`: `BotConfig.strategy: str = "dummy"`, `BotConfig.params: dict[str, Any] = field(default_factory=dict)`; TOML `[[bots]]` with `strategy = "candle_pattern"` and `[bots.params]` table; existing configs parse unchanged (test: the checked-in `config.toml` loads, all bots `strategy == "dummy"`).
  - [ ] `live_paper/node.py`: a `_STRATEGY_PATHS` table `{"dummy": None (DummyStrategy, direct as today), "candle_pattern": ("research.strategies.candle_pattern_strategy:CandlePatternStrategy", "...:CandlePatternStrategyConfig")}`; for a path entry build `ImportableStrategyConfig(strategy_path, config_path, config={instrument_id, order_id_tag=bot_id, **params})` and `StrategyFactory.create(...)` (`nautilus_trader/trading/config.py:124-130`), then `node.trader.add_strategy(...)` exactly as `DummyStrategy` is added; unknown `strategy` → `ValueError` naming the table (DATA-07: no silent fallback to dummy).
  - [ ] `test_boundaries.py`: comment + assertion that `live_paper` has no `research` import (the string path is deliberate; the test states the reason). `test_images.py`: explicit `("live_paper", "research")` and `("live_paper", "kernel")` closure entries because the `ast` walk cannot see a string; `live_paper.dockerfile` `COPY platform/research ./research` and `COPY platform/kernel ./kernel` (kernel is already copied after 23.2 — verify).
  - [ ] `live_paper/tests/test_candle_pattern_bot.py` (runs under `make test-live-paper`): `build_node` with one `candle_pattern` bot on the sandbox venue constructs and the strategy is registered with `order_id_tag == bot_id`.
  - [ ] Docs: `live_paper/README.md` (the two keys, the table of strategies), `docs/BOT_OPERATIONS.md` (config example + the operator action), `docs/DEPLOY_CHECKLIST.md` (live-paper image rebuild because its `COPY` set changed).
  - [ ] Park `awaiting-operator` with the operator action in `sprint-status.yaml`.

## Dev Notes

- **Why string path in `live_paper`:** `live_paper/node.py:33` explains today's direct-class pattern (mirrors `examples/sandbox/dydx_sandbox.py`). Adding a second strategy by direct import would create a `live_paper → research` edge that the spine's context map does not have. `StrategyFactory.create(ImportableStrategyConfig)` is Nautilus's own resolver and is what `BacktestNode` uses, so the same string path works in both places; the boundary test can't see it, hence the explicit `test_images.py` entries and the comment.
- **Bots context (25.3) may move `live_paper` under `bots/` before or after this story.** Write against `BotConfig`/`build_node` as they are; if 25.3 has landed, the same fields live on the `PaperFleet` bot spec and `NautilusHost` does the `add_strategy`. Record which in Completion Notes.
- **Frozen config key sets:** MR1 freezes venue `config.toml` key sets; `live_paper/config.toml` is extended with optional keys that default, so every existing file parses unchanged. Say so in the README.
- **One position, next-bar entry:** the pattern fires on the closed bar; the order goes at the next bar (no look-ahead). The test's planted hammer must produce the entry on bar `t+1`, not `t`.
- **Indicators via `register_indicator_for_bars`:** Nautilus feeds registered indicators before `on_bar`; `CandlePattern.handle_bar` must exist (27.7) for this to work.
- **Project rules:** NAUT-03, DESIGN-02, DATA-07, TEST-01/03, READ-01/03, AD-8 (the `TradingNode` sanction is `live_paper`'s alone), AD-10/AD-11 (bot isolation by `order_id_tag`), SEC-01 untouched.
- **Working directory:** `platform/`; research tests `python3 -m pytest -o addopts="" --rootdir=. research/tests -q`; live-paper tests `make test-live-paper`.

### Project Structure Notes

- `research/strategies/candle_pattern_strategy.py`, `research/strategies/backtest_candle_pattern.py`, `research/tests/test_candle_pattern_strategy.py`; `live_paper/{config,node}.py`, `live_paper/tests/test_candle_pattern_bot.py`, `live_paper.dockerfile`; `platform/tests/{test_boundaries,test_images}.py`; docs.

### References

- Epic text: "Story 27.8"; FR78
- Code: `live_paper/node.py:20-60`, `live_paper/config.py:77-140`, `live_paper/strategy.py` (`DummyStrategy` shape), `ml_signals/strategies/ofi_strategy.py` (strategy conventions), `ml_signals/strategies/backtest_dydx.py` (internal bars from trades), `nautilus_trader/trading/config.py:124-130` (`StrategyFactory`), `collector_core/backfill_bars.py:27` (EXTERNAL bars)
- Spine: AD-8, AD-10, AD-11, AD-D15; parent spine Deferred "`ExchangeDemoBot`/`RealMoneyBot` as separate types" unaffected

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
