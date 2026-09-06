---
baseline_commit: 944891bbbafa9a2869d7b988910b478dcf647576
---

# Story 6.1: TOML-driven instrument control (pin/unpin/start/stop/refresh) + bot_tui overview page

Status: review

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->
<!-- No epics.md entry exists for this story -- standalone feature story, created by explicit
     user request (bypassing epic/PRD ceremony), following the same precedent as Story 5.1.
     Filed as epic-6 since it's unrelated to epic-5's crossed-book investigation. Design was
     worked out interactively with the user (two rounds of plan-mode revision) before this
     story was created -- see this file's Dev Notes for the resulting decisions. -->

## Story

As the operator of the dYdX collector,
I want `config.toml`'s `instruments` list to be the single, always-authoritative source of what gets collected — with pin/unpin, start/stop, and "refresh to current top-by-volume coins" all invokable live from a new bot_tui page — instead of a hidden, timer-driven liquidity reclassification silently changing subscriptions behind my back,
so that I always know exactly what's being collected and why, pinned coins are never dropped even if their volume falls off, and I can manage the collected set (up to a 29-coin cap) without hand-editing a file on a remote host and waiting for a reload timer.

## Acceptance Criteria

1. **TOML is the sole subscribe source at startup.** Given `collector.py`'s `run()` starts, when it builds its initial subscribe set, then it subscribes to exactly `{e.id for e in config.instruments}` (intersected with markets actually known to `fetch_instruments()`, with a warning logged for any configured id not found) — no `classify_liquidity` call and no separate liquid/illiquid computation drives the initial subscribe set.
2. **No automatic subscription changes.** Given the collector is running, when 30 minutes (or any elapsed time) passes with no explicit control action, then the set of subscribed instruments does not change on its own — `_liquidity_check_loop`'s current auto-subscribe/auto-unsubscribe behavior (collector.py:541-549) is removed.
3. **Pin / unpin.** Given a `collector:control` message `{"action": "pin", "id": "<ID>"}` for an id already present in `config.instruments`, when the collector processes it, then that entry's `pinned` field is set `true`, `config.toml` is rewritten to persist it, and no subscription change occurs (pin only changes protection, not membership). `unpin` is the mirror (sets `pinned` false). Both no-op with a logged warning if `id` isn't currently in `instruments`.
4. **Start / stop.** Given a `collector:control` message `{"action": "start", "id": "<ID>"}` for an id not currently in `instruments`, when `len(instruments) < 29`, then a new entry `{id, pinned: false}` is appended, `config.toml` is persisted, and the collector subscribes to it immediately (no reload-timer wait). Given `len(instruments) == 29`, `start` is rejected: no mutation, and a rejection is published on `collector:status` (or logged + surfaced via the next status snapshot) so the TUI can show it. `{"action": "stop", "id": "<ID>"}` removes the matching entry from `instruments` regardless of its `pinned` value, persists, and unsubscribes immediately.
5. **Refresh top coins.** Given `{"action": "refresh_top_coins"}`, when processed, then the collector fetches current markets (`_fetch_markets_json`), computes the top-by-volume set sized to `29 - count(pinned)` via the existing `classify_liquidity` (open_interest.py:109), and replaces every `pinned=false` entry in `instruments` with that new set — every `pinned=true` entry is left byte-for-byte untouched (never removed, never re-ordered relative to itself). Persists to `config.toml` and reconciles subscriptions (subscribe newly-added, unsubscribe dropped-non-pinned).
6. **29-coin cap enforced everywhere.** `pin`/`unpin` never change `len(instruments)` so they're never capped. `start` and `refresh_top_coins` never produce `len(instruments) > 29` — verified by a unit test that asserts this invariant after each action, including from an already-at-cap starting state.
7. **`collector:status` reflects reality.** Given the collector is running, when its status loop ticks, then it publishes, for every id currently in `instruments`, `{id, pinned, liquid: bool, last_trade_ts}` where `liquid` is a live, informational-only recomputation against current volume (via `classify_liquidity`, no `max_liquid` cap applied since this is display, not a selection) — changing `liquid` never itself changes subscriptions (re-verifies AC2).
8. **Retention/pruning behavior for abandoned instruments is preserved.** Given an instrument is removed from `instruments` (via `stop` or `refresh_top_coins` dropping a non-pinned entry) and it still has catalog data on disk, when `_prune_loop` next runs, then that instrument's data is still subject to `non_config_retain_hours` pruning exactly as it is today — this must keep working even though `self._liquid`/`self._illiquid` (the fields it currently reads, collector.py:730) are being removed. See Dev Notes' "What must NOT regress" — this is the easiest thing to silently break in this story.
9. **bot_tui collection-overview page.** A new page lists every id in `collector:status`'s snapshot with columns id/pinned/liquid/last-trade-age; `p` toggles pin/unpin on the selected row; `x` stops it via the same type-to-confirm guard pattern as `bots_pane`'s `s` (app.py:990-1033); `:start <ID>` and `:refresh` command-bar entries add a coin / trigger `refresh_top_coins`. Pin/start are locally disabled at 29 for instant feedback (collector re-validates regardless).
10. **No regressions.** `python3 -m pytest troll/dydx_collector/tests troll/bot_tui/tests -q` passes with zero regressions against the pre-story baseline.

## Tasks / Subtasks

- [x] Task 1 — TOML write support + data model (AC: #3, #4, #5)
  - [x] Add `tomli_w` to `troll/troll-requirements.txt` (stdlib `tomllib` is read-only; this is its natural write counterpart, avoids hand-rolling TOML array-of-tables/string escaping)
  - [x] Add `pinned: bool = False` to `InstrumentEntry` (config.py:24-31); update `load_config`'s comprehension (config.py:54-61) to read it (`entry.get("pinned", False)`)
  - [x] Add `save_config(config: CollectorConfig, path: Path) -> None` in config.py: build the raw dict mirroring `load_config`'s shape (`str(config.network)` round-trips cleanly — confirmed empirically, `DydxNetwork.from_str("mainnet")` stringifies back to `"mainnet"`) and `tomli_w.dump(raw, f)` opened `"wb"`. Full rewrite, not a patch — hand-added comments in `config.toml` are lost on any TUI-triggered write (documented in the function's docstring; acceptable per user, revisit only if it becomes a real complaint)
  - [x] Test: round-trip `save_config` → `load_config`, assert `pinned` survives and all other fields are unchanged

- [x] Task 2 — Collector: startup simplification + delete auto-resubscribe (AC: #1, #2)
  - [x] `run()`: replaced the `classify_liquidity` startup call + `self._liquid`/`self._illiquid` assignment with `to_subscribe = configured & known` + a warning for any configured-but-unknown id
  - [x] Deleted `self._pinned`, `self._liquid`, `self._illiquid` as persistent instance fields — replaced with `self._known_markets: set[str]` (see Dev Notes "State model after this story")
  - [x] `_liquidity_check_loop` renamed to `_status_loop`, subscribe/unsubscribe blocks removed entirely — see Task 4
  - [x] **Beyond-plan discovery, fixed in scope:** found a pre-existing, undocumented standalone script `dydx_collector/update_pinned.py` (`python3 -m dydx_collector.update_pinned`) that already did an ad-hoc "top-by-volume merge into instruments" via its own regex-based TOML rewrite and its own separate `top_by_volume()` implementation — a second, divergent implementation of exactly what `refresh_top_coins` now does correctly (pinned-aware, single `classify_liquidity` source of truth, live-applied). It predated the `pinned` field entirely and would have silently stripped `pinned=true` off every instrument on next use (its regex rewrite doesn't know about that field). Not referenced anywhere else (Makefile/docs/tests) — deleted rather than fixed, per DESIGN-03/SSOT (no reason to keep two ways to do the same thing)

- [x] Task 3 — Control loop: pin/unpin/start/stop/refresh_top_coins (AC: #3, #4, #5, #6)
  - [x] New `_apply_config`/`_apply_and_persist` helpers: diff old vs. new `instruments` ids, subscribe additions, unsubscribe removals, update `self._config`/`self._delta_store`/`self._delta_retain_hours`, then persist — shared by `_reload_config_loop` and every control-action branch
  - [x] New `_control_loop`/`_handle_control_message`/`_refresh_top_coins`: pin/unpin/start/stop/refresh_top_coins implemented exactly as specced, including the 29-cap check on `start` and pinned-preservation on `refresh_top_coins`
  - [x] Malformed/unknown-action messages logged and ignored, never crash the loop (try/except around each message in `_control_loop`, `else` branch in `_handle_control_message` for unrecognized actions)
  - [x] `_control_loop` added to `run()`'s task list
  - [x] Tests added in new `dydx_collector/tests/test_collector_control.py`: cap rejection at exactly 29 and success at 28→29; `refresh_top_coins` pinned-preservation (byte-identical `is` check) and never-exceeds-cap; `stop` removes regardless of `pinned`; unknown action / unknown id is a no-op that persists nothing

- [x] Task 4 — Read-only status loop + `collector:status` publish (AC: #7, #8)
  - [x] `_liquidity_check_loop`'s body replaced by `_status_loop`: fetches markets, computes `liquid_by_volume` via `classify_liquidity(..., max_liquid=None)` (no cap — display, not selection), recomputes `self._known_markets`, publishes `collector:status` per currently-collected instrument
  - [x] **Pruning behavior preserved (AC #8):** extracted `_prune_candidates(instruments, known_markets)` as a standalone, unit-tested pure function (mirrors `_prune_interval_seconds`'s pattern) implementing exactly the union described in the plan; `_prune_loop` now calls it instead of inlining the old `(self._liquid | self._illiquid) - self._pinned` computation. `self._known_markets` starts as an empty set (safe — `_prune_candidates` degrades to just the non-pinned-collected half until the first `_status_loop` tick, never crashes)
  - [x] `_watchdog_loop`'s `live` and `_second_loop`'s iteration set both switched to `{e.id for e in self._config.instruments}`
  - [x] Regression tests added: `test_prune_candidates_includes_dropped_instrument`, `test_prune_candidates_includes_non_pinned_collected` in `test_collector_control.py`

- [x] Task 5 — docker-compose (AC: #3, #4)
  - [x] `troll/docker-compose.yml`: changed the `config.toml` bind mount from `:ro` to `:rw` on the collector service

- [x] Task 6 — bot_tui: Redis state + new page (AC: #9)
  - [x] `troll/bot_tui/collector_state.py` (new, mirrors `bots_state.py`): `_LATEST_COLLECTOR_STATUS`, `_redis_listener`, `publish_control(redis_url, action, instrument_id=None)`
  - [x] `troll/bot_tui/collector_pane.py` (new, pure formatting, no I/O): `collector_rows`, `format_collector_line`, `format_last_trade_age`
  - [x] `app.py`: `"collector"` added to `_BREADCRUMB_LABELS` (and thus `_RECOGNIZED_COMMANDS`/`:collector` navigation for free); `_build_collector_body`/`_build_collector_row_widget`/`_highlighted_collector_id`; `_COLLECTOR_FOOTER_HINT_TEXT`; `_handle_collector_pane_key` (`p` → `_toggle_pin`, no confirm needed since pin/unpin never changes `len(instruments)`; `x` → a dedicated `_collector_stop_confirm_active` guard, kept separate from the bot one rather than generalizing two call sites into an abstraction); wired into `_build_body`, `_handle_global_key`, `_refresh_footer_hint`, `_redraw_loop`, and `run()`'s listener-task set
  - [x] Command-bar actions kept as a small separate pure parser `_parse_collector_command` (not folded into `_dispatch_command`, which is navigation-only-no-argument by contract) — `:start <ID>` / `:refresh`, wired into `_submit_command` ahead of `_dispatch_command`
  - [x] Client-side cap check on `:start` only (`len(_LATEST_COLLECTOR_STATUS) >= 29`) — corrected from this task's original wording: pin/unpin never changes `len(instruments)` (AC #6), so there is nothing to cap-check there; only `start` can push the count up
  - [x] Tests: `test_collector_pane.py` (8 tests covering sort order, cold-open, last-trade-age formatting, stale marker) — real branching logic present, so not skipped per TEST-02

## Dev Notes (implementation addenda)

- **`troll/config.toml` migration (real production config, not a test fixture).** This
  is the file actually bind-mounted into the running `dydx-collector` container
  (`docker-compose.yml`'s `./config.toml` — distinct from the empty, untracked-in-
  practice `dydx_collector/config.toml` used only for local non-Docker runs). Before
  this story its four `[[instruments]]` entries (BTC/ETH/SOL/DYDX) had no `pinned`
  field and relied on the old semantics where *any* `instruments` membership meant
  "locked in forever" — the file's own comment said exactly that. Under the new model,
  `instruments` membership alone no longer implies permanent protection (a
  `refresh_top_coins` action could have silently demoted all four on first use). Added
  `pinned = true` to all four to preserve the operator's original, explicitly-stated
  intent, and rewrote the stale comment referencing the now-deleted
  `update_pinned.py` to point at the new `refresh_top_coins` control action instead.
- **Cognitive-complexity fix in `app.py`:** adding the third modal-guard branch
  (`_collector_stop_confirm_active`) pushed `_unhandled_input` to 11 (over this
  codebase's threshold of 10). Extracted the three mutually-exclusive modal checks
  (command bar / bot stop-confirm / collector stop-confirm) into
  `_handle_modal_guard_key(key) -> bool`, same extraction pattern this file already
  uses for `_handle_bots_pane_key`/`_handle_coin_detail_key`/etc.

## Dev Notes

- **State model after this story.** `Collector` currently tracks three overlapping sets: `_pinned`, `_liquid`, `_illiquid` (collector.py:320-322), each mutated from multiple places (`run()`, `_liquidity_check_loop`, `_reload_config_loop`). After this story there is exactly one authoritative collection: `self._config.instruments` (each entry carrying its own `pinned` bool) — `_pinned`/`_liquid` are deleted outright. The only new persistent field is `self._known_markets: set[str]`, refreshed by the status loop, whose sole purpose is feeding `_prune_loop`'s cleanup of abandoned (stopped/demoted) instruments' catalog data (see Task 4 — this is the one piece of old behavior that has nothing to do with subscriptions and must survive the refactor anyway).

- **Why the auto-resubscribe removal is intentional, not a regression.** The user explicitly rejected the initial plan draft (which kept periodic auto-reclassification) in favor of this fully-manual model: "invoke a write to the toml with top coins from the tui and only then it gets populated... When app start it just sees whats in TOML and starts collecting that." Do not re-introduce any timer-driven subscribe/unsubscribe — `classify_liquidity` is now called only from the `refresh_top_coins` control action and the read-only status loop (which never mutates `instruments`).

- **29 vs. 32.** `_MAX_WS_SUBSCRIPTIONS = 32` (collector.py:153) is dYdX's real per-connection WS hard limit (see its own docstring/comment there) — leave that constant untouched, it's still true and still worth keeping as documented context. The new cap this story enforces is a separate, smaller number confirmed with the user as a deliberate 3-slot safety margin: use a new named constant (e.g. `_MAX_COLLECTED_INSTRUMENTS = 29`) rather than reusing/renaming `_MAX_WS_SUBSCRIPTIONS`, since they now mean genuinely different things (venue ceiling vs. our own operating cap).

- **`InstrumentEntry` is frozen.** Toggling `pinned` means constructing a replacement (`dataclasses.replace(entry, pinned=new_value)`) and rebuilding the `instruments` tuple — don't make the dataclass mutable just for this, `dataclasses.replace` is the one-line idiomatic answer here (ladder rung 5).

- **`DydxNetwork` round-trip.** `load_config` reads `network` via `DydxNetwork.from_str(raw.get(...).lower())` (config.py:71-73). `save_config` needs the reverse — check `DydxNetwork`'s actual string representation (likely `.name.lower()` or a dedicated `.value`; verify against the PyO3 binding rather than assuming) before serializing it back into the raw dict.

- **Redis pattern being mirrored exactly.** This story's `collector:control`/`collector:status` channels, `_control_loop`, and `publish_control` are a direct structural copy of the already-proven `bots:control`/`bots:status` pattern: `live_paper/bot_status.py:196-274` (`_parse_control_message`, `_heartbeat_loop`, `_control_loop`) on the publisher/actor side, `bot_tui/bots_state.py:51-113` (`publish_control`, `_redis_listener`) on the TUI side, `bot_tui/app.py:979-1033` (`_publish_bot_action`, `_open_stop_confirm`/`_submit_stop_confirm`) on the UI-action side. Reuse these shapes; don't invent new ones.

- **Docker/network context.** All `troll/` services run `network_mode: host` and share one `redis` container (`docker-compose.yml`) — `collector:control`/`collector:status` need no new infrastructure, just new channel names on the connection every service already has. `bot_tui`'s container does not and will not mount `dydx_collector/`'s files (confirmed in prior research this story is based on) — all coordination is Redis-only, collector remains sole file writer.

- **Nothing here touches `troll/live_paper/`, `ranking_engine/`, or `ml_signals/`** — this story is scoped to `dydx_collector/` + `bot_tui/`. `coins_pane.py`/`ranking_engine` are a separate, unrelated concern (strategy-signal ranking, not collection-liquidity classification) confirmed during design research — do not conflate the two or try to reuse `coins_pane.py`'s code for the new page beyond its general urwid-list-rendering style.

### Project Structure Notes

- New files: `troll/bot_tui/collector_state.py`, `troll/bot_tui/collector_pane.py`. Modified: `troll/dydx_collector/config.py`, `troll/dydx_collector/collector.py`, `troll/docker-compose.yml`, `troll/troll-requirements.txt`, `troll/bot_tui/app.py`.
- No changes to `troll/dydx_collector/open_interest.py` — `classify_liquidity` is reused as-is, just called from new call sites with different `max_liquid` values (a hard cap for `refresh_top_coins`, `None` for the informational status loop).

### References

- [Source: troll/dydx_collector/collector.py:145-153,308-334,466-576,672-805] — current pinned/liquid/illiquid state, `_liquidity_check_loop`, `_reload_config_loop`, `_prune_loop`, `_watchdog_loop`, `_second_loop`, `run()` — all read in full for this story
- [Source: troll/dydx_collector/config.py] — full file read; `CollectorConfig`/`InstrumentEntry`/`load_config`
- [Source: troll/dydx_collector/open_interest.py:109-160] — `classify_liquidity`, reused unmodified
- [Source: troll/live_paper/bot_status.py:196-289] — control-loop/heartbeat pattern being mirrored
- [Source: troll/bot_tui/bots_state.py:51-113] — TUI-side Redis state pattern being mirrored
- [Source: troll/bot_tui/app.py:486-502,772-805,965-1033,1149-1210] — view-wiring, stop-confirm-guard, command-bar dispatch patterns being mirrored
- [Source: troll/docker-compose.yml:20-45,108-152] — collector/bot_tui service definitions, volume mounts, shared `redis` + `network_mode: host`
- [Source: troll/CLAUDE.md#Memory Discipline, #Design Principles] — MEM-02 (non-configured/abandoned instruments must not accumulate unbounded catalog data — motivates Task 4's pruning-preservation requirement), DESIGN-01/03 (YAGNI, prefer deletion)

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5

### Debug Log References

- `docker compose build collector` + `docker compose run --rm --no-deps -e HOME=/tmp -e USER=collector collector python3 -m pytest dydx_collector/tests bot_tui/tests -q` run after every task and again after each post-review fix — 329 passed, 0 failed on the final run (319 at initial review handoff, +10 from the two post-review fixes' regression tests).
- One unrelated pre-existing failure observed in the wider `make test` scope: `ml_signals/tests/test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`. Confirmed via `git status` that `troll/ml_signals/`/`troll/ranking_engine/` carried pre-existing uncommitted modifications from before this story started (visible in this session's initial git-status snapshot) — this story touches neither directory, so the failure is out of scope per this story's own AC #10/Verification wording (`dydx_collector/tests bot_tui/tests` only), not a regression introduced here.
- `.venv/bin/python3 -m ruff check` run against every file this story touched; fixed the 3 real findings attributable to this story's edits (an unused test variable, a docstring capitalization, and a cognitive-complexity-11 in `app.py`'s `_unhandled_input` after adding the third modal guard) — all pre-existing findings in files this story didn't touch were left alone. Also deleted a genuine pre-existing bug caught by the same lint pass: `CollectorConfig.snapshot_interval_seconds` was declared twice in the dataclass (harmless in Python — last wins — but real dead code), removed the duplicate while already editing that exact class.

### Completion Notes List

- **Post-review production bugs found and fixed** (user tested the deployed build before formal code-review):
  1. **"waiting for collector:status" never clearing.** `_status_loop` slept for the full `liquidity_check_seconds` (1800s default) *before* its first publish — after any collector restart, `collector:status` stayed silent for up to 30 minutes. Fixed by flipping the loop to run-then-sleep (publish immediately on the first iteration, then repeat on the same interval). Regression test: `test_status_loop_publishes_immediately_without_waiting_for_first_sleep`.
  2. **Collector pane unscrollable ("it jumps").** `_build_collector_body` was rebuilt as a brand-new `ListBox`/`SimpleListWalker` on every redraw tick (`_REDRAW_POLL_SECONDS` = 0.5s), which resets urwid focus/scroll position every time — any up/down keypress got silently wiped within half a second. This mirrored `_build_bots_body`'s own documented, accepted YAGNI gap, but at 0.5s instead of bots' ~5s it was actually unusable. Fixed with the same persistent-body/in-place-mutation pattern `_refresh_coins_body` already established for the Coins pane (Story 4.2): `_refresh_collector_body()` + `self._collector_body`, only reconstructing on a real cold-open↔populated shape change, otherwise slice-assigning the existing `SimpleListWalker`. New file `test_app_collector.py` (8 tests), including a direct regression test simulating repeated redraw ticks and asserting scroll position survives.
  - Both were real gaps in the original implementation, not scope creep — fixed in place rather than filed as follow-ups, since they made the shipped feature not work at all for its core purpose.
- **`:help` text clarified** (user-requested): the COLLECTOR PANE section now explicitly states every action persists to `config.toml`, spells out the two-step "`:start <ID>` then `p`" sequence to permanently pin a brand-new coin, and explains why `:refresh` can later replace a not-pinned coin (by design — "current top coins" is re-evaluated each time, not remembered) so a user isn't surprised when an unpinned `:refresh`-added coin later disappears.
- All 10 acceptance criteria implemented and covered by tests (`dydx_collector/tests/test_collector_control.py`, `test_config.py`, `bot_tui/tests/test_collector_pane.py`, plus existing suites re-verified for zero regressions).
- Two beyond-task discoveries, both fixed in scope and documented above/in Task 2's checklist: (1) a pre-existing, undocumented `dydx_collector/update_pinned.py` script that duplicated `refresh_top_coins`'s job via its own divergent volume-ranking implementation and would have silently stripped `pinned=true` off every instrument on next use — deleted, nothing else referenced it; (2) the live production `troll/config.toml` needed `pinned = true` added to its 4 existing entries to preserve the operator's already-stated "locked in forever" intent under the new semantics — without this, those 4 coins would have silently lost permanent protection on first `refresh_top_coins` use.
- Collector-side state model simplified, not just extended: `self._pinned`/`self._liquid`/`self._illiquid` (3 persistent sets, mutated from 3 different places) were deleted entirely in favor of `self._config.instruments` (with a per-entry `pinned` flag) as the sole subscribe source, plus one new `self._known_markets` field used only by `_prune_loop`'s cleanup. Net effect: less collector state than before this story, despite adding 5 new control actions.
- Not yet deployed to the live `dydx-collector` container (only built/tested via ephemeral `docker compose run` containers, which don't touch the running one) — deploying requires `docker compose up -d --build collector` (a live-container restart) and was left for the user to trigger explicitly rather than done unilaterally, per this session's "confirm before restarting a running data-collection container" judgment call.

### File List

**New:**
- `troll/dydx_collector/tests/test_collector_control.py`
- `troll/bot_tui/collector_state.py`
- `troll/bot_tui/collector_pane.py`
- `troll/bot_tui/tests/test_collector_pane.py`
- `troll/bot_tui/tests/test_app_collector.py`

**Modified:**
- `troll/dydx_collector/config.py` — `pinned` field, `save_config()`, removed a pre-existing duplicate field declaration
- `troll/dydx_collector/collector.py` — module docstring, new constants (`_MAX_COLLECTED_INSTRUMENTS`, `_CONTROL_CHANNEL`, `_STATUS_CHANNEL`), deleted `_pinned`/`_liquid`/`_illiquid` fields, added `_known_markets`/`_last_liquid_by_volume`/`_redis: | None`, `run()` startup simplified, `_liquidity_check_loop` replaced by `_status_loop`/`_publish_status` (run-then-sleep, publishes immediately on startup — post-review fix), `_reload_config_loop` simplified via new `_apply_config`/`_apply_and_persist`, new `_control_loop`/`_handle_control_message`/`_refresh_top_coins`, `_prune_loop`/`_watchdog_loop`/`_second_loop` updated to the new state model, new module-level `_prune_candidates()`
- `troll/dydx_collector/tests/test_config.py` — pinned-field + round-trip tests
- `troll/dydx_collector/tests/test_collector_resilience.py` — 3 call sites updated from `collector._pinned = {...}` to `collector._config = dataclasses.replace(...)`
- `troll/troll-requirements.txt` — added `tomli_w`
- `troll/config.toml` — added `pinned = true` to the 4 existing instruments, updated stale comment
- `troll/docker-compose.yml` — collector's `config.toml` mount flipped `:ro` → `:rw`
- `troll/bot_tui/app.py` — imports, `_BREADCRUMB_LABELS`/`_COLLECTOR_FOOTER_HINT_TEXT`/`_MAX_COLLECTED_INSTRUMENTS`/help text (expanded post-review per user request — see Completion Notes), `_SelectableCollectorRow`, collector-stop-confirm state + handlers, `_build_collector_row_widget`/`_highlighted_collector_id`, `_toggle_pin`/`_publish_collector_action`, `_parse_collector_command`, `_handle_modal_guard_key` (complexity extraction), wiring into `_build_body`/`_handle_global_key`/`_refresh_footer_hint`/`_redraw_loop`/`run()`; `_build_collector_body` replaced post-review by persistent-body `_refresh_collector_body`/`_set_collector_filler` + `self._collector_body`/`self._collector_shape` fields (scroll-preservation fix — see Completion Notes)

**Deleted:**
- `troll/dydx_collector/update_pinned.py` — superseded by `refresh_top_coins` (see Task 2 and Completion Notes)
