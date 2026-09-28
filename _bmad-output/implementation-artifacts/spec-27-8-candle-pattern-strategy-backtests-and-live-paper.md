---
title: 'Story 27.8: Candlestick patterns tradeable: CandlePatternStrategy in backtests and paper bots'
type: 'feature'
created: '2026-09-28'
status: 'done'
final_revision: '6cff2b9a25ad229be4520125fb121c2fd977366c'
baseline_revision: '8cb5b89acc3ead52474bf29aee30e735f32c951a'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-8-candle-pattern-strategy-backtests-and-live-paper.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Story 27.7's `kernel.candle_patterns.CandlePattern` shows candlestick patterns on the chart, in the screener and in the scanner notebook, but nothing can trade them. A pattern found in the scanner cannot be backtested or run as a paper bot. Each paper bot is also hard-wired to `DummyStrategy`.

**Approach:**
- Add `research/strategies/candle_pattern_strategy.py`: a Nautilus `StrategyConfig` + `Strategy` pair that trades configured patterns. It uses the scanner's EMA trend filter, a next-bar-open entry, and exits on bar count, on an ATR stop, or on an opposite pattern.
- Add a `BacktestRunner` script and a second worked example in `04_backtest_evaluation`.
- Give the paper `BotConfig` optional `strategy`/`params` keys. The bots host resolves them through Nautilus's `StrategyFactory.create(ImportableStrategyConfig(...))` string path, so bots gains no import edge to research.

## Boundaries & Constraints

**Always:**
- The `live_paper` names in the epic text now map to the bots context (Story 25.3):
  - `BotConfig` → `bots/domain/config.py` (loader: `bots/infrastructure/config.py`);
  - `node.py` → `bots/infrastructure/nautilus_host.py`;
  - `live_paper.dockerfile` → `bots.dockerfile`;
  - `live_paper/README.md` → `bots/README.md`;
  - `live_paper/tests/` → `bots/tests/`.
  The compose service keeps its name `live-paper`.
- The strategy feeds one `kernel.candle_patterns.CandlePattern` per distinct configured name (a name in both sets gets one detector). It never defines a second pattern definition.
- The trend and volatility inputs are Nautilus's own `ExponentialMovingAverage` and `AverageTrueRange`.
- The trend filter uses exactly the scanner's semantics (`research.application.patterns.filter_hits`):
  - `above` passes when the fired bar's close is above the EMA, `below` when it is below, and `any` always passes;
  - the EMA includes the fired bar;
  - an EMA that is not initialized fails both `above` and `below`;
  - the filter is direction-independent, as in the scanner.
- A long fires when a `long_patterns` detector reads +100. A short fires when a `short_patterns` detector reads -100.
- The order is submitted in `on_bar` of the closed pattern bar, so it fills at the first price after that bar closes: the next bar's open, with no look-ahead.
- Every catalog read is time-bounded (MEM-01).
- Tests use real Nautilus objects written with `ParquetDataCatalog.write_data()` (TEST-03).
- Warnings are failures (TEST-04).
- Every simplification carries an in-code `Known limit:` with its upgrade path.
- Docs, the dockerfile `COPY` set, compose mounts and both Makefile test lists change in this same commit.
- `bot_id` stays pinned as `order_id_tag` (AD-11).
- Every existing `platform/bots/config.toml` parses unchanged, and all its bots stay `dummy`.

**Block If:** none. Every decision below is settled by the story, the epic or the code.

**Never:**
- No `bots → research` import, whether static or via `importlib`.
- Do not modify `nautilus_trader/` or `crates/`.
- No new dependency.
- No TA-Lib or `pandas_ta`.
- No `mode` on a paper config.
- `strategy`/`params` are not added to `ExecConfig`: the real-money/demo path stays `DummyStrategy`, recorded as a `Known limit:`.
- No silent fallback: an unknown `strategy`, a non-empty `params` on `dummy`, an unknown or reserved params key, or an explicitly set dummy-only key on a non-dummy bot each raise `ValueError` naming the file or bot (DATA-07).
- Never write `sprint-status.yaml`.
- Never park `awaiting-operator`: the VPS step goes to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" and the story finalizes `done`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Planted hammer, trend passes | 1m bars from trade ticks: a 3-bar down move, then a hammer whose close is above the EMA (`trend_condition="above"`, `long_patterns=("HAMMER",)`), then quiet bars | Exactly one long entry, filled at the hammer bar's close stamp (= bar t+1's open). The position closes after exactly `exit_bars` further bars. | none |
| Trend filter fails | The same data with `trend_condition="below"` | No order at all | none |
| Opposite pattern while long | A `short_patterns` pattern fires (-100) during a long | The position closes at that bar. There is no same-bar reversal; the next entry needs a new pattern. | none |
| Both directions fire on one bar | A long and a short pattern on the same bar while flat | No entry (ambiguous). Logged at debug level. | none |
| ATR stop | A long entry | A reduce-only `STOP_MARKET` is placed at `avg_px_open - stop_atr_multiple * ATR` (a short mirrors it). It is cancelled when the position closes any other way. | A stop price <= 0 or an uninitialized ATR at entry closes the position at once and logs an error. The strategy never holds a position without its stop. |
| Hole in bars | The gap between consecutive bars' `ts_event` exceeds the bar step (time bars) | Every detector, the EMA and the ATR are reset before the new bar is fed, as the scanner resets at holes. | none |
| Bad config | Unknown pattern name, `DOJI` (non-directional), a long-only name in `short_patterns` or vice versa, non-time bar aggregation, `bar_type` of another instrument, `exit_bars < 1`, `stop_atr_multiple <= 0`, bad `trend_condition`, `trade_size <= 0` | Nothing trades | Raises `ValueError` at strategy `__init__`, so a backtest or bot build fails loudly |
| Unknown params key | `params = {exit_bar = 5}` | Rejected | `CandlePatternStrategyConfig(..., forbid_unknown_fields=True)` makes msgspec raise; the host re-raises naming the `bot_id` |
| Existing config | Checked-in `bots/config.toml` | Parses; every bot has `strategy == "dummy"` and empty `params` | none |

</intent-contract>

## Code Map

- `platform/kernel/candle_patterns.py` -- the detector:
  - `PatternName`, `CandlePattern(pattern, **thresholds)` with `handle_bar`/`update_raw`/`value`/`initialized`/`reset`;
  - `_PATTERNS[name].detect` directions: the bullish-capable and bearish-capable names are derivable from the module docstring, so pin them in a strategy-side table checked by a test;
  - `NON_DIRECTIONAL`, and `BULLISH`/`BEARISH`.
- `platform/research/application/patterns.py:67,124-142,235-255` -- `CONDITIONS` and the scanner's EMA and filter semantics, which the strategy mirrors. The strategy restates the `CONDITIONS` tuple (a strategy module imports only `kernel` and `nautilus_trader`), and a test pins the two equal.
- `platform/research/application/backtest_runner.py`, `research/application/ports.py:44,57-120` -- `NodeRunner`, `RunSpec`, `RESERVED_PARAMS` (`instrument_id`, `order_id_tag`, `bar_type`), and `data="trades"`/`"bars:<spec>"`, where `bars:` injects the full `bar_type`.
- `platform/research/strategies/backtest_ofi.py` -- the `run()` + `__main__` shape and the `_CATALOG` default. `ofi_strategy.py` holds the config and strategy conventions.
- `platform/research/tests/test_backtest_runner.py:60-140` -- the synthetic catalog pattern (`_instrument()` CryptoPerpetual, a module-scoped `catalog` fixture) and one node per test. `research/tests/conftest.py` keeps the session engine and logger guard alive.
- `platform/research/notebooks/04_backtest_evaluation.py:63-100` -- the Parameters cell. Add the commented second worked example, then `make notebooks` (jupytext sync of the `.ipynb`).
- `platform/research/BACKTESTING.md:119-125` -- the copy table.
- `platform/bots/domain/config.py:91-113` -- `BotConfig` (frozen). `PaperConfig` shows the `MappingProxyType` freezing idiom.
- `platform/bots/infrastructure/config.py:51-122` -- `_reject_unknown_keys`, `_parse_bot`, and the `_named` error prefix.
- `platform/bots/infrastructure/nautilus_host.py:30-33` holds the docstring that claims ImportableStrategyConfig is backtest-only, which must be rewritten. `:87` is `HostedBot` (→ `Strategy`). `:261-280` is the strategy build loop.
- `platform/bots/infrastructure/cache_reader.py:44-59` -- requires `strategy.last_data_ns` and `config.instrument_id`. `bots/application/ports.py:75`.
- `platform/bots/strategies/dummy.py` -- the heartbeat idiom (`last_data_ns` set from `on_quote_tick`) and `orders_inflight` guard.
- `platform/bots/tests/test_node.py:45-100` -- `_paper_config`, `_build`, `_dispose`, which need Redis at `REDIS_URL`. `test_config.py:294` is the unknown-keys test. `test_bot_status.py:183`.
- `platform/bot_tui/app.py:118-131,657-668` -- the hardcoded `_STRATEGY_SOURCE_PATH`, whose comment says to switch to a per-bot lookup when a second strategy exists. `bot_tui/tests/test_app_bot_detail.py:336-380`. `docker-compose.yml:329-335` holds the mount.
- `platform/tests/test_images.py:339-377` -- `_dynamic_imports`, `import_closure`.
- `platform/tests/test_boundaries.py:108-133` -- `GRAPH`, which has no (BOTS, RESEARCH) edge. `:1466-1477` holds `_BOTS_SANCTIONED_CALLS` (module state must be a `MappingProxyType`).
- `platform/bots.dockerfile` -- the COPY set. `Makefile` `test-live-paper` runs `bots/tests observability/tests kernel/tests tests`.
- Docs to change:
  - `platform/bots/README.md` (Configure `:49`);
  - `platform/docs/BOT_OPERATIONS.md` (`:5-11`, §3 `:157-189`);
  - `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" (`:481`; entry format `### <story-key> <title> (commit: see `git log --grep <key>`)` + `- [ ]` steps);
  - the DDD spine `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` (bots and research rows, dependency graph, `[amended 2026-09-28: Story 27.8 — …]`);
  - `platform/ARCHITECTURE.md` (bots section).

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/strategies/candle_pattern_strategy.py` -- The strategy module. It imports only `kernel` and `nautilus_trader`.
  - `CandlePatternStrategyConfig(StrategyConfig, frozen=True, forbid_unknown_fields=True)` has these fields:
    - `instrument_id: InstrumentId`;
    - `bar_type: str | None = None`; None means `f"{instrument_id}-1-MINUTE-LAST-INTERNAL"`, and `EXTERNAL` is honoured when the string says so;
    - `long_patterns=("HAMMER","ENGULFING","MORNING_STAR")`;
    - `short_patterns=("SHOOTING_STAR","ENGULFING","EVENING_STAR")`;
    - `trend_ema_period=50`, `trend_condition="above"`;
    - `trade_size=Decimal("0.01")`;
    - `exit_bars=10`;
    - `atr_period=14`, `stop_atr_multiple=2.0`;
    - `allow_short=True`.
  - `CandlePatternStrategy.__init__` validates each matrix "Bad config" row. Each named pattern must be able to fire in its set's direction, via a module table `_DIRECTIONS: MappingProxyType[PatternName, frozenset[int]]` covering all 22 names, with a test that replays the kernel's cases to confirm it. It builds the detectors, the EMA and the ATR, and sets `last_data_ns: int = 0`.
  - `on_start` does three things:
    - resolves the instrument (missing → `log.error` + `stop()`);
    - subscribes the bar type;
    - subscribes quote ticks, which set `last_data_ns` for the heartbeat and feed the Sandbox fill engine live (`on_bar` also sets `last_data_ns`).
  - `on_bar` runs in order:
    - hole reset;
    - feed the patterns, EMA and ATR;
    - if in a position, count bars held, then exit on `exit_bars` or on an opposite pattern;
    - if flat with no open or in-flight orders, enter on exactly one direction passing the filter, with every needed indicator initialized.
  - `on_position_opened` places the ATR stop.
  - `on_position_closed` cancels the remaining orders and clears the state.
  - `on_reset` clears all state.
  - The module docstring documents the rules and these `Known limit:`s:
    - direction-independent filter parity;
    - a stop fill in a backtest fills on trade ticks.
  - Every function is ≤ 30 lines, with cognitive complexity ≤ 10.
- [x] `platform/research/strategies/backtest_candle_pattern.py` -- `run(symbol="BTC-USD-PERP.DYDX", start=..., end=..., catalog_path=_CATALOG, **params) -> RunResult` via `NodeRunner().run(RunSpec(..., data="trades", strategy_path/config_path by string))`. `__main__` prints `result.metrics.as_table()` (check that `MetricReport` has `as_table`; if the name differs, use its existing tabular method).
- [x] `platform/research/tests/test_candle_pattern_strategy.py` -- Covers the matrix rows as follows:
  - Rows 1 and 2 run end-to-end through `NodeRunner` with a synthetic CryptoPerpetual trade-tick catalog. Several ticks per minute carry each planted OHLC. Use `trend_ema_period` small, `stop_atr_multiple` large, `exit_bars=3`, `long_patterns=("HAMMER",)`, `allow_short=False`. Assert exactly one closed trade, LONG, with `entry_ts` equal to the hammer bar's close stamp and `exit_ts == entry_ts + exit_bars * 60s`. With `below`, assert zero trades and no orders.
  - Construct at most two `BacktestNode`s in the file (a module-scoped fixture). Prefer one sweep over `trend_condition` via `NodeRunner.sweep`, so there is one node.
  - The remaining rows are unit-level:
    - the "Bad config" `ValueError`s;
    - forbid-unknown-fields;
    - the `_DIRECTIONS` table against the kernel;
    - the `CONDITIONS` tuple equals `research.application.patterns.CONDITIONS` (a test may import both);
    - hole reset;
    - opposite-pattern exit and both-fire.
  - For the opposite-exit, both-fire and ATR-stop rows, either a second catalog in the same sweep, or directly driven strategy tests without mocks (a `BacktestEngine` with `add_data` is acceptable if cheaper). Keep the node count minimal.
- [x] `platform/research/notebooks/04_backtest_evaluation.py` (+ the `.ipynb` via `make notebooks` or `uv run jupytext --sync`) -- Parameters cell: a commented second worked example with the `candle_pattern_strategy` paths:
  - `PARAMS={"trade_size": "0.01", "long_patterns": ["HAMMER", "ENGULFING"], "trend_condition": "above"}`;
  - `DATA="trades"`;
  - a matching `GRID` (e.g. `exit_bars` × `stop_atr_multiple`).
  The markdown bullets mention it.
- [x] `platform/research/BACKTESTING.md` -- Add a copy-table row, "Bars from trades, pattern entries → `backtest_candle_pattern.py`", plus the run command line.
- [x] `platform/bots/domain/config.py` -- `BotConfig` gains `strategy: str = "dummy"` and `params: Mapping[str, Any] = field(default_factory=dict)`, frozen to a `MappingProxyType` in `__post_init__`. The docstring states the two keys.
- [x] `platform/bots/infrastructure/nautilus_host.py` -- Changes:
  - `STRATEGIES: MappingProxyType[str, tuple[str, str] | None]` = `{"dummy": None, "candle_pattern": ("research.strategies.candle_pattern_strategy:CandlePatternStrategy", "…:CandlePatternStrategyConfig")}`;
  - `check_strategy(bot)`: unknown name → `ValueError` listing the table; params on `dummy` → `ValueError`; params keys ∩ (`instrument_id`, `trade_size`, `order_id_tag`, plus every `StrategyConfig` base field) → `ValueError`, since BotConfig owns them;
  - `_strategy_for(bot)`: dummy → as today; otherwise `StrategyFactory.create(ImportableStrategyConfig(path, config_path, {**params, "instrument_id": ..., "trade_size": str(bot.trade_size), "order_id_tag": bot.bot_id}))`, re-raising any error as `ValueError(f"[[bots]] {bot_id}: ...")`;
  - `HostedBot = tuple[BotConfig | ExecConfig, Strategy]`;
  - rewrite the module docstring's "not `ImportableStrategyConfig` (AD-6)" paragraph: dummy stays a direct class, and the others go by string path so there is no bots→research edge;
  - the `Known limit:` for the ExecBot path.
- [x] `platform/bots/infrastructure/config.py` -- `_parse_bot` passes `strategy` (must be a str) and `params` (must be a TOML table), rejects dummy-only keys (`trend_buy_threshold`, `trend_sell_threshold`, `ofi_confirm_threshold`) explicitly set on a non-dummy bot, and runs `_named(path, check_strategy, bot)`.
- [x] `platform/bots/tests/test_config.py` -- Tests:
  - the checked-in `config.toml` loads with every bot `dummy` and empty params;
  - a `candle_pattern` bot with a `[bots.params]` table parses;
  - each rejection row;
  - params are read-only.
- [x] `platform/bots/tests/test_candle_pattern_bot.py` -- `build_node` with one `candle_pattern` bot plus one dummy bot on the sandbox venue constructs. The candle bot's strategy is a `CandlePatternStrategy` with `order_id_tag == bot_id` and the params applied. An unknown params key fails naming the bot. `StrategyCacheReader(strategy).strategy_name == "CandlePatternStrategy"`. Needs Redis like `test_node.py`.
- [x] `platform/bot_tui/app.py` + `bot_tui/tests/test_app_bot_detail.py` + `platform/docker-compose.yml` -- The `v` view resolves the source per bot: `STRATEGY_SOURCE_DIR` (default `/app/strategy_source`) `/ f"{status['strategy']}.py"`, where the class name comes from the bot's `bots:status`. A missing file or unknown bot shows the existing "could not read" text. Compose mounts `./bots/strategies/dummy.py:/app/strategy_source/DummyStrategy.py:ro` and `./research/strategies/candle_pattern_strategy.py:/app/strategy_source/CandlePatternStrategy.py:ro`. Update the comments and the tests, including a candle-bot case.
- [x] `platform/bots.dockerfile` -- `COPY platform/research ./research` (kernel is already copied), with a comment giving the string-path reason.
- [x] `platform/tests/test_images.py` -- An explicit `_STRING_PATH_IMPORTS = {"bots.infrastructure.nautilus_host": ("research.strategies.candle_pattern_strategy",)}` folded into `import_closure`, because the `ast` walk cannot see a string. Plus a drift guard: every non-docstring string literal matching `^[a-z_][\w.]*:[A-Za-z_]\w*$` whose module part is an in-repo module, found in any `bots` module, must appear in the table.
- [x] `platform/tests/test_boundaries.py` -- A comment at `GRAPH` explaining why there is no (BOTS, RESEARCH) edge: the strategy is loaded by string path through `StrategyFactory`, and the image closure is covered by `test_images._STRING_PATH_IMPORTS`. Plus a named test that no `bots` module imports `research` by any static import or `import_module`.
- [x] Docs:
  - `bots/README.md`: the two keys, the strategies table, a TOML example, and a note that the frozen key set is extended with defaulting optional keys, so existing files parse unchanged;
  - `docs/BOT_OPERATIONS.md`: the table row and a rewritten §3, plus the config example;
  - `docs/DEPLOY_CHECKLIST.md`: a new "Deferred operator actions" entry headed `### 27-8-candle-pattern-strategy-backtests-and-live-paper (… commit: see `git log --grep 27-8-candle`)` with the steps: rebuild the live-paper image (its COPY set changed) and restart bot_tui (new mounts); add one `candle_pattern` paper bot to `platform/bots/config.toml` and `make up-live-paper`; confirm a fill in `bot_tui`;
  - the spine's bots and research rows and dependency graph with `[amended 2026-09-28: Story 27.8 — …]`;
  - `platform/ARCHITECTURE.md`'s bots section.

**Acceptance Criteria:**
- Given the research tests, when `python3 -m pytest -o addopts="" --rootdir=. research/tests -q` runs from `platform/`, then everything passes, including `test_candle_pattern_strategy.py` and the notebook harness (04 still runs its first example).
- Given Redis at `REDIS_URL`, when `python3 -m pytest -o addopts="" --rootdir=. bots/tests kernel/tests tests bot_tui/tests -q` runs, then there are no failures beyond the known baseline.
- Given `tests/test_images.py` and `tests/test_boundaries.py`, when they run, then they pass with `research` in the bots image closure and no static bots→research import.
- Given the changed Python files, when `ruff check`, `ruff format --check` and `mypy` run on them, then they are clean.

## Spec Change Log

- 2026-09-28 (dev): **Zero-volume bars are holes.** Nautilus emits flat bars for untraded minutes (`time_bars_build_with_no_updates=True`), so a gap never shows up in `ts_event`. A zero-volume bar resets the indicators and is not fed, matching the scanner.
- 2026-09-28 (dev): **The entry fill price in a backtest** is the pattern bar's last trade, at its close stamp, because the backtest has only trade ticks. This is a Known limit.

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 19: (high 3, medium 5, low 11)
- defer: 0
- reject: 4
- addressed_findings:
  - `[high]` `[patch]` A stop that was rejected, denied, cancelled or expired left the position unprotected. `_ensure_stop` now runs on every bar and from the order-event hooks, and re-places the stop; if the ATR is not ready, it closes the position and logs an error.
  - `[high]` `[patch]` A partially filled entry left the stop sized to the first fill only. `on_position_changed` now resizes it through `_ensure_stop`.
  - `[high]` `[patch]` After a restart the bars-held counter started again from zero. It is now derived from `position.ts_opened` (ceiling division); only the Known limit on phantom cached orders after a Sandbox restart remains, and the class invariant is reworded.
  - `[medium]` `[patch]` An accepted but unfilled close order could be duplicated on the next bar. `_closing()` now guards against it.
  - `[medium]` `[patch]` A `trade_size` below the instrument's `size_increment` failed in `on_bar`. `on_start` now checks `make_qty` and stops the strategy.
  - `[medium]` `[patch]` `on_position_opened` returned silently when the position lookup came back None. It now logs an error and falls back to `_open_position()`.
  - `[medium]` `[patch]` A duplicate or out-of-order bar was fed twice. It is now skipped with a warning.
  - `[medium]` `[patch]` Tests added: a short entry with its BUY stop above the entry, stop re-placement and close, the bar skip, and the new validation rows.
  - `[low]` `[patch]` Validation: `trade_size` NaN/Infinity, a non-finite `stop_atr_multiple` and periods < 1 now raise `ValueError`.
  - `[low]` `[patch]` `_strategy_for` calls `check_strategy`, so an unknown strategy gets a named `ValueError` instead of a `KeyError`.
  - `[low]` `[patch]` A built strategy without `last_data_ns` is refused at build.
  - `[low]` `[patch]` `BotConfig.params` is frozen all the way down, and `hash=False` keeps `BotConfig` hashable.
  - `[low]` `[patch]` New Redis-free test that every `STRATEGIES` entry resolves through `StrategyFactory`.
  - `[low]` `[patch]` The checked-in-config test asserts the property (a bot without `strategy` is `dummy`), not a count of 40 bots.
  - `[low]` `[patch]` bot_tui gets a clear message when a bot has no status yet, a public `bots_state.latest_status()` accessor, and catches `UnicodeDecodeError`.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST entry names the exact mount path.
  - `[low]` `[patch]` The spine's AD-11 row cites symbols instead of line numbers, and the dependency graph shows the runtime-only bots → research string-path edge.
- rejected:
  - one bad params table stops the whole fleet: this is the intended fail-closed behaviour of the one config file;
  - the whole `research/` tree is copied into the bots image: the AC mandates it;
  - quiet markets reset the EMA: this is scanner parity and is documented;
  - the commented notebook example never runs: the spec chose a commented example, and `backtest_candle_pattern.py` plus its test cover the path.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 1, medium 2, low 2)
- defer: 0
- reject: 20
- addressed_findings:
  - `[high]` `[patch]` Live, the "second stop failure at the same instant" guard never matched, because every live event carries its own wall-clock `ts_event`. A venue that kept refusing the stop was sent a new one without limit. The guard is now a count of consecutive stop failures: the second closes the position, and the count restarts when the venue accepts a stop (`on_order_accepted`), on position close and on reset. New tests: two failures at different timestamps close the position, and an accepted stop restarts the count.
  - `[medium]` `[patch]` A hole (for example one zero-volume minute) resets the ATR mid-position. A stop lost before the ATR re-warmed then force-closed the position, where the spec prices the stop from the ATR "at entry". The stop distance (`stop_atr_multiple * ATR`) is now fixed when the position's first stop is priced and reused for every re-placement until the position closes. The old test is replaced: the stop is placed again at the same trigger.
  - `[medium]` `[patch]` An off-grid `trade_size` (for example 0.0015 on a 0.001 increment) was silently rounded by `make_qty`, which DATA-07 forbids. `_tradeable` now requires an exact multiple of `size_increment`, else `log.error` + `stop()`. New test added.
  - `[low]` `[patch]` `_DUMMY_ONLY_KEYS` was a hand-kept tuple. A test now pins it to `BotConfig` ∩ `DummyStrategyConfig` fields, minus the shared keys.
  - `[low]` `[patch]` The spine's 22.6 row cited `VENUES` at `:157`, but it sits at `:158`. It is now cited by symbol only.
- rejected:
  - restart with no cached stop closes the position: this is the fail-closed matrix row;
  - the phantom stop after a restart: already a documented Known limit;
  - default shorts gated by `above`: the documented direction-independent parity;
  - float stop arithmetic: a derived price, not a market-data value;
  - one bad bot stops the fleet, and the whole `research/` COPY: rejected before, by design or mandated by the AC;
  - strategies built before the node: speculative;
  - same-instrument bots on a shared Sandbox: speculative;
  - the commented notebook example: the spec's choice;
  - the DEPLOY_CHECKLIST edit: `config.toml` is bind-mounted, and the step is spec-mandated;
  - the `STRATEGY_SOURCE_DIR` rename: spec-mandated, and nothing sets the old name;
  - tests importing test helpers: `CASES` is spec-directed;
  - no strategy-level log for entry and close rejections: Nautilus logs them, and the next bar retries;
  - a missing instrument or off-grid size leaves the bot idle: spec-mandated `stop()`, and the heartbeat shows it stale;
  - EXTERNAL bars with trades-only data: user misconfiguration;
  - an entry's partial fill: the stop is resized on the final fill;
  - the string-path drift guard missing f-strings and nested blocks: best effort, and the table stays explicit.

### 2026-09-28 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 3, low 4)
- defer: 0
- reject: 17
- addressed_findings:
  - `[medium]` `[patch]` `_covers` compared the stop's full `quantity` with the position's. A stop part-way through its own fill no longer matched the shrunken position, so `_ensure_stop` cancelled it mid-execution and sent a replacement at an already-crossed trigger. It now compares `leaves_qty`. New test: a partly filled stop still covers the rest of the position.
  - `[medium]` `[patch]` Stop state could carry from one position to the next:
    - Nautilus delivers no events to a stopped strategy, so a position closed while the bot was stopped left its stop distance and failure count for the next position.
    - An entry whose fill came after a hole had reset the ATR found no distance and was closed at once.
    
    Stop state is now keyed to the position (id + `ts_opened`, since a NETTING reopen keeps the id) through `_track`. The distance is taken from the pattern bar's ATR when the entry is submitted (`_entry_distance`), with the ATR at first pricing as the fallback after a restart. New test added.
  - `[medium]` `[patch]` A stop left open while flat (its cancel lost or refused) kept `_is_idle` False forever, so the bot never entered again. `_is_idle` now re-cancels such a stop with a warning on every bar. The phantom-order Known limit now names the flat case: the phantom's cancel is refused, and the bot stays out. New test uses a venue with `use_reduce_only=False` so the leftover can rest.
  - `[low]` `[patch]` A `trade_size` below `min_quantity` or above `max_quantity` was denied by the RiskEngine on every entry. It is now refused at start, like an off-grid size (`_size_problem`). New parametrized test.
  - `[low]` `[patch]` MONTH and YEAR bars get a nominal 30- or 365-day step from Nautilus, so every 31-day month read as a hole. They are now refused at construction ("no fixed step"). Two new bad-config rows.
  - `[low]` `[patch]` `test_every_string_path_strategy_builds_from_the_keys_the_host_passes` rebuilt the `ImportableStrategyConfig` by hand. It now goes through the host's `_strategy_for`.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST step relied on `GET /api/errors`, which never sees the strategy's own failures (the strategy module does not import `observability`). It now also says to search the `live-paper` log for `CandlePatternStrategy` ERROR/WARN lines.
- rejected:
  - float stop arithmetic and nearest-tick rounding: a derived price, rejected before;
  - params typos are caught at node build, not at load: this is the spec's design, and the error names the bot;
  - default shorts gated by `above`, and HANGING_MAN versus HAMMER: documented direction-independent parity and kernel semantics;
  - illiquid instruments that reset without logging: scanner parity, rejected before;
  - the commented notebook example, `backtest_candle_pattern.run` untested: the spec chose a commented example, and the `run()` glue is covered by the NodeRunner test (TEST-02);
  - string-path guard bypasses (concatenation, f-strings, nested blocks): best effort, rejected before;
  - no test for compose strategy-source mounts: a missing mount shows a loud "could not read" line, and the checklist checks it;
  - the whole `research/` COPY: mandated by the AC; the image closure test covers the imports;
  - the heartbeat checked with `hasattr` only: the typed attribute is on the strategy, and the build refuses a strategy without it;
  - the checked-in-config test name, the tracked `config.toml` edit on the VPS, and `_dispose` imported across tests: rejected before;
  - bot_tui with a None bot id: the strategy view is reachable only from a selected bot.

## Design Notes

- **Why submit on the pattern bar's close:** the story's task text suggested a `pending_side` submitted on bar t+1. That would fill at t+1's *close*, one bar late. A market order submitted in `on_bar(t)` fills at the first price after bar t closes, which is bar t+1's open: the AC's "enters at the next bar's open", with no look-ahead because bar t is closed. Record this as a deviation in Completion Notes.
- **Why feed indicators manually, not `register_indicator_for_bars`:** a hole must reset the detectors, EMA and ATR *before* the new bar is fed, as the scanner does. Nautilus feeds registered indicators before `on_bar`, too late to reset.
- **Why params are validated twice:** `forbid_unknown_fields` rejects typos in backtests and bots alike. The host's reserved-key check keeps `BotConfig` the sole owner of the identity and sizing keys (`strategy_id`/`order_id_tag` would break AD-11).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests kernel/tests tests/test_boundaries.py tests/test_images.py -q` -- expected: pass
- `docker run -d --rm --name redis-278 -p 6379:6379 redis:7-alpine` (stop it afterwards), then `cd platform && python3 -m pytest -o addopts="" --rootdir=. bots/tests bot_tui/tests -q` -- expected: pass
- `cd platform && ruff check <changed> && ruff format --check <changed> && mypy <changed .py>` -- expected: clean
- `cd platform && uv run jupytext --sync research/notebooks/04_backtest_evaluation.py` -- expected: pair in sync


## Auto Run Result

Status: done

**Summary:**
- A second follow-up review of Story 27.8's `CandlePatternStrategy`. Scope is unchanged, and the stop handling is hardened further:
  - a stop part-way through its fill still counts as covering the position;
  - stop state (distance, failure count) belongs to one position, and the distance comes from the entry's ATR;
  - a stop left open while flat is re-cancelled instead of blocking entries for good;
  - a `trade_size` outside the instrument's quantity limits is refused at start;
  - MONTH and YEAR bar types are refused.

**Files (this pass):**
- `platform/research/strategies/candle_pattern_strategy.py`: `leaves_qty` in `_covers`; `_track`, `_entry_distance` and `_atr_distance`; the leftover-stop cancel in `_is_idle`; `_size_problem` (grid and min/max); the MONTH/YEAR refusal; docstrings and Known limit updated.
- `platform/research/tests/test_candle_pattern_strategy.py`: new tests for a partly filled stop, per-position stop state, a leftover stop while flat, quantity limits, and the MONTH/YEAR config rows.
- `platform/bots/tests/test_candle_pattern_bot.py`: the string-path build test goes through the host's `_strategy_for`.
- `platform/docs/DEPLOY_CHECKLIST.md`: the 27-8 entry says where the strategy's own errors show up.

**Review:** 7 patches applied (3 medium, 4 low), 0 deferred, 17 rejected.

**Verification:**
- `research/tests kernel/tests tests`: 1017 passed, 3 skipped.
- With a temporary `redis:7-alpine`, `bots/tests bot_tui/tests`: 381 passed, 1 deselected. The deselected test is `test_build_node_passes_redis_credentials_and_ssl_from_url`, which is host-dependent, as in earlier passes.
- ruff check, ruff format and mypy are clean on the changed Python files.

**Follow-up review recommended:** true. The stop-state keying and the new cancel path in the entry gate change safety-relevant trading behaviour. They are tested, but an independent look is worthwhile.

**Residual risks:**
- The live Sandbox stop and reject behaviour is still unverified; that is the deferred VPS step.
- A phantom cached stop after a Sandbox restart still blocks the bot; this is a Known limit, now covering the flat case too.
- A notional limit (min or max notional) is still enforced only by the RiskEngine's per-order denial.
- `make test-live-paper` was not run inside the image.
