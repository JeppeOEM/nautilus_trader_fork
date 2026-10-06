---
title: 'Story 33.14: The liquidation cascade bot: a strategy that shorts into a long-liquidation cascade, backtested on the archive and run as a paper bot on the live liquidation feed'
type: 'feature'
created: '2026-10-06'
status: 'done'
baseline_revision: '19908f4e8b0359eae9769d479d14533ef8e6e61f'
final_revision: 'e693afd68e6a616c8292b646c0a6e623c5844cc8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-33-context.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Bybit liquidations have been archived since 33.1 and served since 33.4, but no code trades them. There is no streaming cascade definition, no strategy, no backtest kind that reads `custom_liquidation`, and no path from the collector's `liquidations:raw` into a paper `TradingNode`.

**Approach:** Build the pieces in this order, so research, the backtest and the bot share one cascade definition (SSOT-02):
1. `kernel.indicators.LiquidationCascade`.
2. Pure entry and exit rules (`cascade_rules.py`) and a `LiquidationCascadeStrategy`.
3. A `liquidations` backtest data kind, a runner and four gallery specs.
4. A Nautilus custom data client that bridges `liquidations:raw` into the paper node.
5. A replay and parity path that pairs the bot's signal-log cycles with a catalog replay.
6. The docs.

## Boundaries & Constraints

**Always:**
- **Side mapping (D-147).** `LiquidatedSide.LONG` is a forced **sell** and feeds `rate_long`; `SHORT` feeds `rate_short`. `direction = -1` means longs are being liquidated (the price is falling).
  - `follow` trades with the forced flow: direction -1 is a SELL entry (a short), +1 a BUY.
  - `fade` trades against the cascade once it is spent: direction -1 is a BUY, +1 a SELL.
  - `sides` names the *position* sides allowed: `"short"` and/or `"long"`.
- **Exact inputs (DATA-04).**
  - The indicator is fed `Liquidation.notional_units()` (int, units of `10^-(pp+sp)`).
  - A row whose precisions differ from the instrument definition's is rescaled exactly to the definition's `pp+sp`. A row that cannot be rescaled exactly is logged at ERROR, counted, and not fed.
  - Money thresholds (`min_episode_notional`, `max_daily_loss`, `trade_size`) are `Decimal` config strings. Rates and intensity are floats, a reader's own computation.
- **Indicator time.**
  - `update_liquidation(side, notional_units, ts_ns)` takes the row's `ts_init`: the collector's receipt time, which is what a live bot could know and the order the backtest delivers.
  - `advance(ts_ns)` moves the clock without an event.
  - The clock never moves backwards. An event stamped before the clock is placed at the clock.
  - The baseline is the **exact** continuous-time EMA of the piecewise-constant window rate. Every eviction breakpoint is integrated in order, so the result does not depend on how often `advance` is called.
- **Decisions are pure.** `research/strategies/cascade_rules.py` imports only stdlib and `nautilus_trader.model.enums` (`OrderSide`). The strategy only gathers state and acts on what `should_enter`/`should_exit` return.
- **Strategy (AD-11).**
  - It never adds to an open position.
  - It submits market orders only; the `order_id_tag` is the host's `bot_id`.
  - It keeps a public `last_data_ns`, the latest of its quote and liquidation data.
  - The config sets `forbid_unknown_fields=True`.
  - It imports only `kernel`, `nautilus_trader` and `research.strategies` siblings. Research may not import `bots`; that is `tests/test_boundaries.py`'s graph.
- **Live paper only.** The cascade strategy is never built under `ExecConfig`. `ExecConfig` has no `strategy` key and an `ExecBot` always runs `DummyStrategy`, so real money is structurally unreachable. Keep it that way: add no strategy key there.
- **DATA-07.**
  - An undecodable `liquidations:raw` entry is recorded at `bots.liquidation_feed.entry`, once per entry up to a cap and then as one summary line, and its siblings are still delivered.
  - A Redis disconnect is recorded at `bots.liquidation_feed.connection` and makes the bot read stale after `DATA_STALE_NS`.
  - A quiet market (no liquidations) never reads as a dead feed: only the client's connection state can.
- **Bounded reads (MEM-01).** Research and replay read liquidations through `kernel.catalog_files.query_liquidations`, one UTC day at a time, and the backtest through time-bounded `BacktestDataConfig`.
- No new dependency. Never touch `nautilus_trader/` or `crates/`.

**Block If:**
- The pinned `BacktestNode` cannot deliver `Liquidation` from `custom_liquidation` to a strategy subscribed with `DataType(Liquidation)` without modifying `nautilus_trader/`. HALT with the evidence; do not hand-roll a simulation loop.
- A `LiveMarketDataClient` subclass cannot reach the strategy's subscription without touching `TradingNode`/`DataEngine` private internals. HALT.

**Never:**
- No Hyperliquid, dYdX or spot liquidation rows or feeds; the cascade bot is meaningful for Bybit LINEAR ids only (`has_liquidation_feed`).
- No real-money path.
- No chart, screener or alert change.
- No `price_kind` column.
- No `research/application/liquidations.py` beyond the episode replay below: 33.13 owns forward returns, leverage and forced share.
- No change to `DummyStrategy`'s records or the dummy parity path's classification. The dummy `start` record gains no key.
- No resting stop orders. Exits are market orders decided by `should_exit` (Known limit below).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Quiet then burst | `window_s=30, baseline_s=600, threshold=3, decay=0.5`. 10 min of one 1 000-unit LONG every 60 s, then a 90 s burst of 1 000 units every second | During the quiet stretch: `initialized` at +600 s; `active` false; `direction` 0. During the burst: `rate_long` reaches 1 000 units/s at +30 s into it; `active` turns true at the hand-computed second; `direction` -1; `peak_rate` = max rate | — |
| Decay | Burst stops | `rate_long` falls as entries expire. `spent` turns true at the first update where rate < peak × 0.5. The episode ends (peak, spent and episode fields reset) at the first update that is both spent and not active | — |
| Floor | No liquidation for a whole `baseline_s` | baseline = `max(ema, BASELINE_FLOOR)`, never 0; `intensity` stays finite | — |
| Late event | `update_liquidation(ts=T-2s)` after `advance(T)` | Inserted at T; the clock stays T | — |
| Follow entry | `sides=["short"]`, active, direction -1, rising, episode notional ≥ min, within `entry_timeout_s`, flat, under max entries, cooldown elapsed, daily loss not breached, OFI agrees (if `ofi_confirm`) | `OrderSide.SELL` | — |
| Refusals | Any one of: `"short"` not in sides; not rising; already spent; timed out; max entries reached; cooldown; daily loss breached; OFI disagrees or uninitialised; position open; not initialized | `None` | Daily loss: one INFO log per UTC day |
| Fade entry | `mode=fade`, `sides=["long"]`, episode direction -1, spent | `OrderSide.BUY` | — |
| Exits | Open short; in priority: price ≥ stop → `"stop"`; ≤ take-profit → `"take_profit"`; `exit_on_spent` and spent → `"spent"`; held ≥ `max_hold_s` → `"max_hold"` | That reason; otherwise `None` | — |
| Bad feed entry | `liquidations:raw` array with one non-dict and one valid row | The valid row is delivered; the bad one is ledgered | `bots.liquidation_feed.entry` |
| Other instrument | Valid row for an iid no bot subscribed | Not delivered | — |
| Redis down | Pub/sub drops | ERROR + ledger; reconnect with backoff; the bot's reported `last_data_ns` is capped at the disconnect time | `bots.liquidation_feed.connection` |
| Spot/HL bot | `[[bots]]` cascade on a non-Bybit-LINEAR id | Refused at config load | `ValueError` naming the bot |

</intent-contract>

## Code Map

- `platform/kernel/indicators.py`: the `Indicator` pattern (`OrderFlowImbalance` :179, `PyCondition`, `_set_initialized`, `_reset`); its module docstring has a per-story consumer paragraph.
- `platform/kernel/liquidation.py`: `Liquidation` (:117), `LiquidatedSide` (:92), `notional_units()`, `to_dict`/`from_dict`, `has_liquidation_feed` (:75). Add a `LIQUIDATION_CLIENT_ID = "LIQUIDATIONS"` constant here, shared by research and bots.
- `platform/kernel/catalog_files.py:694`: `query_liquidations`.
- `platform/research/strategies/candle_pattern_strategy.py`: the live-runnable template. Note `last_data_ns` (:292), `_bars` use, ATR `_feed`/`_atr_distance` (:353/:516) and `_tradeable`.
- `platform/research/application/ports.py:123`: `RunSpec`, the `data` validation (:183) and `RESERVED_PARAMS`.
- `platform/research/application/backtest_runner.py`: `write_derived_quotes` (:136), `_data_configs` (:163), the `bar_type` injection (:270-272).
- `platform/research/strategies/backtest_candle_pattern.py`: the runner template.
- `platform/research/application/gallery.py`: `_spec` (:110), `default_specs` (:227).
- `platform/research/notebooks/08_strategy_gallery.py`, `notebooks/_params.py`.
- `platform/research/tests/test_notebooks.py`: `NOTEBOOK_ENV`, 08 at :169.
- `platform/research/tests/fixture_catalog.py`: six instruments; `BTCUSDT-LINEAR.BYBIT` is at :136; no liquidations yet.
- `platform/bots/infrastructure/nautilus_host.py`: `STRATEGIES` (:117), `_RESERVED_PARAMS`, `_paper_venue_clients` (:198), `build_node` (:280), `_strategy_for`/`_importable_strategy` (:338-404), `_signal_log_path` (:371).
- `platform/bots/infrastructure/config.py`: `_parse_strategy`/`_parse_bot`. `platform/bots/infrastructure/cache_reader.py:155`: `last_data_ns`.
- `platform/bots/strategies/signal_log.py`: the line format, which is the shape to match. `bots/strategies/dummy.py:295-437` shows the start record and the timer grid.
- `platform/bots/signal_replay.py`: `_STRATEGY_PATH`/`_DUMMY` (:117), `strategy_config` (:336), `_data_configs` (:324), `_selected_bots` (:464), `check_same_config` (:244).
- `platform/verification/domain/bot_parity.py` (dummy pairing; leave it unchanged), `verification/application/bot_parity.py` (`_compare` :255, `check` :266), `verification/domain/signal_compare.py` (`relative`, `REL_TOL`).
- `platform/ranking/application/engine.py:204-240`: the per-entry decode-and-ledger precedent for `liquidations:raw`.
- `nautilus_trader/live/data_client.py:349` `LiveMarketDataClient`, `live/factories.py:33` `LiveDataClientFactory.create`, `live/node.py:230` `add_data_client_factory`. Custom data must reach `_handle_data` as `CustomData(DataType(Liquidation), liq)`; the topic is keyed by the row's `instrument_id`. These are read-only references.
- `platform/tests/test_boundaries.py:1868`: `TRADING_NODE_HOSTS`. `platform/tests/test_images.py:369`: `_STRING_PATH_IMPORTS`.
- `platform/docker-compose.yml`: the `bot_tui` `strategy_source` mounts (~:369-385).

## Tasks & Acceptance

**Execution:**

*Kernel*
- [x] `platform/kernel/indicators.py` -- Add `LiquidationCascade(window_s, baseline_s, intensity_threshold, decay_ratio)`, an `Indicator` subclass with `PyCondition` checks (`0 < decay_ratio < 1`, positive values).
  - Inputs: `update_liquidation(side: LiquidatedSide, notional_units: int, ts_ns: int)` and `advance(ts_ns)`. Both are O(1) amortised.
  - Outputs: `rate_long`, `rate_short`, `baseline`, `intensity`, `direction`, `active`, `rising` (the total rate is the episode's peak, or above the previous update's), `peak_rate`, `spent`, `episode_direction`, `episode_start_ns`, `episode_notional_units`, `initialized`.
  - Add the named `BASELINE_FLOOR` constant (notional units per second) and the module-docstring consumer note. `_reset` clears everything.
  - `active` requires `initialized`. `direction` is 0 when not active. `episode_*` stays set until the episode ends.
- [x] `platform/kernel/liquidation.py` -- Add `LIQUIDATION_CLIENT_ID`.
- [x] `platform/kernel/tests/test_liquidation_cascade.py` (new) -- Drive the matrix's quiet hour → 90 s burst → decay. Assert every output's transition at hand-computed seconds; the EMA expectations are written out with `math.exp` in the test. Also cover: the floor; the late event; that `advance` frequency does not change the baseline (1 s against 60 s calls give equal values within 1e-12); the episode end; `_reset`; the constructor rejections.

*Research*
- [x] `platform/research/strategies/cascade_rules.py` (new) -- Frozen dataclasses:
  - `CascadeState`: indicator outputs, `now_ns`, `bid`/`ask`, OFI value or `None`, `episode_notional` (quote `Decimal`), `entries_this_episode`, `last_exit_ns`, `daily_realized` (`Decimal`), `position_open`.
  - `PositionView`: side, `entry_price`, `stop_distance`, `opened_ns`.
  - `RulesConfig`: the strategy config's rule fields.

  Define `should_enter(state, config) -> OrderSide | None` and `should_exit(state, position, config) -> str | None` with the matrix's rules and exit priority. Name the reason constants `ENTER_FOLLOW`, `ENTER_FADE`, `EXIT_STOP`, `EXIT_TAKE_PROFIT`, `EXIT_SPENT`, `EXIT_MAX_HOLD`. The exit price is the bid for a long and the ask for a short. Cooldown is measured from `last_exit_ns`.
- [x] `platform/research/strategies/liquidation_cascade_strategy.py` (new) -- `LiquidationCascadeStrategyConfig` and `LiquidationCascadeStrategy`.
  - **Config fields:** `instrument_id`; the indicator's four fields; `sides=("short",)`; `mode="follow"`; `min_episode_notional="0"`; `entry_timeout_s`; `max_entries_per_episode=1`; `cooldown_s`; `trade_size`; exactly one of `stop_pct`/`stop_atr_multiple` (validated in `__init__`); `atr_period=14`; `take_profit_r=None`; `exit_on_spent=True`; `max_hold_s`; `ofi_confirm=False`; `ofi_window=50`; `max_daily_loss=None`; `signal_log_path=None`; `bar_type=None`. `bar_type` is accepted because the runner injects it; ATR uses `<iid>-1-MINUTE-MID-INTERNAL` bars built from quotes and subscribed only when `stop_atr_multiple` is set.
  - **Subscriptions:** `DataType(Liquidation)` with `client_id=ClientId(LIQUIDATION_CLIENT_ID)` and the `instrument_id`, plus quote ticks.
  - **Feeding:** every liquidation and each 1 s timer (aligned to whole UTC seconds) feeds the indicator. Each update builds the state, asks exit first and then entry, acts, and writes one record. Track position, realised PnL per UTC day, episode entries and exits.
  - **Writer:** a private JSON-lines writer with `bots.strategies.signal_log`'s line format (compact `json.dumps`, one flushed line, error after close).
    - The `start` record has `strategy: "liquidation_cascade"`, `bot_id` (the `order_id_tag`), `instrument_id`, `ts_ns` (clock rounded to µs as dummy does), and every config field.
    - Each cycle record: `kind` (`liquidation`|`tick`), `bot_id`, `instrument_id`, `ts_ns`, `rate_long`, `rate_short`, `baseline`, `intensity`, `direction`, `active`, `spent`, `decision` (`enter_short`|`enter_long`|`exit`|`none`|`not_ready`), `reason`. `venue_event_id` appears on `liquidation` records only.
- [x] `platform/research/application/ports.py`, `backtest_runner.py` -- Add `RunSpec.data == "liquidations"`: the `seconds` kind's derived quotes plus `BacktestDataConfig(catalog_path=spec.catalog_path, data_cls=<Liquidation import path>, instrument_id=iid, client_id=LIQUIDATION_CLIENT_ID, bounds)`. Refuse it for an id without `has_liquidation_feed`.
- [x] `platform/research/application/liquidations.py` (new) -- Add `replay_cascade(rows, window_s, baseline_s, intensity_threshold, decay_ratio, end_ns) -> list[CascadeEpisode]` (start, end, direction, peak rate, notional units). It replays `LiquidationCascade`, with an `advance` each second, and is the base that 33.13's `cascade_episodes` extends. Add `read_liquidations(catalog_path, iid, start_ns, end_ns)`, which is day-sliced over `query_liquidations`.
- [x] `platform/research/strategies/backtest_liquidation_cascade.py` (new) -- `run(symbol, start, end, catalog_path=None, **params)` builds `RunSpec(data="liquidations")` and calls `NodeRunner().run`, as in `backtest_candle_pattern.py`.
- [x] `platform/research/application/gallery.py`, `notebooks/08_strategy_gallery.py` (+ `.ipynb` via `make notebooks`), `notebooks/_params.py` if needed -- Add four cascade specs: follow/fade × short-only/both. They use `data="liquidations"` on the `CASCADE_INSTRUMENT` setting (default `BTCUSDT-LINEAR.BYBIT`), with `CASCADE_PARAMS` overrides and the unchanged fill/fee/latency axes.
  - Next to every cascade figure, print the sample (days, episodes from `replay_cascade`).
  - Update the "Thirteen" count.
  - A missing feed id is stated, never crashed on.
- [x] `platform/research/tests/fixture_catalog.py` -- Write `Liquidation` rows for `BTCUSDT-LINEAR.BYBIT` inside the window: background, then a burst, then a decay. Keep every other fixture count and test unchanged (any test that counts directories is updated).
- [x] `platform/research/tests/test_cascade_rules.py` (new) -- Cover every branch of both functions: sides, both modes, each refusal, each exit reason, the priority, and both stop kinds (TEST-01, hand-computed prices).
- [x] `platform/research/tests/test_liquidation_cascade_strategy.py` (new) -- Cover config validation (both stops or neither is refused; unknown field refused) and the precision rescale and refusal. The planted test builds a tmp catalog (instrument, snapshots → quotes, background + 90 s LONG burst + decay) and calls `backtest_liquidation_cascade.run`. It asserts exactly one SELL entry with a fill time inside the burst and one closing BUY, and that the signal log holds exactly one `enter_short`/`follow` and one `exit`/`spent` record.
- [x] `platform/research/tests/test_liquidations.py` (new), `test_notebooks.py` -- `replay_cascade` on hand-built rows; the 08 notebook env gains `NOTEBOOK_CASCADE_PARAMS` small enough for the fixture to hold one episode.

*Bots*
- [x] `platform/bots/infrastructure/liquidation_data_client.py` (new) -- Add `LiquidationDataClientConfig(LiveDataClientConfig)` (`redis_url`), `LiquidationDataClient(LiveMarketDataClient)` and a factory builder `liquidation_client_factory(status) -> type[LiveDataClientFactory]`.
  - **Feed task:** `_connect` starts it. It subscribes `liquidations:raw` with `redis.asyncio`, decodes each array entry through `Liquidation.from_dict`, and calls `_handle_data(CustomData(DataType(Liquidation), liq))` only for instrument ids subscribed through `_subscribe`/`_unsubscribe`.
  - **Status:** a shared `LiquidationFeedStatus` (`connected`, `disconnected_since_ns`, `last_data_ns`).
  - **Failures:** reconnect with bounded backoff. Ledger failures per the Always rules. `_disconnect` cancels the task.
- [x] `platform/bots/infrastructure/nautilus_host.py` -- Changes:
  - `STRATEGIES["liquidation_cascade"]` holds the two research string paths.
  - A `LIQUIDATION_STRATEGIES` frozenset.
  - `build_node` adds the `LIQUIDATIONS` data-client config and factory only when a bot uses such a strategy.
  - `_importable_strategy` passes `signal_log_path` for it.
  - `StrategyCacheReader` for those bots reports `min(strategy.last_data_ns, status.disconnected_since_ns)` while disconnected.
  - `check_strategy` refuses a cascade bot on an id without `has_liquidation_feed`.
- [x] `platform/bots/infrastructure/cache_reader.py` -- Add the optional status argument.
- [x] `platform/bots/signal_replay.py` -- Replay cascade bots too (by their configured strategy; `--strategy dummy|liquidation_cascade` filters). The data is derived quotes plus the catalog's `custom_liquidation` (client id `LIQUIDATIONS`). The strategy config is built with exactly the host's fields, and `check_same_config` is per strategy. The dummy path stays byte-identical.
- [x] `platform/bots/config.toml` -- Add a commented example `[[bots]]` with `strategy = "liquidation_cascade"` and `[bots.params]` for `BTCUSDT-LINEAR.BYBIT` (plus a commented `[venues.BYBIT]`).
- [x] `platform/bots/tests/test_liquidation_data_client.py` (new) and `test_liquidation_cascade_bot.py` (new):
  - The bridge against Redis at `REDIS_URL` (default 6379, skipped with a stated reason when unreachable): delivery to a subscribed iid, the other iid dropped, a bad entry ledgered and its sibling delivered, the disconnect status.
  - The `STRATEGIES` row; `build_node` adds the client only for a cascade fleet; the config example parses once uncommented; the non-LINEAR refusal; the cache-reader cap.
- [x] `platform/tests/test_boundaries.py` (`TRADING_NODE_HOSTS` + docstring), `tests/test_images.py` (`_STRING_PATH_IMPORTS`: the host and replay gain the research paths), `docker-compose.yml` (`bot_tui` mount `LiquidationCascadeStrategy.py`) -- the registrations.

*Verification*
- [x] `platform/verification/domain/cascade_parity.py` (new), `application/bot_parity.py` -- `_compare` dispatches on the `start` record's `strategy` key; no key means dummy and stays unchanged.
  - **Parsing:** a strict parse of cascade records.
  - **Pairing:** `tick` records pair by `ts_ns`, `liquidation` records by `venue_event_id`. The window and truncation rules are dummy's.
  - **Comparison:** floats use `signal_compare.relative`; booleans, `direction` and `decision` are exact.
  - **Classes:** `late_arrival` applies when the live log holds a liquidation written after a tick with a later `ts_ns`, and within `window_s + 1 s` before the cycle. `unexplained` covers everything else and fails. One-sided `liquidation` cycles are `live_only`/`replay_only` and fail.
  - Report via the existing JSON/text renderers, with a per-bot strategy label.
- [x] `platform/verification/tests/test_cascade_parity.py` (new) -- Hand-written logs: equal; a late arrival explained; an unexplained difference; one-sided; a malformed record refused.

*Docs (MR4, OPS-01)*
- [x] `platform/docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` (§2 row + strategy/backtest note), `docs/DATA_DICTIONARY.md` (§2.1 indicator, §2.12 the 08 entry, the signal-log record kinds where §1.22/bot logs are described), `docs/DATA_INTEGRITY_AUDIT.md` (D-173 onward). The audit rows cover the side-direction mapping, the live-feed dependency on the Bybit collector, the 1 s exit granularity Known limit, and the clamped late event plus `late_arrival` parity), `docs/DEPLOY_CHECKLIST.md` (a 33-14 deferred action: add the bot to the VPS paper config, rebuild and restart `live-paper`, and verify the signal log and `bots:status`), `bots/README.md` (strategy table, params, the live-feed dependency, paper-first), `docs/BOT_OPERATIONS.md` §3, `ARCHITECTURE.md` (`liquidations:raw` consumers). -- MR4/DESIGN-03.

**Acceptance Criteria:**
- Given a fleet with only dummy bots, when `build_node` runs, then no `LIQUIDATIONS` client is configured and every existing `bots/tests` and dummy parity test passes unchanged.
- Given a cascade bot's live log and its `signal_replay` log over the same segment, when `python3 -m verification.bot_parity` runs, then the cascade bot is reported with pairs, equal shares and classes, and the exit status is 0 only when nothing is `unexplained`.
- Given `make test`'s notebook run, when 08 executes on the fixture, then the cascade specs run, print their sample (days and episodes ≥ 1), and raise no warning.
- Given `research/strategies/liquidation_cascade_strategy.py`, when `test_boundaries.py` runs, then it imports no `bots`, and `liquidation_data_client.py` is the only new `nautilus_trader.live` importer.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 47: (high 1, medium 9, low 37)
- defer: 0
- reject: 9: (high 0, medium 3, low 6)
- addressed_findings:
  - `[high]` `[patch]` The cascade baseline EMA started at 0 and `initialized` came after one `baseline_s`, so intensity read ~1.6x high right after every start, giving false episodes. Now bias-corrected (`ema / (1 − exp(−elapsed/baseline_s))`), still call-frequency independent; kernel expectations recomputed and the planted test still holds one short closed on spent.
  - `[medium]` `[patch]` A half-open Redis socket kept the bridge "connected" forever. Now `socket_keepalive`, a 1 s connect timeout, `get_message(timeout)` and a PING deadline give detection within 60 s, through the existing disconnect path. The test uses a black-holing relay. Audit D-178.
  - `[medium]` `[patch]` Cascade parity explained every later baseline/intensity difference after any late row, masking defects. Replaced by a decaying bound `d0·exp(−(T−t0)/baseline_s)·(1+REL_TOL)`. Threshold crossings caused by that drift are `late_arrival`.
  - `[medium]` `[patch]` The replay's 1/s snapshot quotes make OFI confirmation, ATR, stops and fills differ by construction. Added the `quote_cadence` class for decision/reason differences over agreeing indicator fields (audit D-179, Known limit).
  - `[medium]` `[patch]` A repeated synthesized `venue_event_id` refused the whole bot. Records are now keyed by (id, occurrence).
  - `[medium]` `[patch]` Fade could buy into a resurging wave; it now requires spent and not rising. Follow could enter long on a brief direction flip; it now requires `direction == episode_direction`.
  - `[medium]` `[patch]` A denied or rejected entry consumed the episode's only entry. It is now rolled back for the entry order only.
  - `[medium]` `[patch]` DEPLOY_CHECKLIST's `BOT_SIGNAL_LOG_DIR` step hid that it turns on logs for every paper bot (~86 MB/bot-day). The fleet-wide effect is now stated, with the parity invocation, rollback, chown, warm-up hour, collector-dead caveat and log-check fixes.
  - `[medium]` `[patch]` (found while verifying the fixes) A row the live strategy refused as stale wrote no record, so parity would fail it as `replay_only`. It is now written at the clock with reason `stale_row`, and a late row's own record pair is `late_arrival` (test fails without the classifier change).
  - `[low]` `[patch]` Unscalable rows:
    - Strategy rows are now ledgered.
    - `replay_cascade` skips and counts them instead of aborting.
    - `read_liquidations` selects on `ts_init`, as the backtest does.
  - `[low]` `[patch]` Gallery and notebook:
    - The gallery tolerates a definition stored twice and states a missing one.
    - Notebook 08's duplicate print is gone and cascade labels carry instrument and data kind.
    - The planted test asserts that `replay_cascade` finds the strategy's episode.
  - `[low]` `[patch]` Strategy config and state:
    - Stale rows past the window are not fed (`stale_rows`).
    - `stop_pct` must be in (0,1) and `take_profit_r > 0`.
    - `bar_type` is restricted to quote-built `-MID-INTERNAL`.
    - A close on an earlier UTC day no longer rolls `_day` back.
    - The no-ATR restart fallback has a `Known limit:`.
    - One `DEFAULT_STOP_PCT` is shared by the CLI and gallery.
    - `kernel.clocks` constants replace local copies.
  - `[low]` `[patch]` Bridge:
    - An ingest or decode failure is ledgered per entry, not as a drop.
    - An instrument-less subscribe is ledgered.
    - The test kills only the named `liquidation-bridge` client.
  - `[low]` `[patch]` Host, replay and parity tests:
    - `build_node` refuses a cascade fleet without a status.
    - `signal_replay` refuses `--start` for cascade bots and lists only keys that really differ.
    - `_same_value`'s True==1 bug is fixed and tested.
    - Lines over 100 columns are wrapped.
  - `[low]` `[patch]` Docs:
    - Every `kernel/indicators.py` line ref in the catalog doc is refreshed.
    - The RunSpec kinds list now includes `liquidations`.
    - The "exact" EMA wording is corrected to "equal up to float rounding".
    - The floor's real size is stated.
    - research README 08 row updated.
    - bots README params table completed, with the D-133 quote interaction.
    - BOT_OPERATIONS backtest pointer fixed.
    - DATA_DICTIONARY §1.22 cascade wording fixed.
    - ARCHITECTURE paper-fill venue corrected.
    - Audit D-173..D-177 rewritten to match.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 2, low 6)
- defer: 1: (high 0, medium 1, low 0)
- reject: 10: (high 0, medium 1, low 9)
- addressed_findings:
  - `[medium]` `[patch]` A tick was recorded at the detector's clock. Live, a row received after the tick's second can be fed before the timer's callback runs, so the tick record fell off the second grid: `live_only`/`replay_only`, or two ticks at one key refusing the whole bot. Fix: ticks are now recorded on the timer's second. Cascade parity classes a late tick's own pair (a live tick written after a record with a later `ts_ns`) `late_arrival`; the exact baseline makes every later record equal again. Tests: the strategy, plus parity with the same tick in order unexplained. Docs: DATA_DICTIONARY §1.22 and audit D-176.
  - `[medium]` `[patch]` The bridge decoded entries through `Liquidation.from_dict`'s `int()`. That silently truncated a float `size_units` (DATA-04), read numeric text, and passed a zero or negative size or price. The detector then raised inside `on_data`. Fix: `checked_entry` refuses each such entry, ledgered, and its siblings are still delivered (7 parametrised cases).
  - `[low]` `[patch]` A handler raising on one row lost the message's later rows uncounted. Rows are now delivered one by one, each failure ledgered.
  - `[low]` `[patch]` The bridge served `Liquidation` rows to a subscription of any data type. It now ledgers and refuses a non-`Liquidation` subscribe.
  - `[low]` `[patch]` `replay_cascade` ticked first at the window start; the strategy's timer first fires one second after the whole second at or before it. It now uses that first tick. The planted test now asserts that the episode start equals the strategy's first active record exactly.
  - `[low]` `[patch]` `read_liquidations` let `query_liquidations`' disagreeing-duplicate `ValueError` escape unrecorded. It is now recorded at `research.liquidations.duplicate` and raised (test).
  - `[low]` `[patch]` The sample keeps a venue event stored twice once, while the backtest streams both copies. This is now a module `Known limit:` with its upgrade path.
  - `[low]` `[patch]` A log with no `start` record was refused as "no parity for strategy None". It now goes to the dummy path, which names the missing start as before 33.14.

## Design Notes

- **Why time is fed on a 1 s timer.** A cascade is spent when liquidations *stop*, so event-only updates would never see it. Ticks on whole UTC seconds give both live and replay the same `ts_ns` grid, so parity pairs them without the dummy's start-grid rule.
- **Exact EMA.** Between breakpoints, a breakpoint being an arrival or the expiry `ts + window_s` of an entry, the window rate `r` is constant, so `b ← r + (b − r)·exp(−Δ/baseline_s)` is exact. Walking expiries in deque order before each update keeps `advance` call-frequency independent, which the backtest/live parity depends on.
- **Episode lifecycle.**
  - Starts when `active` turns true.
  - `peak_rate` is tracked from then on.
  - `spent` latches when `rate < peak × decay_ratio`.
  - Ends at the first update that is spent **and** not active.

  So every episode ends spent: exit-on-spent always gets its chance, and fade always sees an end.
- **Why the strategy subscribes with `client_id=LIQUIDATIONS`.** Without it, the DataEngine routes a custom-data subscription by the instrument's venue to the Bybit adapter, which has no liquidations. The backtest uses the same client id as its data-config label, so one subscription line works in both.
- **Status sharing without internals.** `liquidation_client_factory(status)` returns a factory subclass whose `create` closes over a `LiquidationFeedStatus` created in `build_node`. The host passes the same object to the cascade bots' cache readers. Neither the node's client registry nor any engine private attribute is read.
- **Known limits (in-code, with upgrade paths):**
  - Exits are judged at indicator updates (≥ 1/s) with market orders, not resting stops. Upgrade path: a reduce-only `STOP_MARKET`, as in `CandlePatternStrategy._ensure_stop`.
  - Notional is priced at the bankruptcy price (D-148).
  - The signal log grows unrotated (dummy's limit).
  - `late_arrival` is inferred from file order.

- **Implementation decisions (dev, 2026-10-06), deviations from the plan above:**
  - **Episode end.** The update that is spent and not active still carries the episode and sets `episode_ended=True`. The reset (peak, spent, `episode_*`) happens at the next update, so neither exit-on-spent nor fade can miss an end that coincides with the spent update.
  - **Follow-only gates.** `exit_on_spent` applies to follow positions only; a fade position is entered on spent and would otherwise close at once. `entry_timeout_s` and `rising` are follow-only too. `min_episode_notional` gates both modes.
  - **Other choices:**
    - `replay_cascade` also takes `start_ns`/`precisions` and refuses inexact rows.
    - `stop_pct`/`take_profit_r` are floats converted via `Decimal(repr(x))`.
    - Exits are judged before the detector is warm, so a position found open after a restart keeps its exits.
    - `Liquidation.notional_units_at(pp, sp)` was added for the exact rescale.
  - **Feed status.** `LiquidationFeedStatus` is created by `bots/__main__.py` and passed to `build_node(..., liquidation_status=)` and to `cache_reader_for`, so `build_node`'s return shape is unchanged. The bridge client has its own `Venue("LIQUIDATIONS")`, so it is never the default route. Redis-py's silent reconnect is disabled, so every drop is visible.
  - **One builder for host and replay.** `nautilus_host.strategy_params(bot, log_path)` builds the strategy config for both the host and `signal_replay`, and `signal_log_path` is a reserved `[bots.params]` key. The replay reads its paths from `STRATEGIES`, so only the host gains a `_STRING_PATH_IMPORTS` entry.
  - **Cascade parity.**
    - It refuses a replay starting after the live start.
    - A late row is identified via the replay's `ts_init` for the same `venue_event_id`.
    - Baseline/intensity differences are explained by any earlier late row, because the EMA's memory outlasts `window_s`.
    - A differing `reason` with an equal decision is informational.
    - Dummy JSON gains `"strategy": "dummy"` and the text report a `[dummy]` label; dummy classification is unchanged.
  - **Compose.** `live-paper` gains `BOT_SIGNAL_LOG_DIR: "${BOT_SIGNAL_LOG_DIR:-}"` (empty by default) so the deferred operator step can enable the signal log.
  - **Tests edited.** `test_candle_pattern_bot.py::test_every_string_path_strategy_builds_from_the_keys_the_host_passes` gains per-strategy minimal params. `test_ad8_boundary.py` allows the bridge only `nautilus_trader.live.data_client`/`factories`.
  - **Audit.** Rows D-173..D-177.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests research/tests verification/tests tests -q -p no:cacheprovider` -- expected: all pass, no warnings. The pre-existing `test_legacy_names::test_only_published_language_keeps_a_legacy_name` failure is the baseline's.
- `cd platform && REDIS_URL=redis://127.0.0.1:6379 python3 -m pytest bots/tests -q -p no:cacheprovider` (with a throwaway `redis-server --port 6379` if none is running) -- expected: all pass, deselecting the TLS node test as usual.
- `cd platform && ruff check <touched> && ruff format --check <touched> && mypy <touched python files>` -- expected: clean, with no new mypy errors against baseline.

## Auto Run Result

Status: done

**Summary:** A follow-up review pass over the story's diff (`19908f4e8b..34848ad236`, `platform/` only) by the Blind Hunter and the Edge Case Hunter. Of 18 deduplicated findings: 8 patched, 1 deferred, 10 rejected. The story's implementation itself is as summarised in the first run (one `LiquidationCascade` definition; pure rules; the strategy; the `liquidations` backtest kind and the gallery; the `liquidations:raw` bridge; the cascade replay and parity; the docs).

**Files changed in this pass:**
- `platform/research/strategies/liquidation_cascade_strategy.py`: ticks are recorded on the timer's whole second. `_write_stale`'s `fields` is typed (a mypy error in the story's code).
- `platform/verification/domain/cascade_parity.py`: adds `late_ticks` and classes a late tick's own pair as `late_arrival`. The docstring is updated.
- `platform/verification/application/bot_parity.py`: a log without a `start` record goes to the dummy path.
- `platform/bots/infrastructure/liquidation_data_client.py`: adds `checked_entry`, per-row delivery (`_deliver_row`) and the non-`Liquidation` subscribe refusal. The docstring is updated.
- `platform/research/application/liquidations.py`: the first replay tick matches the strategy's timer. Adds the ledgered `DUPLICATE_SITE` and the duplicate-row `Known limit:`.
- Tests: `bots/tests/test_liquidation_data_client.py` (9 new cases), `research/tests/test_liquidation_cascade_strategy.py` (the late tick, and the exact episode start), `research/tests/test_liquidations.py` (the disagreeing duplicate), `verification/tests/test_cascade_parity.py` (the late tick explained, and in order unexplained).
- Docs: `platform/docs/DATA_DICTIONARY.md` (the signal log's `ts_ns`) and `platform/docs/DATA_INTEGRITY_AUDIT.md` (D-176 late tick and its tests).

**Review findings:**
- 8 patches applied (medium 2, low 6).
- 1 deferred: a bot stopped over `bots:control` cannot be started again in-process, because Nautilus refuses `START` from `STOPPED`. It is pre-existing for every hosted strategy and recorded in `deferred-work.md`. This also refuted the "warm detector after restart" finding, so no restart reset was added.
- 10 rejected:
  - Fade getting one update when spent and the end coincide, and an inactive unspent episode absorbing a later wave: both are the contract's lifecycle, rejected before too.
  - Exact fields excused after any earlier late row: both sides run the one detector, so equal floats with differing exact fields come only from input history.
  - The `min_episode_notional="0"` default: the spec's default.
  - The late-row match by `ts_init`: a row sharing a late row's `ts_init` is itself late.
  - Latent: the unsubscribe refcount (nothing unsubscribes) and the status before the first connect (the strategy's `last_data_ns` is 0 then).
  - Not worth a change: `require_flushed` probing snapshots only (rejected before), an empty segment passing, and a malformed `redis_url` retried forever.

**Verification:**
- `cd platform && python3 -m pytest kernel/tests research/tests verification/tests tests -q -k "not test_only_published_language_keeps_a_legacy_name"`: 2449 passed, 8 skipped, no warnings. The deselected test is the baseline's known failure.
- `REDIS_URL=redis://127.0.0.1:6379 python3 -m pytest bots/tests -q -k "not test_build_node_passes_redis_credentials_and_ssl_from_url"` against a throwaway `redis:8-alpine`: 355 passed.
- `ruff format`/`ruff check` are clean on every touched Python file. `mypy` reports no errors in the story's files; the remaining errors are pre-existing (`kernel/indicators.py`, `kernel/catalog_files.py`, `research/application/ports.py:118`).
- Non-vacuity: the late-tick parity test asserts the same tick written in order is `unexplained`. The bridge tests fail without the check (truncation or a raise) and without per-row delivery (the raise escapes `ingest`).

**Residual risks:**
- A late tick is inferred from file order, like a late row (the existing Known limit).
- `replay_cascade`'s first-tick alignment has no test that tells the two alignments apart; the planted test's exact episode start holds under both.
- The sample/backtest dedup difference is documented, not removed.
- The deferred in-process restart gap means an operator's `start` after a `stop` does nothing until the bot process restarts.

