---
title: 'Story 29.6: Bots pane shows each bot''s live take-profit, stop-loss and position details'
type: 'feature'
created: '2026-09-29'
status: 'done'
final_revision: '1745997fd368832b89d0842f811f5da9b7fc508b'
baseline_revision: '3a5e59c4a19dd69ada3af87dc40e9e2ab950c424'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** No bot rests a take-profit or stop-loss today (`DummyStrategy` enters with market orders and exits by reversing), and neither `bots:status` nor the Bots pane says whether a position is protected, where its exits sit, or what the position is. The operator cannot see at a glance which bot holds an open position with no stop.

**Approach:** `StrategyCacheReader` classifies the bot's own open and emulated orders into stop-loss / take-profit by the order's own properties; `bots:status` gains nine appended fields (prices as `str(Price)` strings); `DummyStrategy` gains optional `take_profit_bps`/`stop_loss_bps` bracket exits (paper `BotConfig` only); the Bots pane gains `entry`/`sl`/`tp` columns and a column header, Bot-detail gains two position lines; a committed churn fixture plus `make bots-churn-check` proves the whole chain live on the sandbox venue.

## Boundaries & Constraints

**Always:**
- Wire rule: `bots:status` fields are only appended after `updated_at`, in this order: `stop_loss`, `take_profit`, `entry_price`, `mark_price`, `position_qty`, `stop_loss_orders`, `take_profit_orders`, `open_orders`, `last_fill_at`. Every existing field stays byte-identical (replay test).
- Price integrity: the five price/qty fields are `str(Price)`/`str(Quantity)` or `null`, never `float`. Exit prices computed in `Decimal` and built with `Price.from_raw(int(d.scaleb(FIXED_PRECISION)), instrument.price_precision)`, never `Price(decimal, precision)`.
- Classification is by the order itself (reduce-only flag, type, side versus the open position), never the strategy class; reads are strategy-scoped (AD-11): `orders_open(instrument_id=, strategy_id=)` ∪ `orders_emulated(instrument_id=, strategy_id=)`, de-duplicated by `client_order_id`.
- Config keys extend `BotConfig` as optional (`int | None = None`), validated positive ints (`bool` rejected) so every existing `config.toml` parses unchanged; with both unset `DummyStrategy` behaves exactly as today.
- Use Nautilus built-ins: `order_factory.bracket(...)` for the two-leg entry; `cancel_all_orders(instrument_id)` for cancelling.
- TUI-01 (persistent `ListBox`, walker updated in place) and TUI-02 (every variable-length fixed-width cell through `fit()`).
- MR4: `platform/docs/DATABASE_SETUP.md`, `platform/docs/BOT_OPERATIONS.md`, `bots/README.md`, Makefile change in the same commit as the code. TEST-04: no new warnings.

**Block If:**
- The live `make bots-churn-check` run cannot be made to pass for a reason outside the repo that no code change can fix (e.g. dYdX mainnet market data unreachable from this box for the whole session). A failing run caused by our code is a defect to fix, never a block.

**Never:**
- Never park `awaiting-operator`: this story ends `done`.
- No bracket keys on `ExecConfig` (real-money/exchange-demo): contingent-order support on real venue exec clients is unverified; documented as a `Known limit:`.
- Never modify `nautilus_trader/` or `crates/`; no new dependency; no change to Story 27.8.
- Never rename or remove a `bots:status` field or reorder the existing thirteen.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Long, bracket resting | long 1.0, reduce-only SELL STOP_MARKET @90, SELL LIMIT @110 | `stop_loss "90.00"`, `take_profit "110.00"`, counts 1/1, `open_orders 2` | — |
| Short | short, reduce-only BUY stop above, BUY limit below | same classification with BUY as closing side | — |
| If-touched TP | reduce-only closing LIMIT_IF_TOUCHED / MARKET_IF_TOUCHED | TP at `trigger_price` | — |
| Stop-limit / trailing | reduce-only closing STOP_LIMIT, TRAILING_STOP_MARKET/LIMIT | SL at current `trigger_price` (follows the trail) | trailing stop with no trigger yet: counted, excluded from price pick |
| Emulated stop | STOP_MARKET held by `OrderEmulator` | counted as SL | — |
| Entry stop | non-reduce-only closing-side STOP_MARKET | not protection; counted in `open_orders` only | — |
| Wrong side | reduce-only order on the opening side | not protection | — |
| Scaled exits | two closing reduce-only LIMITs | TP = the one nearest the mid (entry when no mid), `take_profit_orders 2` | ties broken by `client_order_id` |
| Flat | no open position | all five price/qty fields `null`, SL/TP counts 0; `open_orders` still counts any open order | — |
| No mid yet | position, no MID price | `mark_price null`; other fields filled | — |
| No fills | fills.db has no row for bot | `last_fill_at null` | — |
| TUI pre-story msg | fields absent | `entry`/`sl`/`tp` cells `n/a`; detail lines `n/a` | never fabricated |
| TUI unprotected | position, `stop_loss_orders 0` | `sl` cell `none` in `warning` attr | — |
| TUI flat | `position_side "flat"` | blank entry/sl/tp cells | — |
| bps invalid | `take_profit_bps = 0`, `-5`, `true`, `1.5` | config load raises `ValueError` naming the file; `DummyStrategy.on_start` logs error and stops on a non-positive value | — |

</intent-contract>

## Code Map

- `platform/bots/infrastructure/cache_reader.py` -- `StrategyCacheReader.positions()`: the only Nautilus read; gains order classification.
- `platform/bots/application/ports.py` -- `PositionSnapshot` (extend with defaulted fields), `FillsStore` Protocol (add `last_fill_ns`).
- `platform/bots/infrastructure/fills_store.py` -- `SqliteFillsStore`; `fills(ts, bot_id, ...)` table, index `(bot_id, ts)`.
- `platform/bots/application/supervise.py:55` -- `build_status`, the only `bots:status` builder.
- `platform/bots/strategies/dummy.py` -- `DummyStrategyConfig`, `DummyStrategy._maybe_trade/_submit`; no `on_stop` today.
- `platform/bots/domain/config.py:92` -- `BotConfig`; `platform/bots/infrastructure/config.py:105` -- `_parse_bot` (explicit key mapping, `_reject_unknown_keys`).
- `platform/bots/infrastructure/nautilus_host.py:~392` -- `build_node` maps `BotConfig` → `DummyStrategyConfig`.
- `platform/bot_tui/bots_pane.py` -- pure formatters `format_bot_line`, `bot_detail_lines`, `fit`, `format_uptime`.
- `platform/bot_tui/app.py` -- `_PALETTE` (:221), `_refresh_bots_body` (:554), `_build_bot_row_widget` (:900, duplicates `format_bot_line`), `_highlighted_bot_id` (:922), `_build_bot_detail_body` (:946).
- `platform/bots/tests/test_replay.py` + `fixtures/replay_payloads.json` -- recorded pre-story `status` string compared byte-for-byte.
- `platform/bots/tests/test_bot_status.py`, `test_strategy.py`, `support.py`, `conftest.py` -- BacktestEngine harness helpers (`_engine`, `_quotes_and_deltas`, `_config`), `FakeBus`, `store` fixture.
- `platform/docker-compose.yml:354` -- `live-paper` service (host network, `bots.dockerfile`); `platform/bots/DEPLOY_CHECKLIST.md:125` standalone `compose run` form.

## Tasks & Acceptance

**Execution:**
- [x] `platform/bots/application/ports.py` -- append to `PositionSnapshot`: `entry_price`, `mark_price`, `position_qty`, `stop_loss`, `take_profit: str | None = None`, `stop_loss_orders`, `take_profit_orders`, `open_orders: int = 0`; add `last_fill_ns(bot_id) -> int | None` to `FillsStore` -- one consistent Cache read per heartbeat.
- [x] `platform/bots/infrastructure/fills_store.py` -- implement `last_fill_ns` (`SELECT MAX(ts) ... WHERE bot_id = ?`); update any test fake implementing `FillsStore`.
- [x] `platform/bots/infrastructure/cache_reader.py` -- classify per the matrix; `entry_price = instrument.make_price(position.avg_px_open)` (`Known limit:` avg_px_open is an f64 in the Nautilus core, stamped at the instrument's precision), `mark_price = str(MID Price)`, `position_qty = str(position.quantity)`; nearest-exit pick in `Decimal` from `Price.as_decimal()`.
- [x] `platform/bots/application/supervise.py` -- append the nine fields to `build_status` (`last_fill_at` from `fills.last_fill_ns`); docstring notes the append-only order.
- [x] `platform/bots/strategies/exits.py` (new) -- pure `exit_prices(side, mid, increment, take_profit_bps, stop_loss_bps) -> (tp, sl)` in `Decimal`, rounded to the increment away from the entry (long: TP ceiling, SL floor; short: reverse); plus `entry_with_exits(...)` building the `OrderList`: `order_factory.bracket(...)` when both legs are set, otherwise a MARKET entry (`ContingencyType.OTO`, `linked_order_ids=[leg]`) plus the one reduce-only leg (`parent_order_id=entry`) built exactly like `bracket` builds them.
- [x] `platform/bots/strategies/dummy.py` -- config gains `take_profit_bps`/`stop_loss_bps: int | None = None` (validated in `on_start`); flat entry uses the bracket path when either is set, mid from the last `QuoteTick` in `Decimal` (no quote → no entry); any own open/emulated order while flat or before a reversal → `cancel_all_orders(instrument_id)` and return (the flatten/entry happens on a later cycle); `on_stop` cancels own open orders if any. Unset keys: identical behaviour.
- [x] `platform/bots/domain/config.py`, `platform/bots/infrastructure/config.py`, `platform/bots/infrastructure/nautilus_host.py` -- `BotConfig` gains both keys (positive-int validation in `__post_init__`), `_parse_bot` passes them, `build_node` forwards them; `ExecConfig` untouched with a `Known limit:` comment.
- [x] `platform/bot_tui/bots_pane.py` -- one column-spec source: `bot_line_segments(row, stale, now) -> list[tuple[str | None, str]]` (attr, text); `format_bot_line` = joined texts; `bots_header_line()` aligned to the same widths; `entry`/`sl`/`tp` after exposure, before `up` (price cell `fit(…, PRICE_WIDTH=11)`, distance `fit(…, 6)`, e.g. `58,900.0 -1.8%`, thousands separators via `format(Decimal(s), ",")`); `BOTS_PANE_MIN_WIDTH` constant; `bot_detail_lines` gains two lines (qty/entry/mark/open orders; sl/tp with counts/last fill `format_uptime`-style `… ago` or `no fills yet`).
- [x] `platform/bot_tui/app.py` -- `("warning", "light red", "default")` palette entry; row markup built from `bot_line_segments`; Bots body becomes a `Frame(listbox, header=Text(header_line))` kept persistent (walker still updated in place); `_highlighted_bot_id` and tests unwrap it.
- [x] `platform/bots/tests/test_cache_reader_exits.py` (new) -- real `Cache` + BacktestEngine per the matrix (long, short, each stop and if-touched type, trailing stop after it moves, emulated stop, entry stop excluded, scaled TPs nearer + count 2, flat).
- [x] `platform/bots/tests/test_strategy.py`, `test_exits.py` (new), `test_config*.py`, `test_replay.py`, `test_bot_status.py` -- exit rounding table; bracket bot on the sandbox venue leaves exactly one SL + one TP at the expected rounded prices; reversal leaves no orphan; bps parse/validation; replay: first thirteen keys byte-identical to the recorded message, new keys appended in order.
- [x] `platform/bot_tui/tests/test_bots_pane.py`, `test_app_bots.py`, `test_app_bot_detail.py` -- both exits, unprotected (`warning` attr), flat, pre-story; header alignment with a long `bot_id` and a long price; row renders on one line at `BOTS_PANE_MIN_WIDTH`; docs state that width.
- [x] `platform/bots/tests/fixtures/config.churn.toml` (new), `platform/bots/tests/churn_check.py` (new, pure sequence checker + Redis subscriber CLI), `platform/bots/tests/churn_check.sh` (new), `platform/Makefile` (`bots-churn-check`), `platform/bots/tests/test_churn_check.py` (new) -- per the churn AC.
- [x] `platform/docs/DATABASE_SETUP.md`, `platform/docs/BOT_OPERATIONS.md`, `platform/bots/README.md`, `platform/bots/DEPLOY_CHECKLIST.md` -- new fields, the Bots-pane minimum width, the bps keys with an example, the churn check.

**Acceptance Criteria:**
- Given a bot with an open position and resting reduce-only exits, when the heartbeat publishes, then `bots:status` carries the matrix's values and the thirteen pre-story fields are byte-identical to the recorded message.
- Given `take_profit_bps` and `stop_loss_bps` both set on a sandbox `DummyStrategy`, when it first enters, then exactly one reduce-only STOP_MARKET and one reduce-only LIMIT rest at the expected rounded prices, and after a reversal no reduce-only order remains open.
- Given `bots/tests/fixtures/config.churn.toml` (one paper bot on `BTC-USD-PERP.DYDX` mainnet data, Sandbox exec, `trend_buy_threshold = 0.0`, `trend_sell_threshold = -1.0`, `ofi_confirm_threshold = -1e9`, `take_profit_bps = 5`, `stop_loss_bps = 5`, header comment "mechanics fixture, never deployed"), when `make bots-churn-check` runs, then it starts a local Redis if none answers on `CHURN_REDIS_PORT` (default 6399, a dedicated port so no other stack's Nautilus Cache is loaded), runs the `live-paper` image via `compose run` with the fixture mounted read-only over `/app/bots/config.toml`, a scratch `FILLS_DB_PATH` and host networking, and exits 0 only when within 15 minutes it sees in order: (1) long with non-null `stop_loss`/`take_profit`/`entry_price`/`mark_price`/`position_qty`, counts 1/1, stop < entry < take-profit; (2) flat with null exits, `closed_trades` above (1)'s and `open_orders == 0`; (3) a second long with fresh exits -- else non-zero naming the failed check; the bot container (and a Redis it started) are removed either way.
- Given the dev session, when the story finalizes, then `## Auto Run Result` records the command, its exit status, and the captured protected and flat payloads rendered through `format_bot_line`.

## Spec Change Log

- **2026-09-29, dev (live churn check): exit legs are emulated, triggered on the last trade.** The first `make bots-churn-check` run showed both venue-resting bracket legs rejected by the live Sandbox: `OrderRejected(... reason='REDUCE_ONLY STOP_MARKET SELL order would have increased position')` (and the same for the LIMIT), right after the entry filled. Root cause (nautilus_trader/backtest/engine.pyx `process_order` + execution/engine.pyx): the Sandbox matching engine processes OTO children synchronously within the `SubmitOrderList`, while the live `ExecEngine` applies the entry's fill (and opens the position) asynchronously from its queue, so the children's reduce-only check finds no position. BacktestEngine tests are synchronous and never saw it. Fix within the Always rules (still `order_factory.bracket`): both legs carry `emulation_trigger` (`bots/strategies/exits.py` `EXIT_EMULATION_TRIGGER`), so the `OrderEmulator` holds them until the entry's fill has been applied and releases one only when the market reaches it. `BID_ASK` was tried first (second run passed) but the emulator also reads the node Cache's L2 book, whose best bid sat ~2% under the market on the dYdX feed (third run: stop at 83,792 released at a "bid" of 82,133, filled at 83,832, milliseconds after entry), so `LAST_PRICE` is used. Known limits (documented in exits.py, README, BOT_OPERATIONS): exits live in the node process; the emulator releases a matched take-profit LIMIT as a MARKET order (fills at the book, can fill partly); trade-triggered; exits anchored to the entry-time mid, so a slipped entry can land beyond an exit. The churn fixture's `trade_size` is the instrument increment `0.0001` because the Sandbox (L1_MBP) matches market orders against the top level only and dYdX BTC often rests 0.0001 dust on top; a 0.001 run left a released take-profit 0.0001-filled and the position stuck (fourth run, failed on check 1 after 15 min). Tests: `test_exits.py` asserts the legs' emulation trigger and an un-emulated entry; `test_strategy.py` asserts the resting exits are emulated and feeds trades to the filled-exit test.

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 2, medium 3, low 5)
- defer: 1: (high 0, medium 1, low 0)
- reject: 20: (high 0, medium 3, low 17)
- addressed_findings:
  - `[high]` `[patch]` A reversal cancelled the exits and then held the unprotected position indefinitely when the signal faded by the next cycle. `DummyStrategy._trade_with_exits` now remembers the committed reversal side (`_reversal_side`) and sends the flatten on the next cycle regardless of the signal; test `test_a_reversal_whose_signal_fades_still_flattens` (fails on the old code).
  - `[high]` `[patch]` The flat-with-orders backstop could cancel a just-sent entry's legs while the bot still read flat (entry accepted, fill not yet applied), leaving the fill unprotected. New `_entry_working()`: a flat bot waits while any own order is a non-reduce-only entry or a leg whose parent entry is not closed; test `test_a_flat_bot_keeps_the_legs_of_an_entry_still_working` (fails on the old code).
  - `[medium]` `[patch]` `take_profit_bps` had no upper bound, so a short's take-profit could land at or below zero, and a floored stop could reach zero. Both keys are now bounded by `MAX_EXIT_BPS = 9_999` (renamed from `MAX_STOP_LOSS_BPS`) in `BotConfig` and `DummyStrategy._exits_valid`, which now also rejects `bool`. `_enter` logs an error and skips the entry when an exit computes to at most zero. Tests were added in `test_config.py` and `test_strategy.py`.
  - `[medium]` `[patch]` One malformed price string on `bots:status` raised `InvalidOperation` in the TUI render. `format_price`/`format_distance` now go through `_decimal()`, which returns `n/a` or blank and never raises; tested.
  - `[medium]` `[patch]` Every order event triggered a publish with no rate limit. A trailing stop's `OrderUpdated` burst meant one Cache read, two `fills.db` queries and one publish per event on the node loop. Added `MIN_PUBLISH_SPACING_SECONDS = 0.1` in `supervise.py`; test `test_an_order_event_burst_is_coalesced_by_the_publish_spacing`.
  - `[low]` `[patch]` `format_price` switched to scientific notation below 1e-6. It now formats with `",f"`; tested.
  - `[low]` `[patch]` `churn_check.py` crashed on a non-JSON or non-dict message, and it lost the captured payloads when Redis dropped. It now skips such messages with a stderr note and writes `--out` in `finally`; tests updated and added.
  - `[low]` `[patch]` `churn_check.sh` reported a missing host redis-py as "Redis never answered". It now checks for redis-py up front with a clear message.
  - `[low]` `[patch]` `DummyStrategy._exits_valid` accepted `True`. It is now aligned with `BotConfig`'s rule (see above).
  - `[low]` `[patch]` Exit legs are sized to the entry's full quantity, and resizing after a partial entry fill was undocumented. Added to the `Known limit:` in `bots/strategies/exits.py`.

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 1, medium 1, low 4)
- defer: 0
- reject: 20: (high 0, medium 3, low 17)
- addressed_findings:
  - `[high]` `[patch]` A bracket-mode reversal flattened with a `trade_size` market order. When the position was not `trade_size`, for example after a partly filled flatten, the next cycle cancelled that flatten's own remainder as "resting orders" and sent another full `trade_size`. The bot could then flip into an opposite position with no exits, or leave part of the long unprotected. `DummyStrategy._flatten` now sends one reduce-only market order sized to `abs(portfolio.net_position)`, and `_reversal_side` holds until the bot is flat, so any remainder is cancelled and re-sent for what is left. New test `test_a_reversal_flattens_the_whole_position_not_trade_size` fails on the old code. The fade test now asserts the flatten is reduce-only. README updated.
  - `[medium]` `[patch]` `churn_check.sh` reused whatever Redis answered on `CHURN_REDIS_PORT`, including its own container left behind by an interrupted run (SIGKILL skips the trap). The bot would then inherit that run's Nautilus Cache position, with no exits. A leftover container of that name also made `docker run` fail on the name conflict. The script now runs `docker rm -f` on its own Redis container before probing. Header comment, BOT_OPERATIONS and DEPLOY_CHECKLIST updated.
  - `[low]` `[patch]` `churn_check.protected_long_problems` crashed with `InvalidOperation` on a non-numeric price. That is now a named check failure; tested.
  - `[low]` `[patch]` `format_distance` gave "+100.0%" (7 characters) at 100% or more, which the 6-wide cell truncated. It now uses whole percents from 99.95% up; tested.
  - `[low]` `[patch]` The `dummy.py` module docstring said nothing changes with both bps keys unset. `on_stop`'s cancel applies to every bot, and the docstring now says so.
  - `[low]` `[patch]` The `PositionSnapshot` docstring now states the nearest-exit pick falls back to the entry price when there is no mid.

## Design Notes
- **Review pass (2026-09-29):** in bracket mode a reversal is committed once its exits are cancelled (`_reversal_side`), and a flat bot never cancels the legs of an entry that is still working (`_entry_working`). Both close windows where a position could otherwise sit without the exits it was built with.
- **Prior attempt:** an earlier dev run was stopped mid-way; its WIP is on branch `29-6-prior-attempt` (parent = baseline). Reuse it: `git read-tree -m -u HEAD 29-6-prior-attempt` + `git reset -q`, then re-verify every task and AC.

- `bots:status` keys are appended after `updated_at`, so `json.dumps` of the first thirteen keys is byte-identical to the recording: the replay test compares that prefix and asserts the appended key order.
- Protective orders are cancelled before a reversal's flattening order is sent, one cycle apart (the existing reversal already takes two steps). This avoids a race where a stop fills while the flatten is in flight. Nautilus cancels the sibling leg of an OUO pair when one leg fills (the `OrderEmulator`'s order manager for the emulated legs, see the Spec Change Log), so a TP or SL exit leaves no orphan. The flat-with-open-orders guard is the backstop that enforces "no orphan".
- `on_stop` cancels the bot's own resting orders, so a stopped bot's position shows `sl none` in the warning colour: the truth, not a hidden risk. `Known limit:` a restart does not re-arm exits. The upgrade path is re-placing the protective legs on start when a position exists.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. bots/tests bot_tui/tests -q` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: no new failures versus the baseline.
- `make -C platform bots-churn-check` -- expected: exit 0 within 15 min, payloads captured.
- Throwaway `ruff==0.15.16`/`mypy==1.20.2` venv on touched files -- expected: no new findings versus `git archive HEAD`.

## Auto Run Result

Status: done

**Summary.** `bots:status` gains nine appended fields: `stop_loss`, `take_profit`, `entry_price`, `mark_price`, `position_qty`, `stop_loss_orders`, `take_profit_orders`, `open_orders` and `last_fill_at`. The prices and quantity are `str(Price)`/`str(Quantity)` or null. They come from a strategy-scoped classification of the bot's own open and emulated orders in `StrategyCacheReader`, and the payload is also published on the bot's order events, spaced at least 0.1 s apart.
- `DummyStrategy` gains optional `take_profit_bps`/`stop_loss_bps` bracket exits through `order_factory.bracket`. The legs are emulated and trigger on the last trade price (see Spec Change Log).
- The Bots pane gains `entry`/`sl`/`tp` columns, an aligned header and two Bot-detail lines.
- A committed churn fixture plus `make bots-churn-check` proves the chain live. The work reused the stopped prior attempt's WIP (`29-6-prior-attempt`), re-verified task by task.

**Files changed.**
- `platform/bots/application/ports.py`: `PositionSnapshot` exit and position fields; `FillsStore.last_fill_ns`.
- `platform/bots/application/supervise.py`: appends the nine fields; publishes on order events with `MIN_PUBLISH_SPACING_SECONDS`.
- `platform/bots/infrastructure/cache_reader.py`: classifies protective orders by the order itself and picks the exit nearest the mid.
- `platform/bots/infrastructure/fills_store.py`: `last_fill_ns`.
- `platform/bots/infrastructure/config.py` and `platform/bots/infrastructure/nautilus_host.py`: parse and forward the bps keys.
- `platform/bots/domain/config.py`: `BotConfig` bps keys bounded by `MAX_EXIT_BPS`; Known limit on `ExecConfig`.
- `platform/bots/strategies/exits.py` (new): Decimal exit pricing rounded away from the entry, `entry_with_exits`, the emulation trigger and its Known limits.
- `platform/bots/strategies/dummy.py`: the bracket entry; cancel before a reversal (now committed); flat backstop that spares a working entry; `on_stop` cancel.
- `platform/bot_tui/bots_pane.py`: `bot_line_segments`, `bots_header_line`, `entry`/`sl`/`tp` cells, `BOTS_PANE_MIN_WIDTH`, detail lines, and price formatting that never raises.
- `platform/bot_tui/app.py`: `warning` palette entry, segment markup, and a persistent header `Frame` around the `ListBox`.
- `platform/bots/tests/*`: new `test_cache_reader_exits.py`, `test_exits.py` and `test_churn_check.py`; extended `test_strategy.py`, `test_bot_status.py`, `test_config.py`, `test_replay.py`, `test_node.py` and `test_ports.py`; new `fixtures/config.churn.toml`; replay fixture.
- `platform/bot_tui/tests/*`: row, header, detail and app tests.
- `platform/bots/tests/churn_check.py` and `churn_check.sh`, `platform/Makefile` (`bots-churn-check`) and `.gitignore` (the scratch payload directory).
- Docs: `platform/docs/DATABASE_SETUP.md`, `platform/docs/BOT_OPERATIONS.md`, `platform/bots/README.md` and `platform/bots/DEPLOY_CHECKLIST.md`.

**Follow-up review (2026-09-29).** A fresh adversarial and edge-case pass on the whole story diff found 6 patches (high 1, medium 1, low 4), 0 deferred and 20 rejected (see the second Review Triage Log entry). The high one: in bracket mode a reversal's flatten is now one reduce-only market order sized to the whole open position, re-sent for any remainder until flat. It used to be a `trade_size` order whose partial fill could be cancelled and re-sent into an unprotected opposite position. Files changed in this pass:
- `platform/bots/strategies/dummy.py`: `_flatten`; `_reversal_side` holds until flat; docstrings.
- `platform/bots/tests/test_strategy.py`: new test for flattening the whole position; the fade test asserts reduce-only.
- `platform/bots/tests/churn_check.sh`: removes its own leftover Redis before probing.
- `platform/bots/tests/churn_check.py` and `test_churn_check.py`: a non-numeric price is a named failure.
- `platform/bot_tui/bots_pane.py` and `tests/test_bots_pane.py`: distances of 100% or more fit the cell.
- `platform/bots/application/ports.py`: docstring.
- Docs: `platform/bots/README.md`, `platform/docs/BOT_OPERATIONS.md`, `platform/bots/DEPLOY_CHECKLIST.md`.

Verification for this pass:
- `cd platform && REDIS_URL=redis://127.0.0.1:6398 python3 -m pytest -o addopts="" --rootdir=. bots/tests bot_tui/tests -q -W error::DeprecationWarning`, with the two Redis-cache `test_node` tests deselected and run against a throwaway Redis: **579 passed**.
- Mutation check: the new flatten test fails when `_flatten` is swapped back to `_submit`.
- `ruff@0.15.16` check and format: clean on the touched files. `mypy@1.20.2`: no new findings; `kernel/indicators.py` errors predate this story.
- `make bots-churn-check` was **not** re-run. Its fixture never produces a short signal (`trend_sell_threshold = -1.0`), so the changed reversal path is not exercised by it. The run recorded below stands for the unchanged exit chain.

**Review findings.**
- 10 patches applied (high 2, medium 3, low 5); see the Review Triage Log.
- 1 deferred to `deferred-work.md`: the node Cache's dYdX book diverges from the market.
- 20 rejected (spec-sanctioned behaviour, known limits already documented, or noise).

**Verification.**
- `cd platform && REDIS_URL=redis://127.0.0.1:6398 python3 -m pytest -o addopts="" --rootdir=. bots/tests bot_tui/tests -q -W error::DeprecationWarning`, with the two Redis-cache `test_node` tests deselected as in `make test-live-paper`, run against a throwaway Redis: **576 passed**.
- `data_api/tests observability/tests kernel/tests tests`: 631 passed, 9 failed. The same 9 `data_api` tests fail at baseline because they need Redis on 6379.
- ruff check and format: no new findings (S608 in `fills_store.py` and S105 in `test_node.py` predate this story). mypy: no new findings in touched files.
- Mutation check: both high-severity regression tests fail on the pre-fix code.

**Live churn check.** Command (from the repo root): `make -C platform bots-churn-check`. **Exit status 0**, run on the final code on 2026-09-29, bot started 07:00:15 UTC, all 3 checks passed in about 13.7 minutes.
- In this run the second entry filled at 84,037 against an entry-time mid of 83,979.5, about 7 bps of slippage. That left its take-profit (84,022) below the entry, so check 3 kept failing until that position exited on its own trigger and the next entry priced correctly. That is the documented Known limit (exits anchored to the entry-time mid), and the gate's margin is thin because of it.
- An earlier run before the review patches also exited 0. Rendered through `format_bot_line` by the script:

```
  bot          pnl         symbol               mode  run side  exposure    entry        sl                  tp                  up          wr     
  churn-01     +     0.00  BTC-USD-PERP.DYDX    paper run long        8.39  83,937       83,878      -0.1%   83,962      +0.1%   up 5m44s    wr n/a  <- protected long
  churn-01     +     0.00  BTC-USD-PERP.DYDX    paper run flat        0.00                                                       up 7m09s    wr 0%   <- flat with no orphaned order
  churn-01     +     0.00  BTC-USD-PERP.DYDX    paper run long        8.40  84,006       83,924      -0.1%   84,009      +0.1%   up 13m39s   wr 0%   <- second protected long with fresh exits
```

Protected payload:

```json
{"bot_id": "churn-01", "strategy": "DummyStrategy", "symbol": "BTC-USD-PERP.DYDX", "mode": "paper", "running": true, "position_side": "long", "net_exposure": 8.39, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "win_rate": null, "closed_trades": 0, "started_at": 1790665215.176367, "updated_at": 1790665560.1055176, "stop_loss": "83878", "take_profit": "83962", "entry_price": "83937", "mark_price": "83920.0", "position_qty": "0.0001", "stop_loss_orders": 1, "take_profit_orders": 1, "open_orders": 2, "last_fill_at": 1790665559990326101}
```

Flat payload:

```json
{"bot_id": "churn-01", "strategy": "DummyStrategy", "symbol": "BTC-USD-PERP.DYDX", "mode": "paper", "running": true, "position_side": "flat", "net_exposure": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "win_rate": 0.0, "closed_trades": 1, "started_at": 1790665215.176367, "updated_at": 1790665645.1703548, "stop_loss": null, "take_profit": null, "entry_price": null, "mark_price": null, "position_qty": null, "stop_loss_orders": 0, "take_profit_orders": 0, "open_orders": 0, "last_fill_at": 1790665645067316174}
```

Second protected long:

```json
{"bot_id": "churn-01", "strategy": "DummyStrategy", "symbol": "BTC-USD-PERP.DYDX", "mode": "paper", "running": true, "position_side": "long", "net_exposure": 8.4, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "win_rate": 0.0, "closed_trades": 2, "started_at": 1790665215.176367, "updated_at": 1790666034.8781512, "stop_loss": "83924", "take_profit": "84009", "entry_price": "84006", "mark_price": "83966.5", "position_qty": "0.0001", "stop_loss_orders": 1, "take_profit_orders": 1, "open_orders": 2, "last_fill_at": 1790666034852012358}
```

The bot container and the script's Redis on port 6399 were removed afterwards.

**Residual risks.**
1. The churn gate is sensitive to entry slippage. At 5 bps, a market entry that fills more than 5 bps off the entry-time mid puts its take-profit on the wrong side until that position exits. The observed slippage (2–7 bps on a 0.0001 BTC order) is far wider than the real dYdX spread, which points at the deferred node-book divergence.
2. The emulated exits have Known limits, documented in `exits.py`:
   - they live in the node process;
   - a take-profit is released as a MARKET order and can fill below its price. The first exit of this run was a take-profit released at 83,975 that filled at 83,922, under the 83,937 entry.
3. A restart does not re-arm the exits of a position it inherits (Known limit, with its upgrade path documented).
