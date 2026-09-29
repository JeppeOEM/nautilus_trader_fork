---
title: 'Story 29.5: Market browser in the Collector pane: search a venue''s coins by name and add them'
type: 'feature'
created: '2026-09-29'
status: 'done'
final_revision: '2bc7ff148b1f7f2f94c89597c480e24d4f1cb8f8'
baseline_revision: '1a2d283871258523708914768e6ecc0e1eaefdb8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** To add a coin, the operator types its exact instrument id into `:start <ID>`, so they have to look the id up somewhere else first. The TUI has no list of what each venue offers. A command the collector refuses is only logged, so the TUI can never show why an add did not happen.

**Approach:**
- `ranking` already polls every venue's full market list each volume cycle. It will publish those names on a new channel, `markets:live`.
- The Collector pane gets a `/` market browser. It filters that list by symbol or id across venues and marks each row's state (`collected`, `pending`, `excluded`, `failed`).
- `a` adds the focused row through Story 29.4's `collector:control` (`start`, venue-addressed), behind the type-to-confirm guard.
- The collector's aggregate gains `last_refusal`, so a refused add shows its reason.

## Boundaries & Constraints

**Always:**
- **`markets:live` payload:** one message per venue per volume cycle, `{"venue", "ts", "markets": [{"instrument_id", "symbol"}]}`, in that key order.
  - `ts` is the cycle's `now_ns`, and `markets` is sorted by id.
  - `symbol` comes from `kernel.venues.base_symbol`, derived on publish and never stored (SIGNAL-01).
  - Names only: no volume, price or metric (decision 2026-09-26).
  - A venue's list is the union of its sources that are still fresh by the same `volume_max_age_ns` rule as `refresh_volumes`, so Bybit linear and spot go in one BYBIT message.
  - A venue with no fresh source publishes nothing (DATA-01).
  - A publish failure is ledgered at the new site `ranking_engine.markets`, one venue at a time. It never stops the volume cycle or the other venues' messages.
- **Wire rule:**
  - `collector:status`'s aggregate only appends `last_refusal` after `last_apply`: `{ts, action, id, reason}`, or `null` before any refusal since the collector started. Every existing key and byte stays as it is.
  - `collector:control` is unchanged. `a` sends `{"action":"start","id":…,"venue":…}`, the existing verb for `CollectionPlan.add`.
- **Refused command:** a `PlanRejected` command is recorded as the refusal (`ts` = `time.time_ns()`, `reason` = `str(e)`, `id` null for `pin_top_liquid`) and published at once. It still logs its WARNING.
- **Search:** trim the query and `casefold()` it, then match it as a substring of `symbol` or `instrument_id`. An empty query lists everything.
  - Results are grouped per venue, venues sorted, and ids sorted within a venue.
  - Each group is headed by the Collector header (`<VENUE>: N collected +P pending · cap C`/`· no cap`, `format_section_header` from the venue's current `collector:status` state), plus `· M matches`.
- **Row text:** the `fit()`-bounded id (TUI-02) plus one marker. Each rule applies only when every rule above it does not:
  1. A status row exists and is not pending: `collected`.
  2. A status row exists and is pending: `pending`, or `pending · failed: subscribe failed in last apply <UTC>, retrying` when `last_apply.failed` holds the id.
  3. The latest `last_refusal` names the id and arrived after this TUI sent its add: `failed: <reason>`.
  4. The id is in `unpinned_ids`: `excluded`.
  5. This TUI sent an add and nothing has answered within `ADD_ANSWER_TIMEOUT_SECONDS` (120): `pending`.
  6. Past that timeout: `no answer from <VENUE> collector`.
  7. Otherwise: blank.
- **Refusal order:** `refused` and `new` are compared by arrival order on this TUI, never by the collector's clock. The TUI stores the local receive time when an aggregate's `last_refusal` differs from the previous one.
- **Add refusals:** `a` checks these in order, and the first that applies is shown in the footer with nothing sent:
  1. `command_refusal(venue)` (missing, stale or static plan).
  2. Already collected: `already collected`, or `already in the plan (pending)`.
  3. Excluded: `excluded (unpinned): re-add with :start <ID>`.
  4. An add from this TUI still awaiting its answer: `add already sent, waiting for collector:status`.
  5. The published cap is reached: `cap reached (N)`.

  The same checks run again when the confirm is submitted. Bybit and Hyperliquid have no limit and no capacity estimate. dYdX's cap of 30 refuses.
- **Markets staleness:** a venue with no `markets:live` for `MARKETS_STALE_SECONDS` (180, three missed 60 s polls) shows its rows with `~ `. After `MARKETS_EXPIRE_SECONDS` (900) that venue is dropped from the browser. It is never hidden earlier.
- **TUI-01:** the browser keeps one persistent `ListBox` with its walker updated in place. Focus follows the focused id across a refresh. The walker is rebuilt only when its inputs change (query, markets, status, stale flags, markers), not on every 0.5 s tick.
- **Boundaries:** `bot_tui` never imports `ranking`. `markets:live` is a published-language literal on both sides, like `collector:status`.
- **MR4:** these docs change in the same commit:
  - DATA_DICTIONARY §3 (new `markets:live` subsection) and §1.12 (`last_refusal`)
  - BOT_OPERATIONS (browser keys)
  - DATABASE_SETUP (channel row)
  - ARCHITECTURE (channel list, if one exists)
  - the `_HELP_TEXT` in `app.py`
  - DEPLOY_CHECKLIST (a Deferred operator actions entry for 29-5)

**Block If:**
- The change needs a change under `nautilus_trader/` or `crates/`, or renames a channel, env var or compose service.

**Never:**
- Never write `sprint-status.yaml`, set `awaiting-operator` or write `operator_actions:`. OPS-01 applies: the VPS steps go to DEPLOY_CHECKLIST, and the story finalizes `done`.
- No volume, price or capacity number in the browser. No Bybit or Hyperliquid cap.
- No subscription in `bot_tui` to `rankings:live`, `snapshots:raw` or `ranking:control`.
- No new dependency. Nothing is published optimistically as collected: only `collector:status` makes a row `collected`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Cold open | `/` before any `markets:live` | Body reads `waiting for markets:live…` | none |
| Search | query ` sol ` | Every venue's ids whose symbol or id contains `sol` (case-insensitive), grouped by venue | none |
| Add happy path | `a` on uncollected `SOLUSDT-LINEAR.BYBIT`, type `add` + Enter | `start` sent with `venue: BYBIT`; row `pending` until a status row arrives, then `collected` | none |
| Add refused by the collector | The collector raises `PlanRejected` | The aggregate's `last_refusal` names the id; row reads `failed: <reason>` | WARNING in the collector |
| Subscribe fails | Row pending, id in `last_apply.failed` | `pending · failed: subscribe failed in last apply <UTC>, retrying` | ledgered by capture |
| Already collected or excluded | `a` on such a row | Footer shows the reason; nothing sent | none |
| dYdX full | 30 dYdX rows, `a` on a dYdX id | `cannot add X: cap reached (30)` | none |
| No answer | Sent, no status for 120 s | `no answer from BYBIT collector`; `a` allowed again | none |
| Malformed message | `markets` not a list, an entry without a string id or symbol, or an id of another venue | Message ignored, last good list kept | WARNING |
| Venue stops publishing | No message for 180 s / 900 s | Rows `~ ` / venue group gone | none |

</intent-contract>

## Code Map

- `platform/ranking/application/engine.py`: `volume_cycle` (after `refresh_volumes`), constructor kwargs, `VOLUME_SITE`.
- `platform/ranking/domain/board.py:346-366`: `record_volume_poll`/`refresh_volumes` over `_venue_volumes` (source → (fetched_at, {id: vol})).
- `platform/ranking/application/ports.py`: channel constants and the `LivePublisher` port.
- `platform/ranking/infrastructure/redis.py`: `RedisLivePublisher(client, channel)`.
- `platform/ranking/__main__.py:123-144`: `run()` wiring.
- `platform/kernel/venues.py:67,207`: `venue_of`, `base_symbol`.
- `platform/collection_control/application/status.py`: `plan_aggregate`, `StatusPublisher`.
- `platform/collection_control/application/control.py:96-106`: `handle` (the `PlanRejected` branch).
- `platform/bot_tui/collector_state.py`: status cache, `command_refusal`, `plan_cap`, `collected_count`, `publish_control`.
- `platform/bot_tui/collector_pane.py`: `format_section_header`, `VenueSection`, `_ID_WIDTH`, `_utc_ns_text`.
- `platform/bot_tui/app.py`:
  - view stack
  - `_FOOTER_HINT_TEXTS`
  - `_refresh_breadcrumb`
  - `_build_body`
  - `_redraw_loop`
  - `_handle_modal_guard_key`
  - `_handle_collector_pane_key`
  - the collector confirm guard (`_open_collector_confirm`/`_submit_collector_confirm`, `_COLLECTOR_CONFIRM_VERBS`)
  - `_focused_collector_id`
  - `run()`
- `platform/bot_tui/tests/test_no_rankings_feed.py`: its docstring only.
- `platform/tests/test_boundaries.py:127-128`: the bot_tui channel comment.
- Tests:
  - `ranking/tests/{test_engine,test_replay,test_ports}.py` (the `_engine`/`_Live` helpers)
  - `collection_control/tests/{test_status_replay,test_control}.py` (`_APPENDED_KEYS`)
  - `bot_tui/tests/{test_app_collector,test_collector_pane,test_collector_status_replay}.py`

## Tasks & Acceptance

**Execution:**
- [x] `platform/ranking/application/ports.py`: add `MARKETS_CHANNEL = "markets:live"`.
- [x] `platform/ranking/domain/board.py`: add `venue_markets(now_ns) -> dict[str, list[str]]`, the sorted ids per venue from sources that are not expired. It uses the same age rule as `refresh_volumes`, grouped by `venue_of`.
- [x] `platform/ranking/application/engine.py`: add a `markets: LivePublisher` kwarg. After `refresh_volumes`, `volume_cycle` calls `publish_markets(now_ns)`, which builds each venue's message (`markets_message(venue, ts, ids)`, a pure function) and publishes it.
  - Each failure is ledgered at `MARKETS_SITE = "ranking_engine.markets"`.
  - An id whose `base_symbol` raises is ledgered and left out.
  - Update the module docstring.
- [x] `platform/ranking/__main__.py`: pass `markets=RedisLivePublisher(client, MARKETS_CHANNEL)`.
- [x] `platform/collection_control/application/status.py`: `StatusPublisher.record_refusal(action, iid, reason)`. `plan_aggregate(..., last_refusal)` appends `last_refusal`. Update the docstring's list of appended fields (Story 29.5).
- [x] `platform/collection_control/application/control.py`: in `handle`, on `PlanRejected`, keep the WARNING, then `record_refusal` and publish ledgered through `_publish(frozenset())`. Update the docstring.
- [x] `platform/bot_tui/markets_state.py` (new): the `markets:live` listener, modelled on `archive_state`/`collector_state`.
  - It caches per venue `[(instrument_id, symbol)]` and the receive time.
  - It validates the message whole; a malformed one is WARNING-logged and ignored.
  - It provides `venue_markets_stale(venue, now)` and `live_venues(now)`, which exclude venues that have expired.
- [x] `platform/bot_tui/collector_state.py`: track `last_refusal` per venue with its local receive time, set when it changes. Add the sent-add registry: `record_sent_add(iid, now)`, `sent_add_at(iid)`, and forget an entry once its row appears.
- [x] `platform/bot_tui/market_browser.py` (new, pure, no urwid):
  - `COLD_OPEN_TEXT`, `ADD_ANSWER_TIMEOUT_SECONDS`
  - `search(markets, query) -> list[BrowserGroup]`
  - `result_marker(...)`
  - `add_refusal(...)`
  - `format_group_header(...)`
  - `format_result_line(...)`
- [x] `platform/bot_tui/app.py`: add the `markets` view, entered by `/` from the Collector pane and pushed on the stack.
  - Breadcrumb `Collector > markets`; footer `a add  / search  esc back  :q quit`.
  - `/` opens a footer `urwid.Edit("/")`. Its `postchange` signal refreshes the results live, Enter moves focus to the results, and Esc closes the edit and keeps the query. `/` from the results reopens the edit.
  - `a` runs `add_refusal`, then the confirm guard (typed word `add`, wire action `start`), then `record_sent_add` and publish.
  - Add a persistent-`ListBox` refresh with an input key. Start `markets_state._redis_listener` in `run()`. Add a `_HELP_TEXT` section. The Collector footer gains `/ markets`.
- [x] `platform/bot_tui/collector_pane.py`: add an optional `format_last_refusal_line(plan)` shown under the last-apply line in each venue section, so refusals of `p`/`x`/`:start` are visible too.
- [x] `platform/tests/test_boundaries.py`: add `BOT_TUI_REDIS_CHANNELS = {"bots:status", "collector:status", "archive:status", "markets:live"}` and a test.
  - The test AST-scans `bot_tui` non-test modules for `subscribe(...)` arguments: string literals, or module-level string constants.
  - It asserts that set equals `BOT_TUI_REDIS_CHANNELS`.
  - Update the GRAPH comment: `markets:live` carries names only, and it is still not a `views` edge.
- [x] `platform/bot_tui/tests/test_no_rankings_feed.py`: its docstring notes that `markets:live` (names only) is allowed.
- [x] Tests, covering every I/O matrix row:
  - `ranking/tests/test_engine.py`:
    - one message per venue, with Bybit linear and spot merged
    - the exact bytes and key order
    - an expired source excluded, and a venue with none publishes nothing
    - a publish failure ledgered while the others still go out
  - `collection_control/tests`:
    - `_APPENDED_KEYS` gains `last_refusal`, still a prefix-preserving append
    - a refused `start` records and publishes the refusal
  - `bot_tui/tests/test_market_browser.py` (new): search, markers (every precedence case), refusals (collected, pending, excluded, awaiting, dYdX cap reached, Bybit uncapped at 100 rows), the group header count.
  - `bot_tui/tests/test_markets_state.py` (new): validation, staleness and expiry.
  - `bot_tui/tests/test_app_markets.py` (new, headless like `test_app_collector`):
    - `/` cold open
    - a live filter
    - the add flow with a fake `publish_control`: confirm, the payload has `start` + venue, the row goes pending, then collected after a status message
    - refusal before send
    - the count updating from `collector:status` without a reload
    - focus kept across a refresh
    - Esc handling
- [x] Docs:
  - DATA_DICTIONARY §3.6 `markets:live` (publisher, cadence, shape, freshness) and §1.12 `last_refusal`
  - BOT_OPERATIONS Collector section (`/`, Enter, `a`, markers, cold open, no answer)
  - the DATABASE_SETUP channel table
  - ARCHITECTURE/README channel mentions, where they list channels
  - DEPLOY_CHECKLIST "Deferred operator actions" 29-5: redeploy `ranking_engine`, the collectors and `bot_tui`, then verify `/sol` lists markets and one `a` add on Bybit

**Acceptance Criteria:**
- Given the ranking engine's volume cycle, when it completes, then `markets:live` carries one names-only message per venue with fresh sources, and the TUI's `/` browser searches it with rows marked `collected` from the applied set, or shows `waiting for markets:live…` before the first message.
- Given a focused result, when the operator presses `a` and confirms, then `start` with that venue goes out on `collector:control`, and the row shows `pending` until `collector:status` shows it collected, or shows `failed` with the reason from `last_refusal` or `last_apply`. A collected or excluded id, or a full dYdX cap (`cap reached`), is refused before any message is sent.
- Given `collector:status` updates, when the pane renders, then each venue header reads `<VENUE>: N collected +P pending` with no Bybit/Hyperliquid limit or estimate, and the count changes without a reload.
- Given MR4, when merged, then the docs listed above and `test_boundaries.py`'s `bot_tui` channel record include `markets:live`.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12: (high 0, medium 1, low 11)
- defer: 1: (high 0, medium 1, low 0)
- reject: 9: (high 0, medium 2, low 7)
- addressed_findings:
  - `[medium]` `[patch]` `last_refusal` holds one refusal per venue. A second refusal on the venue, or a restarted collector's `null`, replaced the answer to an add before the browser showed it, and the row fell back to `pending`, then `no answer`. A refused `stop`/`unpin` of the same id also read as the add failing. Now `collector_state` copies a newly arrived refusal of a `start` naming an outstanding add onto that add (`_ADD_REFUSALS`, `add_refused_reason`). The copy is dropped when the add is re-sent or its row appears. `AddContext` carries `refused_reason`. A `Known limit:` covers another sender's refused `start` of the same id.
  - `[low]` `[patch]` Refusal ordering compared two wall-clock readings (`received_at <= sent_at`), which an NTP step could invert. It is now arrival order only: the add is recorded before it is published, and a refusal is attributed when it arrives.
  - `[low]` `[patch]` The local cap check ignored adds still in flight, so two quick adds at 29/30 on dYdX both passed. `_adds_in_flight` now counts this TUI's other awaiting adds on the venue. `add_refusal` has a `Known limit:` saying it is advisory, and that the collector's cap check stays the authority.
  - `[low]` `[patch]` A refusal whose own publish failed was not republished until the full refresh (1800 s). `StatusPublisher` now tracks `_shown_refusal`, so the change poll republishes it within `STATUS_CHANGE_POLL_SECONDS`.
  - `[low]` `[patch]` The markets listener reconnected every 180 s whenever the publisher was legitimately silent (no venue with a fresh volume source). It now PINGs after 180 s of silence and resubscribes only when no pong arrives within `PING_ANSWER_SECONDS`. Tested with a fake pubsub.
  - `[low]` `[patch]` When the query changed and the focused id was filtered out, focus followed the old position onto an arbitrary row, possibly another venue's. A changed query now starts on its first match.
  - `[low]` `[patch]` An old `cannot add`/`sent:` footer echo survived reopening and closing the search. The footer hint is now refreshed when the search closes.
  - `[low]` `[patch]` A `markets:live` message with `ts: true` passed validation, because a bool is an int. It is now rejected.
  - `[low]` `[patch]` The `test_boundaries.py` channel scan missed `psubscribe`/`ssubscribe` and keyword-form channels. It now scans both, and its self-test covers them.
  - `[low]` `[patch]` `format_last_refusal_line` printed `None` for a null reason. It now prints `?`.
  - `[low]` `[patch]` `test_a_refused_command_warns_and_changes_nothing` now asserts exactly one aggregate, published last.
  - `[low]` `[patch]` The group header said `1 matches`. It now says `1 match`.
- deferred: `publish_control` swallows a failed publish while the footer says `sent:`. This predates the story for every collector action. For an add it costs up to 120 s of `pending` before `no answer`.
- rejected:
  - `app.py` reading `collector_state` privates: the existing pattern.
  - up/down not moving through the results while the search edit is open: the documented flow is Enter, then the results.
  - the malformed-id ledger repeating each minute: volume sources only build suffixed ids.
  - the refusal-publish flood from a scripted sender: internal channel.
  - adds from a stale-but-live list: the id existed minutes ago; unlisted ids are already deferred.
  - whole-message rejection: spec intent, never a partial list.
  - a truncated-list heuristic: speculative.
  - an unknown cap treated as uncapped: the collector enforces its cap.
  - duplicate ids: the engine publishes a sorted set.

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 2, low 1)
- defer: 1: (high 0, medium 0, low 1)
- reject: 17: (high 0, medium 3, low 14)
- addressed_findings:
  - `[medium]` `[patch]` `StatusPublisher.publish` built its messages from `_last_refusal`, then set `_shown_refusal` from it again after the bus awaits. `record_refusal` takes no lock, so a refusal recorded mid-burst was marked shown without being sent. If the control service's own publish then failed, that refusal waited up to 1800 s, and the TUI's add read `no answer`. `publish` now reads the refusal once and uses that value for both. Test: `test_a_refusal_recorded_mid_publish_is_republished_at_the_next_poll`, which fails without the fix.
  - `[medium]` `[patch]` An add whose `collector:control` publish never reached Redis read `pending`, then `no answer from <VENUE> collector`, blaming a collector that was never sent anything. The add counted toward the cap and `a` was refused for 120 s. Now `publish_control` returns whether Redis took the message. `_publish_collector_action`'s done callback echoes `failed to send <action> <id>: Redis publish failed` for every collector action, and for an add drops that exact send (`forget_sent_add(id, sent_at)`). Documented in BOT_OPERATIONS.md. Test: `test_an_add_whose_publish_failed_says_so_and_is_not_awaited`.
  - `[low]` `[patch]` The market browser's help says `j/k` move the selection, but nothing mapped them. The browser's ListBox is now `_VimListBox`, which maps `j`/`k` to down/up and returns the original key when unhandled. Test: `test_j_and_k_move_the_selection_as_the_help_says`.
- deferred: the Collector and Bots panes' help also advertises `j/k`, which is unmapped there. This predates the story (Story 6.1 / 4.x).
- rejected:
  - `excluded` refused by `a` although `start` clears the exclusion: spec-mandated (Boundaries §refusals 3, AC).
  - markers never expiring: a refusal or no-answer is the add's answer until a re-send or row, as designed.
  - a refusal matched only by id: `Known limit:` already documents it; each collector refuses only its own venue's ids.
  - the confirm prompt dropping the venue: the id carries the venue suffix.
  - the search edit swallowing non-Enter/Esc keys: the documented modal.
  - markets derived from volume sources only: spec design, documented.
  - the malformed-id ledger repeating each minute, and the Redis publish inline in the volume cycle: rejected last pass, and ranking already publishes inline.
  - `collector_state` privates read from `app.py`: the existing pattern.
  - the channel scan missing aliased `subscribe`: speculative.
  - help/doc wording: `pending` is refused both as a plan row and as an awaiting add, which is consistent.
  - DEPLOY_CHECKLIST fallback steps: the operator check already covers them.
  - untested paths: covered by this pass's tests.
  - focus reset after every list expires: the rows were gone, so a first-row reset is correct.
  - the wall-clock add timeout: consistent with every other TUI staleness check. A multi-minute NTP step is out of scope.
  - an empty-but-fresh source publishing nothing: spec rule (no fresh ids, no message). The list expires at 900 s.

## Design Notes

- **Why `last_refusal`:** today a `PlanRejected` only logs a WARNING, so an add refused by a race (the cap filled, or the id collected from another TUI) would stay `pending` in the browser forever. The AC asks for "the reason from the status payload", and this is the one additive field that carries it. The 120 s no-answer marker covers a message that is lost with no answer at all (a collector down less than the staleness window). It is shown as text and never hides the row.
- **Why a separate `markets` view:** it pushes on the view stack, so Esc pops back to the Collector pane naturally, and its body is a different persistent `ListBox` from the venue sections (TUI-01). Adding `markets` to `_BREADCRUMB_LABELS` would make `:markets` a command, so the breadcrumb is special-cased like `bot_detail`.
- **Why rebuild only on change:** Bybit spot plus linear is about 1,100 ids. Rebuilding that many `Text` widgets every 0.5 s is wasteful. A tuple key of the inputs (the query, each venue's `markets:live` receive time and stale flag, the status/plan receive times, the markers' time-driven flips) decides when to rebuild.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. ranking/tests collection_control bot_tui/tests tests/test_boundaries.py tests/test_images.py capture/venues -q -W default -p no:cacheprovider`: all pass, and the warning count does not rise above HEAD.
- `cd platform && ruff check <changed .py> && ruff format --check <changed .py> && mypy <changed .py>`: clean, with no mypy errors beyond those already at HEAD.

## Auto Run Result

Status: done

**Summary:** a follow-up review of Story 29.5 (market browser, `markets:live`, `last_refusal`), baseline `1a2d283871` through `eea5068dc2`. It found and fixed 3 defects in the story's own code, in commit `2bc7ff148b`.
- **The refusal-publish race** (`last_refusal` could be marked shown without being sent).
- **A failed add publish** (it blamed the collector with `no answer`).
- **`j`/`k` in the market browser** (its help advertised them, but nothing mapped them).

**Files** (under `platform/`):
- `collection_control/application/status.py`: `publish` reads `_last_refusal` once, for both the burst and `_shown_refusal`.
- `bot_tui/collector_state.py`: `publish_control` returns `bool`, and a new `forget_sent_add(id, sent_at)`.
- `bot_tui/app.py`:
  - `_publish_collector_action` takes `add_sent_at`. Its done callback echoes a failed publish and forgets that add.
  - `_VimListBox` backs the market browser.
- `docs/BOT_OPERATIONS.md`: the failed-publish footer.
- **Tests:**
  - `collection_control/tests/test_control.py`: the mid-publish refusal.
  - `bot_tui/tests/test_app_markets.py`: the failed-publish add, and `j`/`k`.
  - `bot_tui/tests/test_app_collector.py`: the stub accepts the new argument.
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new entry for the Collector/Bots panes' unmapped `j/k`, which predates the story.

**Review:** one pass, with Blind Hunter and Edge Case Hunter.
- 3 patches: 2 medium, 1 low.
- 1 deferred.
- 17 rejected. See the triage log.

The new code resolves most of the existing deferred entry about a failed `publish_control` showing `sent:`. Its receiver-count suggestion is not implemented. That ledger entry was left untouched, as instructed. The orchestrator owns its status.

**Follow-up review recommended:** false. The three fixes are small and local, each has a test, and the one behaviour change (`publish_control` returns a bool) is additive.

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. ranking/tests collection_control bot_tui/tests tests capture/tests capture/venues archive/tests views/tests -q -W default -p no:cacheprovider`: 1773 passed, 51 warnings. The previous pass had 1770 passed and the same 51 pre-existing warnings.
- The new race test fails with the `status.py` fix reverted and passes with it.
- `ruff check` and `ruff format --check` (0.15.16) on the 6 changed .py files: clean.
- mypy (1.20.2, via uvx with urwid/redis) on `app.py`, `collector_state.py` and `status.py`: 10 errors, identical with the changes stashed. They are all the environment's urwid-stub class (`SimpleListWalker`/`ListWalker` typing) plus one in `kernel/second_snapshot.py`. There are no new ones.
- Nothing was run on the VPS. The existing 29-5 DEPLOY_CHECKLIST deferred entry still covers it.

**Residual risks:**
- A `publish_control` that succeeds but reaches no subscriber (0 receivers) still reads `sent:`, then `no answer`. That part of the existing deferred entry remains.
- The Collector and Bots panes still advertise `j/k` without mapping them (deferred).
- The earlier pass's residual risks are unchanged: request-id-less refusal attribution (`Known limit:`), the advisory local cap, the PING-based dead-connection detection window, and the per-keystroke rebuild of about 1,100 rows.
