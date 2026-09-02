---
title: 'Story 4.7: Bot-detail trades blotter and PnL-over-time chart'
type: 'feature'
created: '2026-09-02'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: true
context: []
warnings: []
baseline_revision: '656e936879a4e69717df60281e4eca7a22d31c2d'
final_revision: '0efaf0df97504315d14604f0692d5c269d0b312e'
---

<intent-contract>

## Intent

**Problem:** Bot-detail (Story 4.5) shows only the live-snapshot header; an operator must leave the TUI to see per-fill history or PnL trend, even though Story 4.6 already publishes `bots:history:{bot_id}:day|week|month|all` to Redis for exactly this.

**Approach:** Add a GET-polling state module (`bot_history_state.py`, since these are plain keys, not pub/sub channels — unlike every existing `*_state.py`) and extend Bot-detail's body/keybindings in `app.py` to render two more bordered regions (trades blotter, PnL sparkline) below the existing snapshot, with `t` cycling the range preset.

## Boundaries & Constraints

**Always:**
- New state module polls all 4 `bots:history:{bot_id}:{range}` keys via plain Redis `GET` (not `pubsub`/`listen`) every 15s (half of `trade_history.py`'s 30s publish cadence).
- Staleness/never-fetched timeout = 90s (3x poll cadence — same ratio `bots_state._BOT_STALE_SECONDS=15.0` uses against its 5s heartbeat, applied to history's own cadence, not that literal constant).
- Distinguish three states per AD-10/`bot_status.py`'s existing "`None` means genuinely unknown" convention: (a) unavailable — never fetched or past the staleness timeout, renders `"history unavailable"`; (b) fetched but genuinely empty (`trades: []`), renders a distinct `"no trades yet"`; (c) has data — renders it. Do not conflate (a) and (b).
- New pure formatting functions (blotter row lines, sparkline text, range-cycle) go in `bots_pane.py` (where `bot_detail_lines()` already lives — Bot-detail's pure-logic home, not a new `bot_detail.py`), matching `coin_detail.py`/`coin_detail_state.py`'s established pane/state split.
- `t` cycles day→week→month→all→day (never free-form), footer-echoes the new range, redraws both new regions for it.
- `o` deep-link: build a URL and `webbrowser.open()` it exactly like `_open_dashboard_chart()`/`coin_detail.dashboard_chart_url()` already do for coins (same try/except + footer echo) — do not invent a new open/echo mechanism.
- Add any new file(s) to `test_ad8_boundary.py`'s `_READER_MODULES` list.
- No Nautilus/`live_paper` imports anywhere in `bot_tui` (AD-8) — Redis only.

**Block If:** none identified — a spec gap here should be resolved via `## Design Notes`' documented judgment call, not a HALT, since this is a well-precedented extension of existing patterns.

**Never:**
- Build or modify `troll/ml_signals/dashboard.py` or any web-dashboard route. It has no per-bot page today (confirmed: its route list has none) — that gap pre-dates this story and is not this story's job to close, mirroring how the Coin-detail deep-link already ships as pure URL-open without this codebase guaranteeing richness on the far end. `o` only needs to construct `{DASHBOARD_BASE_URL}/bot/{bot_id}` and open it.
- Touch the existing snapshot `LineBox`'s own construction/logic (Story 4.5) — only wrap it inside a new outer `Pile` alongside the two new regions.
- Add a new generic `_open_dashboard(kind, id)` abstraction for what is only two call sites (coin, bot) — duplicate the ~3-line pattern (YAGNI, this codebase's own established duplicate-small-helpers convention).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Current range's key has `trades`/`pnl_series` | Blotter lists fills (ts, side, price, qty, realized_pnl); sparkline renders bars from `pnl_series` | No error |
| Never fetched | Poll loop hasn't completed a first successful GET for this bot+range | Both regions show `"history unavailable"` | No crash, no stale placeholder data |
| Stale | Entry exists, `updated_at` older than 90s | Both regions show `"history unavailable"` | No crash |
| Empty-but-fresh | `trades: []`, `pnl_series: []`, fresh `updated_at` | Blotter/sparkline show `"no trades yet"`, distinct from "unavailable" | No error |
| `t` pressed | Bot-detail open, range = "day" | Range becomes "week"; footer echoes it; both regions redraw for new range's state | No error |
| `o` pressed | Bot-detail open | `webbrowser.open()` called with the bot's URL; footer echoes the URL | Browser-open exception is swallowed (matches Coin-detail); footer still echoes attempt |

</intent-contract>

## Code Map

- `troll/bot_tui/app.py` -- `_build_bot_detail_body()` (~390-421) wraps snapshot `LineBox` + 2 new regions in an outer `Pile`; `_handle_bot_detail_key()` (~831-840) gains `t`/`o`; schedule `bot_history_state`'s poll task the same way `bots_state`'s listener is already scheduled.
- `troll/bot_tui/bot_history_state.py` (NEW) -- `poll_loop(redis_url, bot_id, interval=15.0)`, module dict keyed by `(bot_id, range)`, `is_stale(...)` (90s), mirrors `bots_state.py`'s shape but `GET`, not `pubsub`.
- `troll/bot_tui/bots_pane.py` -- add `HISTORY_UNAVAILABLE_TEXT`, `NO_TRADES_YET_TEXT`, `trades_blotter_lines(entry)`, `pnl_sparkline_text(entry)`, `next_range(current)` alongside the existing `bot_detail_lines()`/`COLD_OPEN_TEXT`.
- `troll/bot_tui/coin_detail.py` -- read-only reference for `dashboard_chart_url()`'s exact URL-building/open pattern to mirror for the bot URL.
- `troll/bot_tui/tests/test_ad8_boundary.py` -- add `bot_history_state.py` to `_READER_MODULES`.
- `troll/bot_tui/tests/test_bots_pane.py`, `test_app_bot_detail.py` -- extend for the new functions/regions/keys.

## Tasks & Acceptance

**Execution:**
- [x] `troll/bot_tui/bot_history_state.py` -- new GET-poll state module -- Story 4.6's keys are GET, not pub/sub; no existing module fits. Built with an explicit `open_bot()`/`close_bot()` tracked-bot lifecycle (mirroring `coin_detail_state.py`'s shape, not `bots_state.py`'s accumulate-everything shape) since history is only ever rendered for the one bot open in Bot-detail -- a design refinement beyond the spec's Code Map (which didn't specify this), justified in-line in the module's own docstring.
- [x] `troll/bot_tui/bots_pane.py` -- add pure formatters (blotter lines, sparkline text, unavailable/empty text, range cycle, `dashboard_bot_url`) -- keeps Bot-detail's pure logic in its established home
- [x] `troll/bot_tui/app.py` -- extend `_build_bot_detail_body()` + `_handle_bot_detail_key()` + schedule the poll task -- wires the two new regions and `t`/`o` in; also updates `_open_bot_detail()` (resets range, calls `open_bot()`) and adds `_BOT_DETAIL_BLOTTER_HEIGHT`
- [x] `troll/bot_tui/tests/test_ad8_boundary.py` -- add new module to `_READER_MODULES`
- [x] `troll/bot_tui/tests/test_bot_history_state.py` (new, 10 tests), extend `test_bots_pane.py` (+12 tests) + `test_app_bot_detail.py` (+7 tests, plus 1 existing footer-hint assertion updated for the now-legitimate `t`/`o` hints) -- covers the I/O matrix above directly, plus `next_range`'s full cycle

**Acceptance Criteria:**
- Given Bot-detail is open, when it renders, then it shows 3 stacked bordered regions: snapshot (unchanged from 4.5), trades blotter, PnL sparkline -- `test_build_bot_detail_body_has_three_stacked_bordered_regions`
- Given the PnL region, when `t` is pressed, then the range cycles day→week→month→all→day and both new regions redraw for it -- `test_t_key_cycles_range_and_echoes_footer`, `test_t_key_full_cycle_returns_to_day`
- Given Bot-detail, when `o` is pressed, then `webbrowser.open()` is called with a bot-scoped dashboard URL and the footer echoes it -- `test_o_key_sets_footer_to_bot_dashboard_url` (footer-echo half only; `webbrowser.open()`'s actual OS-level behavior is the same disclosed manual-smoke-check gap `test_app_coin_detail.py`'s own docstring already documents for the coin `o` key)
- Given Story 4.6's read surface is unreachable for the current bot+range, when Bot-detail renders, then both new regions independently show `"history unavailable"` while the snapshot region is unaffected -- `test_build_bot_detail_body_shows_history_unavailable_before_any_fetch`, `bots_pane.trades_blotter_lines`/`pnl_sparkline_text`'s own `None`-entry unit tests

## Spec Change Log

(empty -- no bad_spec loopback occurred)

## Review Triage Log

### 2026-09-02 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 1, medium 0, low 3)
- defer: 4 (high 0, medium 2, low 2)
- reject: 7
- addressed_findings:
  - `[high]` `[patch]` Cross-bot stale-write race: `bot_history_state._handle_history_payload` wrote a late-arriving GET response keyed only by `range_name`, with no check that the in-flight request's `bot_id` still matched `_TRACKED_BOT_ID` at write time -- switching Bot-detail to a different bot while a poll was in flight could briefly show one bot's trades/PnL under another's view. Fixed by threading `bot_id` through `_poll_once`/`_handle_history_payload` and dropping the write if it no longer matches `_TRACKED_BOT_ID`; added `test_stale_payload_from_a_bot_switched_away_from_is_dropped` as a direct regression test. Found independently by both review agents.
  - `[low]` `[patch]` `_HISTORY_STALE_SECONDS = 90.0`'s comment claimed "3x the poll cadence" (15s), which is actually 6x -- the ratio is really 3x `trade_history.py`'s 30s *publish* cadence. Comment corrected; the constant itself (90.0) was already correct.
  - `[low]` `[patch]` `format_trade_line`'s blank-PnL placeholder was a hardcoded `" " * 10`, decoupled from `format_pnl`'s actual output width -- a future formatting change to `format_pnl` would silently misalign the blotter's PnL column. Now derived as `" " * len(format_pnl(0.0))`.
  - `[low]` `[patch]` `pnl_sparkline_text` mapped values to glyph indices via `int(...)` truncation, biasing every interior bucket toward the lower glyph instead of the nearest one. Changed to `round(...)`.

## Design Notes

The "unavailable vs. no-trades-yet" split is the one non-obvious judgment call: epics.md's AC4 only names "unavailable," but this codebase already has a load-bearing precedent (`bot_status.py`'s `win_rate: None` vs `0.0`) for never collapsing "unknown" into "empty" — collapsing them here would make a bot that's simply never traded look identical to a broken Redis connection. Keep them distinct.

## Verification

**Commands:**
- `docker compose --profile live-paper build live-paper` -- no compose profile covers `bot_tui` directly; if a `bot_tui` test command already exists in `Makefile`/`docker-compose.yml`, use it instead of inventing one
- `python3 -m pytest troll/bot_tui/tests -q` -- expected: all pass, zero regressions against the pre-existing baseline count
- `ruff format --check` / `ruff check` / `mypy --disallow-incomplete-defs` on every new/modified file -- expected: clean

## Auto Run Result

**Summary:** Bot-detail (Story 4.5) now renders 3 stacked bordered regions -- the existing live-snapshot header, a new trades blotter, and a new PnL sparkline -- sourced from Story 4.6's `bots:history:{bot_id}:{day,week,month,all}` Redis keys via a new GET-polling state module. `t` cycles the range preset; `o` opens a bot-scoped dashboard URL (the destination page itself doesn't exist yet -- explicitly out of scope, see Never boundary). A read-surface-unreachable state renders `"history unavailable"` independently in both new regions without affecting the snapshot header.

**Files changed:**
- `troll/bot_tui/bot_history_state.py` (new) -- GET-polls the 4 history keys for whichever single bot Bot-detail has open; tracks staleness (90s) the same way `bots_state.py` does for `bots:status`.
- `troll/bot_tui/bots_pane.py` -- adds `next_range`, `format_trade_line`, `trades_blotter_lines`, `pnl_sparkline_text`, `dashboard_bot_url`, plus `HISTORY_UNAVAILABLE_TEXT`/`NO_TRADES_YET_TEXT`.
- `troll/bot_tui/app.py` -- `_build_bot_detail_body()` renders the 2 new regions; `_handle_bot_detail_key()` gains `t`/`o`; poll task scheduled/cancelled alongside the view's open/close lifecycle.
- `troll/bot_tui/tests/test_bot_history_state.py` (new, 11 tests), `troll/bot_tui/tests/test_bots_pane.py` (+12), `troll/bot_tui/tests/test_app_bot_detail.py` (+7), `troll/bot_tui/tests/test_ad8_boundary.py` (+1 registered module), `troll/bot_tui/tests/conftest.py` (fixture updates for the new state module).

**Review findings:** 1 high-severity patch (a real cross-bot data-race in the new poll module, confirmed by reading the code directly rather than trusting either review agent's inference, then fixed with a regression test), 3 low-severity patches (a wrong ratio in a comment, a hardcoded-width coupling, a truncation-vs-rounding bias), all applied. 4 items deferred to `deferred-work.md` (an inherited `assert`-as-invariant pattern shared with the pre-existing Coin-detail deep-link, the dead `/bot/{id}` link's missing in-app signal, unverified small-terminal degradation, and a per-redraw-tick widget-rebuild cost) -- none blocking. 7 findings rejected after verifying against the actual code/wire-contract trust boundary (an unreachable `next_range` edge, `pnl: null` and malformed-trade-item paths that the sole first-party writer can't produce, a `dashboard_bot_url` empty-base-url edge matching pre-existing behavior, an assumed-by-spec 15s/30s poll-vs-publish ratio, and duplicated URL-open logic the spec explicitly required).

**Verification:** `python3 -m pytest troll/bot_tui/tests -q` -- 178 passed, 0 failed. Baseline before this story: 148. After implementation (Task/AC verification): 177. After this review pass's 1 added regression test: 178. `ruff format --check`, `ruff check`, `mypy --ignore-missing-imports` all clean on every new/modified file.

**Residual risks:** `poll_loop()`'s actual Redis GET/reconnect behavior against a live Redis instance was not smoke-tested (same disclosed-gap convention every other `*_state.py` module's listener/poll loop already carries in this codebase) -- only its pure logic was exercised. The `o` key's dashboard link 404s until a future story builds the web dashboard's bot-detail route.

Follow-up review recommended: **true** -- the high-severity fix touches new concurrency-sensitive logic (an async poll loop racing against synchronous UI-driven state resets); worth an independent second look beyond this same pass's own patch-and-verify cycle.
