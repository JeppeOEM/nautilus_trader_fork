---
title: 'Story 29.4: Runtime collection control for Bybit and Hyperliquid'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: '3ebd370f558e7a543d801104ea12591b1a6d8778'
final_revision: '68fcf04bdd0b0c3df07306cd8933c9736780985b'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** Only dYdX's collected set can change at runtime. Bybit and Hyperliquid have static plans (cap = their own size, `accepts_commands` false). Adding a coin on them means editing `config.toml` and restarting the container. `collector:control` has no `venue` field, so a command cannot be sent to one venue. Their clients are not idempotent per channel and subscribe with no pacing, which is only harmless while the plan never changes.

**Approach:** Add an optional `venue` field to `collector:control`. A message without it means dYdX. Each venue's `ControlService` acts only on its own venue's messages and refuses an id from another venue. Wire Bybit and Hyperliquid with the same control, status and hot-reload loops as dYdX. Their plans become uncapped (`cap` optional), gain an optional `exclude`, and are written back to their `config.toml`. Make both clients idempotent per channel, and pace each wire frame under a limit measured live, stored as a named constant per venue.

## Boundaries & Constraints

**Always:**
- Wire rule: fields are only added. `collector:control` gains `venue` after the existing keys, so `{action, id}` keeps its bytes as the prefix. A message without `venue` is routed to DYDX (replay-tested with the recorded fixture). `collector:status` keeps its shape. Bybit/Hyperliquid's `cap` becomes `null` (no cap, decision 2026-09-26) and `accepts_commands` becomes `true`.
- dYdX keeps cap 30, its `[[instruments]]` table form, its liquidity pin, and every existing behaviour.
- Bybit and Hyperliquid have no cap. `CollectionPlan.cap` is `int | None`. A plan with a liquidity threshold must have a cap, because a pin fills the cap's free slots. `pin_top_liquid` on these venues is refused by the plan ("admits no pins"), since the operator picks coins by hand.
- Their plan file is the one the collector reads (`BYBIT_COLLECTOR_CONFIG`/`HYPERLIQUID_COLLECTOR_CONFIG`). It is saved through `TomlPlanStore`. `instruments` stays a flat list. `exclude` is an optional flat list, written only when non-empty, so a file with no exclusions keeps exactly its current key set. The compose mount changes `:ro` to `:rw`, with the same paths.
- Removing an instrument clears its book (`LiveBook.forget`) and stops its rows. Its catalog files are untouched (no Bybit/Hyperliquid retention reads the plan). Its rankings age out as stale.
- Pacing lives in the client: every Python-side wire call waits on a pacer at the venue's named constant. The constant cites the measurement, recorded in `docs/DATA_DICTIONARY.md` §1 with the date and method, and the measurement script is committed.
- Per-channel idempotency mirrors each Rust client, verified in `crates/`. Bybit's topic reference is taken before its only fallible step, so a raised subscribe still holds the channel. Hyperliquid takes no reference, so only a success holds it.
- MR4: DATA_DICTIONARY §1/§1.12, BOT_OPERATIONS, README, `platform/CLAUDE.md` and DEPLOY_CHECKLIST change in the same commit.

**Block If:**
- A live endpoint cannot be reached for the measurement.
- The fix needs a change under `nautilus_trader/` or `crates/`, or a renamed env var, service or mount path.

**Never:**
- Never write `sprint-status.yaml`, never set `awaiting-operator`, never write `operator_actions:` (OPS-01). VPS steps go to DEPLOY_CHECKLIST "Deferred operator actions".
- No coin cap and no capacity estimate for Bybit/Hyperliquid.
- No catalog deletion on removal. No new dependency (the measurement uses `aiohttp`, which is already present).
- No market browser, `a` key or `markets:live`: those belong to Story 29.5.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Legacy message | `{"action":"start","id":"X.DYDX"}` | dYdX acts. Bybit/HL ignore it | none |
| Venue-addressed | `{..., "venue":"BYBIT"}` | Only Bybit acts | none |
| Venue/id mismatch | `venue` BYBIT, id `.HYPERLIQUID` (or a legacy message with a Bybit id) | Refused by the receiving plan, nothing changes | WARNING |
| Malformed venue | `"venue": 3` | Ignored by every venue | ledgered `collector.control` |
| Bybit start | New id | Saved, then subscribed (paced), then published, `last_apply.subscribed` | subscribe fails: `pending` plus one `collector.subscribe_failed` per attempt |
| Bybit unpin | Collected id | Removed and excluded (`exclude` written), tombstone published | none |
| Re-start excluded | Excluded id | Added; `exclude` key dropped when it becomes empty | none |
| pin_top_liquid | Bybit/HL | Refused: no liquidity threshold | WARNING |
| Hand edit | `instruments` edited in the file | Reload loop applies the diff within `PLAN_RELOAD_SECONDS` | invalid file: `collector.config_reload`, plan kept |
| Retry after partial subscribe | Channel A held, B failed | Retry sends only B. Removal releases A and B once each | none |

</intent-contract>

## Code Map

- `platform/collection_control/domain/plan.py` -- `CollectionPlan` (`cap: int`, `free_slots`, `add`, `pin`).
- `platform/collection_control/application/control.py` -- `ControlService` (`_handle_message`, `_command`, `_lingering_over_cap`, `_wire_slots`, `_pin_top_liquid`).
- `platform/collection_control/application/status.py` -- `STATIC_PLAN_STATUS_SECONDS` and the aggregate `cap`.
- `platform/collection_control/application/reload.py` -- `reload_loop`.
- `platform/collection_control/infrastructure/plan_store.py` -- `TomlPlanStore.save` (merges `plan_toml_fields`).
- `platform/capture/infrastructure/config.py` -- `_static_plan` (`cap=len(ids)`), `VENUE_SCHEMAS` BYBIT/HYPERLIQUID `plan_keys`, `plan_toml_fields`.
- `platform/capture/venues/{bybit,hyperliquid}/__main__.py` -- today they wire the status loop only. `capture/venues/dydx/__main__.py`'s `control_plane` closure is the pattern to follow.
- `platform/capture/venues/{bybit,hyperliquid}/client.py` -- `subscribe`/`unsubscribe`/`resync_orderbook`.
- `crates/adapters/bybit/src/websocket/client.rs:742-840` -- refcounted topics (`add_reference` comes before the cmd send). `crates/adapters/hyperliquid/src/websocket/client.rs:1077-1600` -- no refcount (the ActiveAssetCtx is shared by mark/index/funding/OI).
- `platform/capture/application/capture_service.py:1647` -- the `_subscribe_one` docstring "Bybit's and Hyperliquid's are not [idempotent]" limit.
- `platform/bot_tui/collector_state.py` -- `publish_control` (no venue), `command_refusal` (its "no venue" known limit, and deferred-work's `plan_is_stale` gap). `bot_tui/collector_pane.py` `format_section_header` (`cap ?`). `bot_tui/app.py` `_publish_collector_action` and `_collector_command_refusal`.
- `platform/docker-compose.yml:124,151` -- the `:ro` config mounts.
- Tests: `collection_control/tests/{test_plan,test_control,test_plan_store}.py`, the `control_payloads.json` fixture, `capture/venues/{bybit,hyperliquid}/tests/{test_status_wiring,test_client}.py`, `capture/tests/test_config.py`, `bot_tui/tests/{test_collector_status_replay,test_app_collector,test_collector_pane}.py`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/scripts/measure_ws_limits.py` -- New independent aiohttp prober:
  - Bybit (linear, spot): args per subscribe request, and a request burst to find the per-second limit.
  - Hyperliquid: a subscriptions-per-connection ramp and a per-second burst.
  - Run it live and keep the output.
  - Covers AC 2.
- [x] `platform/capture/application/wire_channels.py` -- New `WireChannels(frames_per_second)`:
  - `hold(key, send, undo=None)` / `release(key, send, undo=None)`. At most one held reference per (channel, id). Every frame waits on a monotonic pacer. A failed send runs `undo` to restore the Rust client's bookkeeping (amended in review, see triage log).
  - Unit-tested for idempotency, undo on failure, cancellation, and spacing.
- [x] `platform/capture/venues/bybit/client.py` -- Route every channel call (twin feed and `resync_orderbook` included) through `WireChannels` at `BYBIT_WS_FRAMES_PER_SECOND`, with the inverse call as `undo` for every hold and release. The docstring cites the Rust lines and the measurement.
- [x] `platform/capture/venues/hyperliquid/client.py` -- Same, at `HYPERLIQUID_WS_FRAMES_PER_SECOND`. Only the asset-context calls take an `undo`. Also add `HYPERLIQUID_MAX_WS_CHANNELS = 1000`, a channel budget checked before sending.
- [x] `platform/capture/application/capture_service.py` -- Update the `_subscribe_one` docstring: every client is now idempotent per channel.
- [x] `platform/collection_control/domain/plan.py` -- Make these changes:
  - `cap: int | None = None`, and `free_slots -> int | None`.
  - `add` checks the cap only when it is set.
  - New invariant: a threshold requires a cap.
  - Update the docstrings.
- [x] `platform/capture/infrastructure/config.py` -- Make these changes:
  - `_static_plan` builds `cap=None` and reads an optional `exclude`.
  - Add `"exclude"` to BYBIT/HYPERLIQUID `plan_keys`.
  - `plan_toml_fields` writes non-dYdX `exclude` only when non-empty.
- [x] `platform/collection_control/infrastructure/plan_store.py` -- `save` pops the venue's plan keys before merging, so an emptied `exclude` disappears. Docstring: the file is the committed `config.toml` for Bybit/HL.
- [x] `platform/collection_control/application/control.py` -- Make these changes:
  - `_handle_message` reads `venue` (absent means DYDX; a non-string is ledgered) and ignores other venues.
  - `_command` refuses an id whose `kernel.venues.venue_of` is not the plan's.
  - `_lingering_over_cap` returns `[]` when uncapped.
  - `_pin_top_liquid` checks the threshold before the slots.
  - `markets` becomes `MarketsSource | None`.
  - Update the docstring.
- [x] `platform/collection_control/application/{status,reload}.py` -- Rename `STATIC_PLAN_STATUS_SECONDS` to `PLAN_STATUS_SECONDS` (1800 s, for a plan with no liquidity refresh), and update every user. Add `PLAN_RELOAD_SECONDS = 30`, matching dYdX's `config_reload_seconds` default.
- [x] `platform/capture/venues/{bybit,hyperliquid}/__main__.py` -- Wire `StatusPublisher(..., None, accepts_commands=True)`, `TomlPlanStore(config_path, VENUE)`, `ControlService(plan, store, capture, status, None)`, and the three loops (`reload_loop`, `status.loop(lambda: control.plan, ...)`, `control_loop(RedisControlChannel)`).
- [x] `platform/docker-compose.yml` -- Change the Bybit and Hyperliquid config mounts to `:rw`, with the comment "the plan store writes it back".
- [x] `platform/bot_tui/collector_state.py` -- Make these changes:
  - `publish_control(redis_url, action, instrument_id=None, venue=None)` appends `venue`.
  - `command_refusal` refuses a stale plan (closes the deferred-work entry) and drops its known limit.
- [x] `platform/bot_tui/app.py` -- Pass the row's venue (`:pintop` sends DYDX). Skip the cap check when the cap is `null`.
- [x] `platform/bot_tui/collector_pane.py` -- The header reads `· no cap` for an explicit `null` cap, and keeps `cap ?` when the key is absent.
- [x] Tests, covering the I/O matrix rows and AC 3:
  - Plan: uncapped add, threshold-without-cap refused.
  - Config and store: the exclude round trip, a file with no exclusions keeping its key set, the committed configs.
  - Control: venue routing, the legacy fixture going to DYDX, mismatch, malformed venue, uncapped lingering, pin refused.
  - Wiring for Bybit and Hyperliquid: three loops, `accepts_commands`, and one start applied end to end over a fake capture and bus.
  - Client idempotency and pacing.
  - `bot_tui`: publishes with `venue` (the old bytes as prefix), a stale refusal, the `no cap` header.
  - A removal over a real `CaptureService` forgets the book and deletes no file.
- [x] Docs:
  - `docs/DATA_DICTIONARY.md` §1: a "WebSocket subscribe limits" subsection (measured values, date, method, constants).
  - `docs/DATA_DICTIONARY.md` §1.12: `venue`, every venue consuming, `cap` null, the cadences.
  - `docs/BOT_OPERATIONS.md` collector section.
  - `platform/README.md` config section.
  - `platform/CLAUDE.md` "Adding a venue": steps 2, 4 and 5 mention the pacer and the control plane.
  - `docs/DEPLOY_CHECKLIST.md`: a Deferred operator actions 29-4 entry (redeploy, then `:start` a coin on each venue and verify; the committed `config.toml` is rewritten on the VPS, so commit it back before a pull).
  - `_bmad-output/implementation-artifacts/deferred-work.md`: mark the `command_refusal` entry resolved.

**Acceptance Criteria:**
- Given the Bybit and Hyperliquid collectors, when a `start`/`stop`/`unpin` for their venue arrives on `collector:control`, then the plan is saved to their `config.toml`, applied through `CaptureService.apply` (`Applied`, `pending` plus one `collector.subscribe_failed` per failed attempt), and published on `collector:status` with the unchanged row and aggregate shape. A message without `venue` still drives dYdX alone.
- Given `docs/DATA_DICTIONARY.md` §1, when read, then each venue's measured subscribe limits show the date, the method (`scripts/measure_ws_limits.py`) and the pacing constant. Each client paces every wire frame under that limit, and neither Bybit's nor Hyperliquid's plan has a cap, while dYdX's stays 30.
- Given an instrument removed from a Bybit/Hyperliquid plan, when the apply runs, then its `LiveBook` is forgotten, no further row is buffered for it, and no catalog file is deleted.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 2, medium 3, low 4)
- defer: 0
- reject: 4: (high 0, medium 1, low 3)
- addressed_findings:
  - `[high]` `[patch]` Hyperliquid's Rust asset-context calls mutate `asset_context_subs` before their fallible send, so a failed first mark subscribe left the retry sending no `activeAssetCtx` frame. `WireChannels` now takes a per-call `undo` that restores the Rust bookkeeping after a failed send, replacing the `reference_kept_on_failure` flag. Bybit passes the inverse on every call; Hyperliquid on its asset-context calls. A cancellation during the pacing wait changes nothing.
  - `[high]` `[patch]` With no cap, a Hyperliquid plan could pass the venue's measured 1000 channels per IP, which the venue rejects asynchronously and silently. The client now counts the channels it holds and raises before sending past `HYPERLIQUID_MAX_WS_CHANNELS`, so the id shows `pending` and ledgers `collector.subscribe_failed`. This is a venue limit, not a plan cap. `Known limit:` other sockets on the same IP are not counted.
  - `[medium]` `[patch]` `bot_tui` `command_refusal` let DYDX commands through when no aggregate had ever arrived (dYdX stopped). Every venue with no aggregate is now refused. `Known limit:` a collector stopped less than 1 h ago still gets commands.
  - `[medium]` `[patch]` A single-file bind mount follows the inode, so a host-side replace (`git checkout`/`pull`, `sed -i`, rename-saving editors) is invisible to the container, and later saves land in the orphaned file. The checklist now says `docker compose restart <service>` after a replace, and the `Known limit:` is in `TomlPlanStore`, both `config.toml` files and the README.
  - `[medium]` `[patch]` A hand-edited id of another venue was hot-reloaded into a plan. `CollectionPlan` now refuses any collected or excluded id of another venue.
  - `[low]` `[patch]` An unknown venue string on `collector:control` is now ledgered instead of silently dropped.
  - `[low]` `[patch]` `CollectionPlan.cap` is required again; `None` is passed explicitly for uncapped plans.
  - `[low]` `[patch]` The pacing rationale now states what was measured (a one-off 200-request burst, a 20/s ramp) and that the constants are chosen margins. Added a `Known limit:` for apply latency under the subscription lock.
  - `[low]` `[patch]` A failed unsubscribe on the trades-only twin socket now raises, so capture retries it instead of leaking it. A failed twin subscribe stays non-fatal.
- rejected: an explicit `"venue": null` being ledgered (loud, and no producer sends it); commands rewriting the committed file (epic decision, documented `Known limit:` with upgrade path); the duplicated legacy-venue literal across contexts (published-language rule, replay-tested on both sides); stub-based tests not modelling Rust state (addressed by the undo tests).

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 0, medium 2, low 11)
- defer: 1: (high 0, medium 1, low 0)
- reject: 6: (high 0, medium 2, low 4)
- addressed_findings:
  - `[medium]` `[patch]` A dYdX collector still on a pre-29.4 image (the `dydx` profile, which `make redeploy-all` never restarts) ignores `venue` and does not check an id's venue, so a new TUI's `:start SOLUSDT-LINEAR.BYBIT` would also land in `data/dydx_config.toml`. The DEPLOY_CHECKLIST 29-4 entry now moves the dYdX collector onto the new image (`make up-dydx`) before any command is sent.
  - `[medium]` `[patch]` The last pass's "no aggregate cached, refuse every venue" means a freshly started TUI refuses every command, dYdX's too, for up to 1800 s (pub/sub keeps no history). This is now a `Known limit:` in `command_refusal` with its workaround (any publish, e.g. a collector restart) and upgrade path (an aggregate heartbeat, or the last publish kept in a Redis key), and is stated in BOT_OPERATIONS. The spec's 1800 s cadence was kept rather than changed in review.
  - `[low]` `[patch]` `WireChannels` said a failed undo is "best effort" without saying why that is safe. The docstring now shows each inverse changes the Rust bookkeeping before its own fallible send, and that Bybit's `unsubscribe` never raises, so a failed undo has still restored the state.
  - `[low]` `[patch]` The `WireChannels` cancellation docstring claimed the pyo3 call always completes; pyo3-async-runtimes drops the Rust future on cancel. Corrected: recording it as done is the likely outcome, and a wrong guess costs nothing, since capture cancels only at `run()` teardown and `run_forever` builds a new client each attempt.
  - `[low]` `[patch]` Bybit `unsubscribe`'s docstring said a failure raises for capture to retry; the Rust `unsubscribe` swallows its send failure (client.rs:834-836). The docstring now states that and why it is still consistent.
  - `[low]` `[patch]` Both clients' "the next subscribe of the id retries" twin-trades comment was false in practice: the id counts as applied, so the retry loop never retries it. Corrected in both.
  - `[low]` `[patch]` Bybit `resync_orderbook` found the book channel by list position; it now looks it up by name.
  - `[low]` `[patch]` Both clients' reconnect-replay `Known limit:` said "bounded by the plan's size" without saying the plan is uncapped, and the apply-latency limit omitted that at start the whole plan is applied before the status and control loops run. Both now say so, with the numbers.
  - `[low]` `[patch]` DEPLOY_CHECKLIST: a `git checkout`/`pull` replaces the plan file with one owned by the git user, so the uid-1000 check is re-run after every replacement.
  - `[low]` `[patch]` DATABASE_SETUP claimed the plan files are the only `rw` config mounts; the three UI preference files are `rw` too. Reworded.
  - `[low]` `[patch]` `scripts/measure_ws_limits.py` and DATA_DICTIONARY §1.14 now warn never to run the Hyperliquid ramp from the VPS, since it holds that IP's whole 1000-channel budget.
  - `[low]` `[patch]` `test_the_client_paces_at_the_measured_*_rate` were renamed `..._named_*_rate`: the constants are chosen margins, not measured ceilings.
  - `[low]` `[patch]` `command_refusal` answered an id with an unregistered venue token (`eth-usd-perp.hyperliquid`) "waiting for hyperliquid plan", as if it were only delayed. It is now refused as an unknown venue (new test).
- deferred: a `start` of an id the venue does not list is saved to the plan and stays `pending` for good (pre-existing on dYdX; a `listed` query on the `Capture` port is the fix).
- rejected: the Hyperliquid 1000-channel refusal coming after the plan save (the spec forbids a cap or capacity estimate, and the per-attempt ledger entry is the specified outcome); commands to a stopped collector lost silently (the documented `Known limit:`); tests pinning the committed `config.toml` that commands now rewrite (the epic decision, already a residual risk); one malformed `venue` ledgered by every collector (each consumer's own record); duplicated wiring tests per venue (the per-venue wiring pattern; routing is unit-tested in `collection_control`); the twin channel counted in the Hyperliquid budget (the conservative direction, only at the limit).

## Design Notes

- **Why not `awaiting-operator`:** the invocation allows it for human-only actions. `platform/CLAUDE.md` OPS-01 (the binding project rule, confirmed by the operator's standing feedback) forbids it and routes VPS steps to DEPLOY_CHECKLIST, so the story finalizes `done`.
- **Pacing is per Python call, not per wire frame.** Hyperliquid's mark/index/funding/OI calls share one `activeAssetCtx` frame, so pacing each call over-counts, which is the safe direction. The Rust clients' reconnect replay is not paced by us. `Known limit:` in the client docstring: bounded by the plan size, and the upgrade path is a pacing hook in the Rust client (outside `platform/`).
- **Uncapped means `None`, never a large int:** `collector:status` publishes `null`, and the TUI renders "no cap".

## Verification

**Commands:**
- `cd platform && python3 -m pytest collection_control capture/tests capture/venues bot_tui/tests tests/test_boundaries.py tests/test_images.py -q -p no:cacheprovider` -- expected: all pass, with no new warnings.
- `cd platform && ruff check <changed .py> && ruff format --check <changed .py> && mypy <changed .py>` -- expected: clean.
- `cd platform && python3 scripts/measure_ws_limits.py --venue bybit` and `--venue hyperliquid` -- expected: results match DATA_DICTIONARY §1.

## Auto Run Result

Status: done

**Summary:** Bybit's and Hyperliquid's collected sets are now live plans, like dYdX's.
- `collector:control` gains an optional `venue` field, appended after `{action, id}`. A message without it means DYDX, and this is replay-tested against the recorded fixture.
- Each collector acts only on its own venue. It refuses an id of another venue, ledgers an unknown or malformed venue, and runs the same control, status and hot-reload loops as dYdX.
- The two plans have no cap (`cap: null` on `collector:status`, shown as `no cap` in the TUI). They accept an optional `exclude`, written only when non-empty, and are written back to their `config.toml`, whose compose mount is now `:rw`. `pin_top_liquid` is refused for them, and dYdX keeps its cap of 30.
- Each client holds every channel at most once through `capture/application/wire_channels.py`, with an undo that restores the Rust client's bookkeeping after a failed send.
- Each client paces its wire calls: `BYBIT_WS_FRAMES_PER_SECOND = 20`, `HYPERLIQUID_WS_FRAMES_PER_SECOND = 10`. Hyperliquid refuses a subscribe past its 1000-channel per-IP budget before sending, so the id shows `pending` and the refusal is ledgered.
- Removing an instrument forgets its book and deletes no catalog file.
- The TUI sends the venue with every command and refuses any venue whose plan is missing or stale.

**Measurement** (`platform/scripts/measure_ws_limits.py`, recorded in DATA_DICTIONARY §1.14):
- Bybit, 2026-09-28: linear accepted 10, 11, 20 and 50 args per request; spot refused 11 or more ("args size >10"). A burst of 200 subscribes, then 200 unsubscribes, was all acked on both markets.
- Hyperliquid, 2026-09-29: a burst of 100 frames was all acked. A 20/s ramp was refused from subscription 1001 onward ("Cannot subscribe to more than 1000 channels.").

**Files** (all under `platform/` unless noted):
- **Collection control:**
  - `collection_control/domain/plan.py`: optional (explicit) cap and the venue-id invariant.
  - `application/control.py`: venue routing.
  - `application/status.py` and `application/reload.py`: `PLAN_STATUS_SECONDS` and `PLAN_RELOAD_SECONDS`.
  - `infrastructure/plan_store.py`: plan-key replacement and the inode `Known limit:`.
- **Capture:**
  - `capture/infrastructure/config.py`: flat plans with `exclude`, no cap.
  - `capture/application/wire_channels.py` (new) and `feed.py` (twin send helper).
  - `capture/venues/{bybit,hyperliquid}/client.py`: channels, pacing and the Hyperliquid budget.
  - `capture/venues/{bybit,hyperliquid}/__main__.py`: the control plane is wired.
  - `capture/venues/{bybit,hyperliquid}/config.toml`: comments only.
- **TUI:** `bot_tui/collector_state.py`, `app.py`, `collector_pane.py`: venue on publish, stale and missing-plan refusal, `no cap`.
- **Compose:** `docker-compose.yml`: config mounts `:rw`.
- **Measurement:** `scripts/measure_ws_limits.py` (new).
- **Docs:** DATA_DICTIONARY §1.12 and §1.14, BOT_OPERATIONS, README, `platform/CLAUDE.md` "Adding a venue", DATABASE_SETUP, ARCHITECTURE, and DEPLOY_CHECKLIST (the 29-4 deferred operator actions entry).
- **Deferred work:** `_bmad-output/.../deferred-work.md`: the `command_refusal` entry was reworded to say what is now covered.
- **Tests:** new `capture/tests/test_wire_channels.py` and `test_control_wiring.py` for both venues (replacing `test_status_wiring.py`), plus additions across the plan, store, control, config, client, apply, bot_tui and boundaries tests.
- **Follow-up pass (2026-09-29):**
  - `capture/application/wire_channels.py`: undo and cancellation docstrings.
  - `capture/venues/bybit/client.py`: the unsubscribe docstring, the resync lookup by name, the twin comment and the Known limits.
  - `capture/venues/hyperliquid/client.py`: the twin comment and the Known limits.
  - `bot_tui/collector_state.py`: the unknown-venue refusal and the fresh-TUI `Known limit:`.
  - Tests: `bot_tui/tests/test_app_collector.py` (+1 test), and the pacing tests renamed in both `capture/venues/*/tests/test_client.py`.
  - Docs: BOT_OPERATIONS, DATABASE_SETUP, DATA_DICTIONARY §1.14, DEPLOY_CHECKLIST 29-4, and the `scripts/measure_ws_limits.py` docstring.
  - Deferred work: one new entry.

**Review:**
- First pass: 9 patches (2 high, 3 medium, 4 low), 0 deferred, 4 rejected.
- Follow-up pass (2026-09-29): 13 patches (2 medium, 11 low), 1 deferred, 6 rejected. See the triage log.
- The follow-up's patches:
  - **Deploy order:** a DEPLOY_CHECKLIST step moves a still-running dYdX collector onto the 29.4 image before any venue-addressed command. A pre-29.4 one would add a Bybit id to dYdX's plan.
  - **Fresh-TUI refusal:** a TUI started between two publishes refuses commands for up to 30 min. This is now a documented `Known limit:` (code and BOT_OPERATIONS) with a workaround and an upgrade path.
  - **Unknown venue:** `command_refusal` refuses an unregistered venue token as unknown.
  - **Book resync:** Bybit's resync looks up the book channel by name, not by list position.
  - **Accuracy fixes:** docstrings and docs corrected on undo safety, cancellation, Bybit's never-failing `unsubscribe`, the twin-trades retry, the uncapped reconnect replay and startup apply latency, the uid re-check after a file replacement, the `rw` mounts, and a VPS warning for the measurement script.
- Deferred to the ledger: a `start` of an id the venue does not list is saved and stays `pending` for good. This predates 29.4 on dYdX.

**Follow-up review recommended:** false. The follow-up pass changed no behaviour beyond one TUI refusal branch and a lookup by name, both tested. The rest is documentation, and each item is local.

**Verification:**
- Follow-up pass: `python3 -m pytest -o addopts="" --rootdir=. collection_control capture/tests capture/venues bot_tui/tests tests archive/tests ranking/tests views/tests -q -W default`: 1685 passed, 0 failed, 51 warnings (the same pre-existing ResourceWarnings as before). `ruff check` and `ruff format --check` (0.15.16) on the changed `.py` files: clean. `mypy` 1.20.2 on them: the same 10 errors as a `git archive HEAD` control, none new.
- `python3 -m pytest collection_control capture/tests capture/venues bot_tui/tests tests archive/tests ranking/tests views/tests -q`: 1684 passed, 0 failed.
- `tests/test_legacy_names.py` after the commit: 40 passed.
- The subagent reports no new warnings under `-W default`: 51, the same count as HEAD, all older ResourceWarnings.
- `ruff check` and `ruff format --check` on the 36 changed `.py` files: clean.
- mypy: only errors that were already there.
- `docker compose config -q`: OK.
- Nothing was run on the VPS. That is the DEPLOY_CHECKLIST 29-4 deferred entry (OPS-01).

**Residual risks:**
- The Rust reconnect replay is not paced by us.
- The Hyperliquid budget counts only this process's sockets.
- Commands rewrite the committed `config.toml` (comments lost; the VPS checkout shows it as modified).
- A single-file mount misses host-side file replacements until the container restarts.
- A collector stopped less than an hour ago still receives TUI commands.
- A freshly started TUI refuses every venue's commands until that venue next publishes (up to 30 min).
- An unlisted id `start`ed on a venue stays `pending` in the plan (deferred).
- The HL reconnect replay of a large uncapped plan bursts past anything measured.
- The Bybit rate is a chosen margin: no sustained-rate ceiling was measured.
