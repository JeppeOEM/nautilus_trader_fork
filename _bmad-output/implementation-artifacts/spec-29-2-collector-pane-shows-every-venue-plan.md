---
title: 'Story 29.2: Collector pane shows every venue''s plan'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: 'b9f3fa8e73ee6f61064a6f808818706d6870d178'
final_revision: 'ee68298b528f9d052c922a2c3e2369149b65ede9'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** Only dYdX publishes `collector:status`, and the TUI's Collector pane is a flat list with no venue grouping. Bybit and Hyperliquid, whose plans are static, are invisible. The single `unpinned_ids` aggregate would clobber itself across venues. The pane never shows a cap, a `pending` marker or what the last apply did, and its client-side cap is 29 while dYdX's real cap is 30.

**Approach:** Every venue's collector publishes `collector:status` through the one `StatusPublisher`:
- dYdX keeps its live control plane.
- Bybit and Hyperliquid wire only the status loop over their static plan.
- The per-venue aggregate (`{"unpinned_ids": [...]}`) gains appended fields: `venue`, `cap`, `accepts_commands`, `min_liquidity_usd` and `last_apply` (capture's last `Applied`, time-stamped).
- Row bytes stay unchanged. The TUI derives each row's venue from its id through `kernel.venues.venue_of` (SIGNAL-01).

The TUI renders one section per venue. The `p`/`x`/`:start`/`:pintop` actions stay enabled only where the venue's plan accepts commands; elsewhere they are refused with the reason.

## Boundaries & Constraints

**Always:**
- Wire rule: status row messages, the `removed` tombstone and every `collector:control` payload stay byte-identical to their recordings. The aggregate only gains keys appended after `unpinned_ids`, in the order `venue, cap, accepts_commands, min_liquidity_usd, last_apply`, so the recorded aggregate minus its closing `}` is a strict prefix of the new one.
- `last_apply` is `null` before capture's first apply. Afterwards it is `{"ts": <ns>, "subscribed": [...], "unsubscribed": [...], "failed": [...]}`, with each id list sorted. It reports the most recent `CaptureService.apply`, whether that apply came from startup or a command.
- Older producer: an aggregate without `venue` is dYdX's. Without `accepts_commands`, a venue accepts commands iff it is `DYDX` (the pre-story contract). A missing `cap` means unknown: the client-side cap check is skipped, and the collector still refuses.
- A venue with rows but no aggregate yet follows the same default: dYdX actions are allowed, and other venues are refused with "waiting for <VENUE> plan on collector:status".
- Refusal reason for a venue whose plan does not accept commands: `<VENUE>: static plan: edit platform/capture/venues/<venue>/config.toml` (lowercase venue in the path). Nothing is published.
- Every section header reads `<VENUE>: N collected +P pending`, where P counts rows with `"pending": true` and N the rest. The same line carries `cap <cap>`. Venues sort alphabetically, and rows sort by id within their venue.
- A row's liquidity label shows only when its venue's `min_liquidity_usd` is non-null. A `pending` marker shows on pending rows. The id is `fit()`-truncated (TUI-02). The walker is updated in place (TUI-01).
- Staleness: a stale row keeps its `~`. A section header also gets `~` once its aggregate is older than `_STATUS_STALE_SECONDS`.

**Block If:**
- Showing the per-venue plan would require renaming or removing an existing `collector:status`/`collector:control` field, or changing the bytes of a row message.

**Never:**
- No `venue` field on row messages; the TUI derives it (SIGNAL-01).
- No control plane (`ControlService`, reload loop) for Bybit or Hyperliquid: that is Story 29.4.
- No `venue` field on `collector:control`.
- No change to `nautilus_trader/` or `crates/`. Do not write `sprint-status.yaml`.
- No new config keys (the loader's key sets are frozen). A static plan's republish period is a named constant.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| dYdX aggregate | plan cap 30, excl `[A,Z]`, threshold 20000, one apply | `{"unpinned_ids": [A, Z], "venue": "DYDX", "cap": 30, "accepts_commands": true, "min_liquidity_usd": 20000.0, "last_apply": {...}}` | — |
| Bybit static | 4 ids, applied at start | 4 rows (`liquid: false`, bytes as dYdX's), then aggregate `venue: "BYBIT", cap: 4, accepts_commands: false, min_liquidity_usd: null` | — |
| `p`/`x` on Bybit row | focused `BTCUSDT-LINEAR.BYBIT` | footer shows the static-plan reason, no confirm, nothing published | — |
| `:start` Bybit id | `:start ETHUSDC-SPOT.BYBIT` | refused with the reason, nothing published | — |
| `:start` at dYdX cap | 30 dYdX rows, cap 30 (plus Bybit rows) | refused `at 30-instrument cap`; Bybit rows are not counted | — |
| Older aggregate | `{"unpinned_ids": ["X"]}` | DYDX section's unpinned line shows X; cap `?`; actions allowed | — |
| Only aggregates | a venue with 0 rows | its section header shows (0 collected); focus lookup returns None | — |
| Malformed id row | `BTCUSDT` | grouped under an `UNKNOWN` section; actions refused (unknown venue) | — |

</intent-contract>

## Code Map

- `platform/capture/application/ports.py` -- `Applied`, `CaptureStatus`, which gains `last_applied: Applied | None = None` and `last_applied_ns: int = 0`.
- `platform/capture/application/capture_service.py:1583-1619,1714-1726` -- `apply` (records its result and `time.time_ns()`) and `capture_status` (reports them).
- `platform/collection_control/application/status.py` -- `status_messages`, `StatusPublisher` (`markets` becomes optional; new `accepts_commands` flag), and a new `STATIC_PLAN_STATUS_SECONDS = 1800.0`.
- `platform/collection_control/tests/test_status_replay.py` + `fixtures/status_payloads.json` -- the byte replay. The fixture stays unchanged.
- `platform/collection_control/tests/test_control.py` -- ControlService tests (the fake `_Channel`, around lines 491-512).
- `platform/capture/venues/dydx/__main__.py:177-199` -- control_plane wiring (it passes `accepts_commands=True`).
- `platform/capture/venues/{bybit,hyperliquid}/__main__.py` -- `build_capture_from_file` adds the status loop.
- `platform/capture/venues/dydx/tests/test_control_wiring.py` -- the wiring-test pattern that the Bybit and Hyperliquid tests copy.
- `platform/bot_tui/collector_state.py` -- `_handle_status_message`, `publish_control`, and the `_LATEST_UNPINNED_IDS` global (replaced).
- `platform/bot_tui/collector_pane.py` -- pure formatters.
- `platform/bot_tui/app.py:113-118,157-185,466-545,766-830,979-994,1070-1079` -- the cap constant, the help text, body refresh, focus lookup, confirm and publish, `:start`, and keys.
- `platform/bot_tui/tests/{test_app_collector,test_collector_pane,test_collector_status_replay}.py`, `bot_tui/tests/conftest.py` (it has no collector_state reset yet).
- Docs: `platform/docs/BOT_OPERATIONS.md:46-62`, `platform/README.md:147-166` and `:48-87`, `platform/docs/DATA_DICTIONARY.md` (the collector:status section), `platform/ARCHITECTURE.md` (:230, the channel row at :485), and the `collection_control/__init__.py` docstring.

## Tasks & Acceptance

**Execution:**
- [x] `platform/collection_control/tests/fixtures/control_payloads.json` -- Record, before any code change, the exact `collector:control` strings that the current `publish_control` sends for `start`/`unpin`/`stop` (with an id) and `pin_top_liquid`. -- This is the control replay baseline.
- [x] `platform/capture/application/ports.py`, `capture_service.py` -- Add the `CaptureStatus` fields and record the last apply. -- The fact behind `last_apply`.
- [x] `platform/capture/tests/test_apply.py` -- Add tests: `last_applied` is None and `last_applied_ns` is 0 before any apply, and both are reported after a failed and a successful apply.
- [x] `platform/collection_control/application/status.py` -- Make these changes:
  - Append the aggregate keys per Always.
  - Add `accepts_commands: bool` (keyword, required) and `markets: MarketsSource | None`. `refresh` returns when the threshold is None, and raises `RuntimeError` when a threshold is set but `markets` is None (a wiring bug, ledgered by the loop).
  - Update the module docstring.
- [x] `platform/collection_control/tests/test_status_replay.py` -- Make these changes:
  - Rows and the tombstone are byte-equal to the fixture.
  - The aggregate starts with the recorded aggregate minus `}` plus `", "`. With the five new keys popped, it equals the recorded JSON.
  - New tests cover a static-plan publisher (no markets, `accepts_commands` false, `min_liquidity_usd` null, `last_apply` null before an apply) and a `last_apply` shape after an apply.
- [x] `platform/collection_control/tests/test_control.py` -- Feed each recorded `control_payloads.json` string through `ControlService` and assert the plan command it maps to.
- [x] `platform/capture/venues/dydx/__main__.py` -- Pass `accepts_commands=True`.
- [x] `platform/capture/venues/bybit/__main__.py`, `platform/capture/venues/hyperliquid/__main__.py` -- In `build_capture_from_file`, `add_loops` a `StatusPublisher(capture, RedisStatusBus(redis_url_from_env()), None, accepts_commands=False).loop` over `lambda: plan` and `STATIC_PLAN_STATUS_SECONDS`. Update the docstrings.
- [x] `platform/capture/venues/{bybit,hyperliquid}/tests/test_status_wiring.py` -- New tests: `build_capture_from_file` wires `StatusPublisher.loop` and no `ControlService`/reload loop.
- [x] `platform/bot_tui/collector_state.py` -- Make these changes:
  - Replace `_LATEST_UNPINNED_IDS` with `_LATEST_PLANS: dict[str, dict]` and `_PLAN_RECEIVED_AT: dict[str, float]`, keyed by venue (default `DYDX`).
  - Add `venue_of_row(iid)` (through `kernel.venues.venue_of`; `UNKNOWN` on `MalformedInstrumentId`), `plan_is_stale(venue)` and `command_refusal(venue) -> str | None` per Always.
  - Leave `publish_control` byte-unchanged.
- [x] `platform/bot_tui/collector_pane.py` -- Add `venue_sections(statuses, plans)` (sorted venues, each with its rows sorted by id), `format_section_header`, `format_last_apply_line`, and `format_collector_line(row, stale, show_liquidity)` with a `pending` marker and `fit()`. `format_unpinned_line` stays per venue.
- [x] `platform/bot_tui/app.py` -- Make these changes:
  - Render the sections as non-selectable Text headers, apply and reason lines, then rows and the venue's unpinned line; the archive line stays last.
  - Show the cold open only when there are neither rows nor plans.
  - `_highlighted_collector_id` returns None when a non-row widget is focused.
  - `p`/`x`/`:start` consult `command_refusal`, and `:pintop` consults it for `DYDX`.
  - The cap check counts DYDX rows against the DYDX `cap` (skipped when unknown).
  - Delete `_MAX_COLLECTED_INSTRUMENTS`. Update the help text and docstrings.
- [x] `platform/bot_tui/tests/conftest.py` -- Add an autouse reset of every `collector_state` global.
- [x] `platform/bot_tui/tests/` -- Cover the I/O matrix:
  - Update the replay test to the new aggregate.
  - Add a `publish_control` byte test against `control_payloads.json` (patch `aioredis.Redis.from_url`).
  - Test the sections, the refusals, cap-from-wire, the older aggregate, the aggregate-only venue and the malformed id.
- [x] Docs -- Update the following (MR4):
  - `docs/BOT_OPERATIONS.md` and `README.md`'s TUI section: describe the two-pane TUI, with the Collector pane's per-venue sections and read-only Bybit/Hyperliquid.
  - `README.md` "Configure instruments": note that the static plans now publish status.
  - `DATA_DICTIONARY.md`: the aggregate's new keys.
  - `ARCHITECTURE.md` and the `collection_control/__init__.py` docstring: every venue publishes status, and only dYdX takes control.

**Acceptance Criteria:**
- Given dYdX, Bybit and Hyperliquid collectors running, when the operator opens `:data`, then three sections show each venue's collected set, pending marks, excludes, cap and last apply. dYdX's actions work, and Bybit's and Hyperliquid's are refused with the static-plan reason.
- Given the recorded `status_payloads.json` and `control_payloads.json`, when the replay tests run, then rows, tombstone and control strings are byte-identical, and the aggregate differs only by the appended keys.
- Given `docs/BOT_OPERATIONS.md` and `platform/README.md`, when read, then they describe the two-pane TUI with a per-venue Collector pane.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 2, low 6)
- defer: 0
- reject: 6: (high 0, medium 0, low 6)
- addressed_findings:
  - `[medium]` `[patch]` The refresh kept focus by walker position, so a section line appearing or vanishing above the focused row (a venue's first aggregate replacing its "waiting" line) moved `p`/`x` onto a different instrument. Focus now follows the focused row's id (`_focused_collector_id`, `_keep_collector_focus_on_a_row(focused_id)`); tested.
  - `[medium]` `[patch]` A static plan edited and its collector restarted (the path the refusal tells the operator to take) left the removed id's row forever, since there is no tombstone. It also inflated the header and the cap count. A venue's aggregate now drops that venue's rows that were not republished since its previous aggregate. The mid-publish-reconnect ceiling is a `Known limit:`; tested.
  - `[low]` `[patch]` `_submit_collector_confirm` re-checks `command_refusal`, since a plan may withdraw `accepts_commands` while the operator types; tested.
  - `[low]` `[patch]` `_utc_ns_text` no longer raises on an out-of-range `ts` (it would have broken the pane on every redraw). A non-dict, non-null `last_apply` reads `? (malformed)` instead of "none yet"; tested.
  - `[low]` `[patch]` The `_shown_state` docstring claimed the startup apply lands after the first publish; `run()` awaits it before the loops start. It now states the real case: an apply that moves no pending mark.
  - `[low]` `[patch]` The new control-replay test waited a fixed 50 ms on `control_loop`. It now awaits `_handle_message` directly, with no wall-clock race.
  - `[low]` `[patch]` Added a `Known limit:` on `command_refusal`: `collector:control` has no venue, so this is safe only while dYdX alone accepts commands. The upgrade path is 29.4's venue field.
  - `[low]` `[patch]` Added a `Known limit:` on `STATIC_PLAN_STATUS_SECONDS`: pub/sub has no history, so a TUI started between publishes waits for the next one.
- rejected: `last_apply` keeps a since-recovered failure (spec Design Notes: history, time-labelled; `pending` is the live truth); the refusal path for a dYdX plan that says `accepts_commands: false` (no producer sends that); `liquid: false` on static rows (the row bytes are the wire contract; the TUI hides the label by `min_liquidity_usd`); a static plan's cap equal to its size (the honest plan fact, `cap=len(ids)`); venue-token normalisation of the aggregate (producers send `CollectionPlan.venue`); a lowercase `:start` id (venue suffixes are case-sensitive, and the collector would refuse it too).

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 1, low 4)
- defer: 0
- reject: 10: (high 0, medium 0, low 10)
- addressed_findings:
  - `[medium]` `[patch]` dYdX's status loop and its `ControlService` called `StatusPublisher.publish` concurrently, so their row+aggregate bursts could interleave on the bus. The TUI's sweep would then drop live dYdX rows until the next full publish (up to 30 minutes). `publish` and `publish_removed` now hold one `asyncio.Lock`, and the loop re-reads the plan after its indexer fetch so it cannot republish a plan a command has since replaced. A test proves that two concurrent publishes reach the bus whole; it fails without the lock.
  - `[low]` `[patch]` The sweep compared row and aggregate receipt times from `time.time()`, so a backwards clock step could drop just-republished rows. It now tracks, per venue, which ids arrived since that venue's last aggregate (`_REPUBLISHED_SINCE_PLAN`), with no clock involved. Tested with a clock stepped back.
  - `[low]` `[patch]` The `_keep_collector_focus_on_a_row` docstring promised `p`/`x` "never act on an instrument the operator did not pick". Once the focused row is gone, focus falls to the row at its position. The docstring now says so and notes that the confirm prompt names the id.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST step did not say that `make redeploy-all` also restarts `live-paper`. It now says so and gives the narrower collector + `bot_tui` commands.
  - `[low]` `[patch]` The DEPLOY_CHECKLIST's `:data` check did not say that a section appears only at its collector's next publish (pub/sub keeps no history). It now says to open the TUI within a minute of the restart or wait up to 30 minutes.
- rejected:
  - The refusal text for a dYdX plan with `accepts_commands: false`: no producer sends that, and the spec fixes the text.
  - `collector_pane` importing `collector_state`: an import only, no I/O.
  - A misleading "waiting for <X> plan" reason on a typo'd venue suffix, and the lowercase-id case already rejected.
  - A `:start` of an already-collected id refused at the cap: this check existed before the story.
  - No client-side cap without a published `cap`: the spec's Always.
  - `last_apply` overwritten by an empty apply: the spec says the most recent apply.
  - The Redis URL resolver differing between dYdX and the other venues: dYdX's is pre-existing.
  - A ledger entry every 30 s per venue during a Redis outage: the same as dYdX's existing loop, and a true error.
  - Wiring tests reading private attributes.
  - UNKNOWN-venue rows not swept: they age out through `~` staleness, and the loader never plans such an id.

## Design Notes

- **No venue on rows:** a row's venue is its id suffix, so a wire field would store it twice. Only the aggregate, which has no id, carries `venue`.
- **Pins:** every collected entry is pinned (Story 6.1, `CollectionPlan.pins == collected`), so the section's collected set is its pin set. There is no separate pins list.
- **Reason path:** the AC's `<venue>_collector/config.toml` names the pre-26.2 package directories. The live files are `platform/capture/venues/<venue>/config.toml`, so the reason names those.
- **`last_apply` vs pending:** `last_apply` is history (what the most recent apply did). A failed id later subscribed by capture's retry loop drops its `pending` mark while `last_apply` still lists it as failed. The `pending` mark is the live truth, and the apply line is labelled with its time.
- **Mixed versions:** an older TUI reading the new Bybit aggregate would overwrite dYdX's unpinned list with `[]`. Collectors and `bot_tui` ship from one image, so this lasts only during a rollout.

## Verification

**Commands:**
- `cd platform && python3 -m pytest collection_control/tests capture/tests/test_apply.py capture/venues bot_tui/tests tests/test_boundaries.py -q` -- expected: all pass, with no new warnings (known Redis-dependent failures excepted).
- `cd platform && ruff check <changed> && ruff format --check <changed> && mypy <changed .py>` -- expected: clean.


## Auto Run Result

Status: done

**Summary:** Story 29.2 (implemented in d4c56bc8e1) had a follow-up review pass. Every venue's collector publishes `collector:status`. The per-venue aggregate gains appended keys: `venue`, `cap`, `accepts_commands`, `min_liquidity_usd` and `last_apply`. The TUI's Collector pane shows one section per venue, and only dYdX's plan accepts commands. This pass fixed one medium concurrency bug and four low findings.

**Files changed in this pass** (under `platform/`):
- `collection_control/application/status.py`: `StatusPublisher.publish`/`publish_removed` are serialized by an `asyncio.Lock`, and the loop re-reads the plan after its liquidity fetch.
- `bot_tui/collector_state.py`: the row sweep uses per-venue arrival sets (`_REPUBLISHED_SINCE_PLAN`) instead of wall-clock receipt times.
- `bot_tui/app.py`: the `_keep_collector_focus_on_a_row` docstring now matches its behaviour.
- `docs/DEPLOY_CHECKLIST.md`: 29-2 now covers the `live-paper` restart, the narrower commands and the wait for a section.
- Tests:
  - `collection_control/tests/test_status_replay.py`: the concurrent-publish test.
  - `bot_tui/tests/test_app_collector.py`: the clock-step test, and the sweep test no longer depends on timestamps.
  - `bot_tui/tests/conftest.py`: resets the new global.

**Review:** 5 patches (1 medium, 4 low), 0 deferred, 10 rejected. See the Review Triage Log.

**Follow-up review recommended:** false. The one medium fix is a local lock in the publisher, covered by a test that fails without it. The rest are docstring, doc and small state-tracking changes.

**Verification:**
- `python3 -m pytest collection_control/tests capture/tests capture/venues bot_tui/tests tests/test_boundaries.py -q`: 780 passed.
- `ruff check` and `ruff format --check` on the 6 changed Python files: clean.
- `mypy` on `status.py` and `collector_state.py`: the only error is pre-existing, in the untouched `kernel/second_snapshot.py:152`.
- Not verified against live collectors or Redis. That check is the deferred operator step in `docs/DEPLOY_CHECKLIST.md`.

**Residual risks:**
- `collector:control` has no venue. Only dYdX may publish `accepts_commands: true` until Story 29.4 (`Known limit:` on `command_refusal`).
- A TUI that reconnects in the middle of a publish drops the rows it missed until the next publish (`Known limit:` on the sweep).
- Redis pub/sub keeps no history, so a freshly started TUI waits up to 30 minutes for a quiet venue's section (`Known limit:` on `STATIC_PLAN_STATUS_SECONDS`).
