---
title: 'Story 25.4: collection_control/ context: the plan is the intent, the applied set is the fact'
type: 'refactor'
created: '2026-09-26'
status: 'done'
baseline_revision: 'c9fab9c5d793f103bf28b44c2729e5bd827e046b'
final_revision: '75c469c57dbdd2749163fae82d9101229582316e'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/25-4-collection-control-plan-intent-vs-applied-set.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** dYdX's control plane lives inside `DydxCollector` (`dydx_collector/collector.py:301-538`), and the sampler iterates the *plan* (`_instrument_ids()` = config instruments), not what the feed actually subscribed:
- a wire-failed unsubscribe leaves an instrument sampled and archived while `collector:status` says it was removed;
- a failed subscribe raises out of the control handler, or out of `run()` and restarts the whole collector;
- `_process_data` books and archives any instrument's message.

The venue `config.toml` also has two schema owners: `dydx_collector/config.py`'s key-by-key loader, and `collector_core/config.py`.

**Approach:**
- New `collection_control/` context:
  - `domain/`: the `CollectionPlan` aggregate, `LiquidityTier`, `classify_liquidity`.
  - `application/`: `ControlService`, `StatusPublisher`, the reload loop.
  - `infrastructure/`: `TomlPlanStore`, the Redis adapters, the dYdX markets adapter.
- `Collector.apply(diff) -> Applied` makes capture's applied set the fact. The sampler, the watchdog, the cross-check and the backfill iterate `applied ∩ plan`.
- `collector_core/config.py` becomes the one venue-config loader, returning `(CoreConfig, CollectionPlan)`.
- The expired `ranking_engine` shims are deleted.

## Boundaries & Constraints

**Always:**
- `collector:status` stays byte-identical to the recording of today's `_publish_status` for these messages: every per-instrument row of an applied instrument (`{"id","liquid","last_trade_ts","trade_backfill"}`, in that order), the `{"unpinned_ids": [...]}` aggregate, and the `{"id", "removed": true}` tombstone.
  - The one addition: a planned-but-unapplied instrument's row appends `"pending": true`.
  - `bot_tui/` code is not modified.
  - The recorded fixture, and a replay through `bot_tui.collector_state`/`collector_pane`, prove this.
- `collector:control` keeps its `{action, id}` actions exactly: `start` (plan `add`), `unpin`, `stop` (plan `remove`) and `pin_top_liquid` (plan `pin`). A rejected command, or an unknown action, logs a WARNING and changes nothing, as it does today.
- Each venue keeps its `config.toml` key set. What is new:
  - dYdX's file now goes through `core_config_from_dict`'s strictness: an unknown key refuses start, and `stale_book_seconds` and the other core keys are now honoured;
  - a dYdX `[[instruments]]` entry accepts only `id`, `store_order_book_deltas` and `retain_hours`.
- Plan invariants hold at construction (`__post_init__`); a violation raises `ValueError` naming the ids:
  - `excluded ∩ collected = ∅`;
  - `|collected| ≤ cap` (dYdX `cap = 30`; a static plan's cap is its own size);
  - ids are unique;
  - `retain_hours` is `None` or ≥ 0.
- A pin is admitted only from a `LiquidityClassification` whose `min_volume_usd` equals the plan's `min_liquidity_usd`.
- The order is save, then apply, then publish: control validates through the one loader before `TomlPlanStore.save`, and applies only a saved plan.
- Capture:
  - A book, or book state, exists only for `applied ∩ plan`: it is cleared when an instrument leaves the plan, whether or not the wire unsubscribe succeeds.
  - A book or trade message for a non-(applied ∩ plan) instrument is counted and never booked, folded or archived. Counts are ledgered once per flush at `collector.unplanned_message`.
  - A subscribe that fails on the wire is ledgered at `collector.subscribe_failed` once per attempt, shown as pending, and retried by capture's own retry loop. An unsubscribe that fails is ledgered at `collector.unsubscribe_failed` and retried too.
- The layering and state rules:
  - AD-D2 layering, with each aggregate/port docstring naming its invariant (DESIGN-01);
  - no module-level mutable state in `collection_control`: `_STATE_RULED_CONTEXTS` gains it;
  - every tolerated failure is ledgered (DATA-07).
- `archive.RetentionPolicy` remains the only deleter. Control holds no prune loop.

**Block If:**
- The byte identity of the existing `collector:status` message shapes cannot be kept.

**Never:**
- Touch `nautilus_trader/`, `crates/`, `bot_tui/` code or `sprint-status.yaml`.
- Rename a Redis channel, compose service, env var, or the `/app/dydx_collector/config.toml` bind mount.
- Gate mark/index/funding/open-interest messages by the applied set: they also arrive on venue-wide channels (`subscribe_global`, the OI poll) and are archived for every market today.
- Let control delete catalog files, or let capture import `collection_control`. The only exceptions are the composition roots and the one loader (see Design Notes).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Subscribe fails on wire | `apply(added={X})`, client `subscribe(X)` raises | `Applied(failed={X})`; X not sampled; status row has `"pending": true`; retry loop later subscribes → sampled | `collector.subscribe_failed` per attempt |
| Unsubscribe fails on wire | `apply(removed={X})`, `unsubscribe(X)` raises | `Applied(failed={X})`; book cleared; X not sampled; its later book/trade messages counted not booked; retried | `collector.unsubscribe_failed`; `collector.unplanned_message` per flush |
| Unlisted id | plan id absent from `fetch_instruments` | pending, never subscribed or retried | ledgered `collector.subscribe_failed` once per apply |
| Re-add while still subscribed | X in applied (failed unsubscribe), then `add` X | retry cancelled; a fresh book is forced via `_resync` when the client can resync | — |
| start at cap / already collected | 30 collected, `start Y` | WARNING, file and subscriptions unchanged | — |
| pin_top_liquid | free slots N, markets JSON | top-N USD-volume liquid ids not collected/excluded appended, saved, applied | unparseable volume → `open_interest.volume24h` |
| Save fails | file unreadable / invalid mid-edit | command not applied, plan unchanged | `collector.control` ledgered |
| Reload of bad file | hand-edited invalid TOML | current plan kept, collector keeps running | `collector.config_reload` ledgered |
| Load violates invariant | 31 instruments or exclude∩collected | start refused, `ValueError` naming ids | fail closed |

</intent-contract>

## Code Map

- `platform/dydx_collector/collector.py` -- control plane `:301-538` to move; `DydxCollector` keeps `__init__`, the book hooks, `_resync_book` and `_open_interest_loop` (capture). `main()` becomes the composition root.
- `platform/dydx_collector/{config.py,open_interest.py}` -- `InstrumentEntry`/`DydxConfig`/`load_config`/`save_config`, `classify_liquidity` (`:41`), and `_fetch_markets_json` (`:102`, rename it public: `fetch_markets_json`).
- `platform/collector_core/collector.py`:
  - `_instrument_ids` `:652`, `_clear_book_state` `:655`, `_process_data` `:784`, `_sample_tick` `:1214`, `run()` `:1861-1926`, `run_forever` quarantine `:2007`;
  - the other `_instrument_ids` users are `_crosscheck_loop` `:1625`, `_run_backfill` `:1717` and `_book_watchdog_message` `:1830`;
  - the flush reporting is `_report_stale_trades`.
- `platform/collector_core/{config.py,ports.py}` -- the one loader, plus capture's new port/value types.
- `platform/bybit_collector/{config.py,collector.py}`, `platform/hyperliquid_collector/{config.py,collector.py}` -- move onto the one loader. Bybit's `_open_interest_loop` uses `_instrument_ids()` `:157`.
- `platform/archive/prune_catalog.py:67,129-138` -- reads the plan; repoint to `TomlPlanStore(...).load()`.
- Tests:
  - `dydx_collector/tests/{test_collector_control,test_config,test_open_interest,test_collector_resilience,test_collector_trade_ohlc,test_candle_feed}.py`;
  - `collector_core/tests/{test_collector,test_venue_time,test_book_check,test_capture_lock}.py`;
  - `bybit_collector/tests/*`, `hyperliquid_collector/tests/test_candle_wiring.py`, `tests/test_hotpath.py:237`.
  - All of these build a collector or a config and need a plan and an applied set.
- `platform/tests/test_boundaries.py`:
  - `THIS_STORY` `:64`;
  - the maps `:139-167`, `COMPOSITION_ROOTS` `:186-207` and `LEGACY_PACKAGES` `:253-256`;
  - `ranking_engine` mentions `:29,150,481,1158,1162`;
  - `_STATE_RULED_CONTEXTS` `:1315`.
- `platform/ranking_engine/` -- the four shims expire at this story: delete them.
- Wiring and docs:
  - `collector.dockerfile:13-28` and `Makefile:148-150`;
  - `ARCHITECTURE.md` (module map `:27-43`, collectors §1 `:120-181`, Redis table `:365-366`) and `CLAUDE.md` (intro, DATA-01's dYdX loader text, "Adding a venue" steps 2–4);
  - `docs/DATA_DICTIONARY.md` (`collector:status`), `docs/DEPLOY_CHECKLIST.md`;
  - `research/__init__.py:33`, `research/BACKTESTING.md:133`, `bot_tui/tests/test_ad8_boundary.py:19-21`.

## Tasks & Acceptance

**Execution:**
- [x] Scratch, before any edit: drive the pre-move `DydxCollector._publish_status`/`_publish_removed` with a `_FakeRedis`, a fixed plan (with an `exclude`), a liquid set, `_last_book_update_ns` and backfill counts. Record the published strings in `platform/collection_control/tests/fixtures/status_payloads.json`.
- [x] `collector_core/ports.py`:
  - `PlanDiff` Protocol: read-only `added`, `removed` and `store_deltas` (`frozenset[str]`; `store_deltas` is the complete post-change set of ids whose raw deltas are archived);
  - frozen dataclasses `Applied(subscribed, unsubscribed, failed)` and `CaptureStatus(applied, pending, last_book_update_ns, trade_backfill)`.
- [x] `collector_core/collector.py`:
  - `Collector.__init__(..., *, plan: Iterable[str])` holds the plan mirror `_plan_ids`, plus `_applied`, the retry sets, `_unplanned_messages` and `_listed`.
  - `async apply(diff) -> Applied`:
    - on add, mark the id applied before awaiting `subscribe`, so its snapshot is not dropped; on failure, un-mark it, clear its book, queue a retry and ledger;
    - on remove, leave the plan and clear the book first, then `unsubscribe`;
    - on re-add of a still-applied id, `_resync` when the client can resync.
  - `capture_status()` returns a `CaptureStatus`.
  - `_instrument_ids()` returns `sorted(applied ∩ plan)`.
  - The `_process_data` gate covers book deltas and trades only.
  - `_subscription_retry_loop` (30 s) joins the core loops.
  - `run()` records `_listed` and applies the initial plan through `apply`.
  - `run_forever` quarantines on the plan ids.
- [x] `collector_core/config.py`:
  - `CoreConfig` loses `instruments` (the key still parses, into the plan);
  - `BybitConfig` and `DydxConfig` (`network`, `open_interest_poll_seconds`, `config_reload_seconds`, `liquidity_check_seconds`) move here;
  - a frozen `VENUE_SCHEMAS` table covers DYDX, BYBIT and HYPERLIQUID: environments, extra keys, defaults (HL `stale_book_seconds` 12), plan shape and cap;
  - `venue_config_from_dict(raw, venue)` and `load_venue_config(path, venue) -> (CoreConfig, CollectionPlan)`, plus `plan_toml_fields(plan)` (the writer half of the schema).
- [x] `collection_control/domain/{plan,liquidity}.py`:
  - `InstrumentEntry`, `PlanDiff(plan, added, removed, store_deltas)`, `PlanRejected(ValueError)`;
  - `CollectionPlan(venue, instruments, cap, excluded, min_liquidity_usd, non_config_retain_hours)`, with `collected`, `pins`, `free_slots`, `delta_store_ids` and `delta_retain_hours`, and the commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload`;
  - `LiquidityTier`, and a `LiquidityClassification(liquid, illiquid, volumes, unparseable, min_volume_usd)` returned by a pure `classify_liquidity` (same split and tie order as today; no ledger call).
- [x] `collection_control/application/`:
  - `ports.py`: channels, plus `Capture`, `PlanStore`, `StatusBus`, `ControlChannel` and `MarketsSource`;
  - `status.py`: a pure `status_messages(plan, liquid, capture_status) -> list[str]`, and `StatusPublisher` (`refresh`, `publish`, `publish_removed`, `loop`, which runs first and then sleeps);
  - `control.py`: `ControlService` (owns the current plan under an `asyncio.Lock`; `handle(action, iid)`, `reload()`, `control_loop`);
  - `reload.py`: `reload_loop`.
- [x] `collection_control/infrastructure/`:
  - `plan_store.py`: `TomlPlanStore(path, venue)`. `load` goes through the one loader. `save` re-reads the file, replaces `instruments`/`exclude`, validates through the loader (the round-trip must equal the plan), and rewrites in place.
  - `redis.py`: `RedisStatusBus`, `RedisControlChannel`.
  - `markets.py`: `DydxMarkets`, over `dydx_collector.open_interest.fetch_markets_json`.
  - `collection_control/__init__.py`: the context docstring.
- [x] `dydx_collector/collector.py`:
  - `DydxCollector(config, plan_ids, *, store_deltas, control_plane)`;
  - an `apply` override updates `_delta_store`, then calls `super`;
  - a `build_collector(path)` composition root wires the control plane loops as `extra_loops`, and `main()` uses it.
- [x] Bybit/HL entrypoints -- `load_venue_config`, and pass `plan=plan.collected`.
- [x] Shims (`REMOVE_AFTER = "26-2-capture-package-and-venue-packages-with-entrypoints"`):
  - `dydx_collector/config.py` re-exports `DydxConfig` and `InstrumentEntry`;
  - `bybit_collector/config.py` re-exports `BybitConfig`;
  - `dydx_collector/open_interest.py` serves `classify_liquidity` via `_MOVED_NAMES`/`MOVED_NAMES_REMOVE_AFTER`/`__getattr__`;
  - delete `hyperliquid_collector/config.py`.
  - Update every in-repo caller.
- [x] Delete `platform/ranking_engine/` and its COPY line, map entries and doc mentions.
- [x] Tests:
  - `collection_control/tests/`:
    - one invariant test per plan command;
    - the ported control-plane and config tests;
    - a `PlanStore` contract test against `TomlPlanStore`;
    - the status replay test against the fixture;
    - the matrix rows.
  - `collector_core/tests/test_apply.py`: a fake client failing subscribe/unsubscribe on the wire, the retry, the unplanned counting, the pending status.
  - `bot_tui/tests/test_collector_status_replay.py`: the recorded strings run through `_handle_status_message` plus `format_collector_line`, with a pending row.
  - Update the existing helpers to pass `plan=` and set `_applied`.
- [x] `tests/test_boundaries.py`:
  - `THIS_STORY` → 25-4;
  - `COMPOSITION_ROOTS` gains `dydx_collector.collector` and `collector_core.config` → `COLLECTION_CONTROL`, and `archive.prune_catalog` stays;
  - the map/symbol rows are updated;
  - `_STATE_RULED_CONTEXTS` gains `collection_control`;
  - `ranking_engine` is removed.
- [x] Wiring and docs:
  - `collector.dockerfile`: COPY `collection_control`, drop `ranking_engine`;
  - Makefile `test` += `collection_control/tests`;
  - `ARCHITECTURE.md` (a `collection_control/` row, control as its own context, the `pending` key);
  - `CLAUDE.md`: intro, DATA-01's dYdX-loader sentence, "Adding a venue" steps 2–4 (the client subscribe cap now cites `collection_control`'s cap; step 3 becomes a `VENUE_SCHEMAS` row; step 4 passes `plan=`);
  - `DATA_DICTIONARY.md` `collector:status`;
  - a `DEPLOY_CHECKLIST.md` Story 25.4 VPS step: check `data/dydx_config.toml` for unknown keys, fewer than 30 ids, `exclude ∩ instruments = ∅`, and the core keys now honoured.

**Acceptance Criteria:**
- Given the platform suite (`make test` list incl. `collection_control/tests`), when it runs with `-W default`, then there are no failures beyond the 3 baseline Redis `data_api` ones and no new warnings.
- Given `test_boundaries.py`, `test_images.py` and `test_namespace.py`, when they run, then the collector image's closure includes `collection_control`, every shim resolves to its new object, and no module outside the composition roots and the one loader crosses capture→collection_control.
- Given the recorded pre-move payloads, when the new `StatusPublisher` publishes the same state, then every string is identical.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 12 (high 0, medium 5, low 7)
- defer: 1 (high 0, medium 1, low 0)
- reject: 3 (high 0, medium 0, low 3)
- addressed_findings:
  - `[medium]` `[patch]` The retry loop ran outside any lock, so `apply` and a retry round could interleave:
    - a removal mid-round let the round subscribe an id that was no longer planned, leaving it applied with nothing tracking it;
    - a re-add mid-round let the round unsubscribe a planned id.
    - Fix: `Collector._subscription_lock` serializes `apply` and every retry round.
    - Regression test `test_a_removal_during_a_retry_round_leaves_no_untracked_subscription`. It fails with the lock removed.
  - `[medium]` `[patch]` dYdX `subscribe`/`unsubscribe` are two channel calls, so a half failure was retried as a whole.
    - `DydxClient.subscribe` now rolls the trades channel back when the orderbook subscribe fails. A failed rollback is ledgered at `collector.subscribe_rollback`.
    - `unsubscribe` attempts both channels, then raises the first failure.
    - 3 tests added.
  - `[medium]` `[patch]` The 30-instrument cap counted only planned ids, while an id whose unsubscribe failed still holds a wire slot.
    - `CaptureStatus.lingering` (applied minus plan) is new.
    - `ControlService` refuses `start`, and shrinks `pin_top_liquid`'s slots, by the lingering count. 2 tests added.
  - `[medium]` `[patch]` `RedisControlChannel.listen` had no liveness check, so a half-open connection blocked silently forever. It now PINGs after 30 s idle and raises when no PONG arrives within the next window. Verified against a live Redis.
  - `[medium]` `[patch]` The dYdX loader accepted `nan`, `inf`, bools and negative values for `liquidity_min_oi_usd`, `non_config_retain_hours` and `retain_hours`. The integer cadences were truncated with `int()`. Now:
    - numbers are type-checked;
    - thresholds must be finite and >= 0;
    - cadences must be real integers greater than 0.
    - 7 cases added to the plan-store contract test.
  - `[low]` `[patch]` A reload applied a hand edit but published nothing until the next status tick (up to 1800 s). It now publishes the rows and tombstones at once. Test added.
  - `[low]` `[patch]` A status-publish failure after a saved and applied command raised out of `handle`. It was mislogged as a failed command and the tombstone was skipped. It is now ledgered at `collector.status_loop`. Test added.
  - `[low]` `[patch]` The status loop shared one `try` between the indexer refresh and the publish, so an indexer outage withheld every row. The rows now publish with the last good labels. Test added.
  - `[low]` `[patch]` A removed id kept its `_last_book_update_ns`, so a re-add reported a book time from before its removal. It is now popped on removal. Test added.
  - `[low]` `[patch]` `_listed` is fetched once per run, so a market listed after start stays pending until restart. This is now a `Known limit:` comment with its upgrade path (refetch and reconnect, because dYdX's WS client parses by its instrument list).
  - `[low]` `[patch]` Also added:
    - a `CaptureStatus.lingering` test;
    - `docs/DATABASE_SETUP.md` §4/§5 now name `platform/data/dydx_config.toml` and the plan's real keys (the stale `pinned` key is dropped).

### 2026-09-26 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 12 (high 0, medium 2, low 10)
- defer: 1 (high 0, medium 0, low 1)
- reject: 9 (high 0, medium 0, low 9)
- addressed_findings:
  - `[medium]` `[patch]` A forced book resync (an orderbook unsubscribe plus a subscribe) ran outside `_subscription_lock`. A `stop` landing between its two halves let the resync re-subscribe a removed id's book, with nothing tracking it.
    - `Collector._resync` now takes the lock. While a wire change holds it, the resync is queued to `_resync_pending` rather than stalling the sampler.
    - `_resync_wire` skips an id that is no longer collected.
    - dYdX's `_resync_book` goes through `_resync`.
    - Regression test `test_a_removal_during_a_resync_unsubscribes_after_it`, plus a queued-resync test. Both fail with the lock removed.
  - `[medium]` `[patch]` dYdX's `unsubscribe` tried both channels, so a failed orderbook half left the trades channel gone. A re-add then reused the "still applied" subscription, with trades silently missing, and the retry re-sent an already-ended channel.
    - `DydxClient.unsubscribe` is now all-or-nothing like `subscribe`: a failed orderbook unsubscribe re-subscribes trades, and a failed rollback is `collector.unsubscribe_rollback`.
    - 3 tests replace the old attempt-both test.
  - `[low]` `[patch]` A removed id kept its queued resync. `apply` now discards it from `_resync_pending`. Test added.
  - `[low]` `[patch]` `start` of a lingering id was refused although capture reuses its subscription, and `reload` skipped the lingering-slot check that `start` enforces.
    - `ControlService._lingering_over_cap(plan)` counts only lingering ids the plan does not collect again. `start` and `reload` both use it, and a refused reload is ledgered at `collector.config_reload` with the current plan kept.
    - 3 tests added.
  - `[low]` `[patch]` A pending row stayed `pending` for up to `liquidity_check_seconds` (1800 s) after capture's retry subscribed it. `StatusPublisher.loop` now polls every 30 s between refreshes and republishes when the collected or pending set changed. Test added; `DATA_DICTIONARY.md` updated.
  - `[low]` `[patch]` `pin_top_liquid` with nothing to pin still rewrote the plan file (dropping its comments). It now returns without saving. Test added.
  - `[low]` `[patch]` `TomlPlanStore.save` truncated the file before serializing, so a failed dump left it empty. It now serializes first.
  - `[low]` `[patch]` `classify_liquidity` let `float` accept `NaN`, `inf` and negative volumes: NaN read as illiquid, and inf won every pin. These are now unparseable and ledgered. Test added.
  - `[low]` `[patch]` A failed subscribe left the book time of a message booked during its await, so a pending row showed a `last_trade_ts`. It is now popped. Test added.
  - `[low]` `[patch]` Two `Known limit:` notes in `Collector._subscribe_one`, also summarized in `ARCHITECTURE.md`:
    - "applied" means the subscribe frames were sent, so an asynchronous venue rejection is not seen;
    - Bybit's and Hyperliquid's multi-topic subscribes are not all-or-nothing, which is harmless while their plans are static.

### 2026-09-26 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 1, medium 5, low 1)
- defer: 1 (high 0, medium 1, low 0)
- reject: 13 (high 0, medium 0, low 13)
- addressed_findings:
  - `[high]` `[patch]` A dYdX subscribe that failed to send left its topic in the Rust client's reconnect replay set with no reference held (`send_and_track_subscribe`: `mark_failure` plus `remove_reference`). After a reconnect replayed it, capture's retry sent a duplicate subscribe, which dYdX answers with no snapshot. Capture had counted the replayed snapshot as unplanned, so the id never got a real book.
    - `DydxClient` now tracks `_held` channels (the Rust reference, 0 or 1) and `_replayable` ones (a failed subscribe).
    - A replayable orderbook is re-subscribed (subscribe, unsubscribe, subscribe) to force a fresh snapshot.
    - Tests: `test_a_retried_subscribe_forces_a_fresh_snapshot_of_a_replayable_orderbook` and `test_a_fresh_snapshot_interrupted_by_a_failed_unsubscribe_is_forced_again`.
  - `[medium]` `[patch]` A `stop` of a pending id sent nothing, because `apply` unsubscribed applied ids only. A replayed topic, or the half that did send, stayed on the wire untracked, holding a slot, with its messages counted forever.
    - `apply` now also unsubscribes an id whose subscribe failed (it lingers until that succeeds).
    - `DydxClient._release` takes a reference before unsubscribing a replayable channel, because at reference 0 the Rust unsubscribe is a no-op.
    - 4 tests in `test_apply.py`, plus `test_unsubscribe_ends_a_channel_only_a_reconnect_replay_may_hold`.
  - `[medium]` `[patch]` When the subscribe rollback's `unsubscribe_trades` also failed, the Rust client restored the trades reference. Capture's retry then raised it to 2 without sending anything, and a later `stop` only lowered it to 1: the trades subscription leaked forever.
    - The rollback is gone: `subscribe` and `unsubscribe` are idempotent per channel, so a retry sends only what is missing.
    - `collector.subscribe_rollback` and `collector.unsubscribe_rollback` no longer exist.
  - `[medium]` `[patch]` A half-failed resync (unsubscribe sent, subscribe failed) followed by a removal leaked the orderbook topic: `unsubscribe_orderbook` at reference 0 returned `Ok` without sending. `resync_orderbook` now goes through the channel mirror, and the removal ends the replayable topic. Test: `test_a_half_failed_resync_is_completed_by_the_removal`.
  - `[medium]` `[patch]` Re-adding a lingering id whose failed unsubscribe had already ended a channel reused the subscription with that channel missing. When the rollback failed, the trades were silently missing. `_subscribe_added` now runs capture's `_subscribe_one` (a no-op for channels still held) before the resync, and a failure there leaves the id pending. The expectation in `test_re_adding_a_still_subscribed_id_cancels_the_retry_and_resyncs` was updated, and one test was added.
  - `[medium]` `[patch]` dYdX's `_apply_deltas` override had no "a book starts from a snapshot" guard. After `_resync` cleared a book (and, since this story, queued its wire half behind `_subscription_lock`), in-flight incremental deltas rebuilt a shallow book that was sampled. Because a book then existed, `_handle_missing_book` never ran the queued resync.
    - The guard from the core was added: the deltas are counted as `_deltas_before_snapshot_dropped` and reported each flush.
    - Test added. Five existing tests now start their book from a Clear, as on the wire.
  - `[low]` `[patch]` `collector:status` took `pending` from capture's plan mirror, which lists an added id only once `apply` reaches it. For the length of a throttled `apply` or `pin_top_liquid`, such a row published as collected. `status.pending_ids` now computes it as "the plan's ids not in capture's applied set". Test added.

## Design Notes

- **The one loader crosses capture→control on purpose.** The spine (AD-D17) names capture's config loader as the one loader returning `CollectionPlan`, but AD-D2's graph has only control→capture. `collector_core.config` is therefore whitelisted in `COMPOSITION_ROOTS` for `COLLECTION_CONTROL` and imports `collection_control.domain` only. Everything else in capture sees the plan as ids plus the `PlanDiff` Protocol.
- **Pending needs one additive key.** `bot_tui` stores the whole dict and renders only `id`/`liquid`, so `"pending": true` on unapplied rows needs no TUI change. Every other shape stays byte-identical. The user has confirmed that nothing is frozen until prod.
- **Only the plan hot-reloads.** Core thresholds are read once at start, as Bybit and Hyperliquid already are. The old `_apply_config` also swapped the capture config silently.
- **Known limits.**
  - The save is an in-place rewrite of a single-file bind mount, so a rename is impossible, and comments are lost. Upgrade path: mount `platform/data/` as a directory and write temp-then-rename.
  - Only dYdX has a live plan.
  - Per-instrument ticker data of an id whose unsubscribe failed is still archived until the retry succeeds, because the client does not tag message provenance.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -W default -p no:cacheprovider` -- expected: only the 3 baseline Redis failures.
- `cd platform && REDIS_URL=redis://127.0.0.1:16379 python3 -m pytest -o addopts="" --rootdir=. bots/tests -q` (with the two Makefile deselects) -- expected: all pass.
- ruff format/check and mypy on the changed modules -- expected: clean apart from the known pre-existing findings.


## Auto Run Result

Status: done

**Summary.** A second follow-up review pass on Story 25.4. A fresh Blind Hunter and Edge Case Hunter reviewed the whole `c9fab9c5d7..8b077e34fb` diff. Their main finding, checked against `crates/network/src/websocket/subscription.rs` and `crates/adapters/dydx/src/websocket/{client,handler}.rs`, is that the "all-or-nothing" dYdX wire semantics did not match the Rust client:
- a failed subscribe send stays in the reconnect replay set with no reference held;
- sends happen only on a reference change between 0 and 1;
- an unsubscribe at reference 0 is a silent no-op.

This pass replaces the rollback approach with per-channel mirroring in `DydxClient`, and closes dYdX's shallow-book-after-resync gap.

**Files changed in this pass:**
- `platform/dydx_collector/client.py`: `_held`/`_replayable` channel mirror; idempotent `subscribe`/`unsubscribe`/`resync_orderbook` through `_hold`/`_release`/`_send`; rollback and its two ledger sites removed.
- `platform/collector_core/collector.py`: `apply` also unsubscribes a removed id whose subscribe failed; a re-add of a lingering id re-subscribes before its resync; the `Known limit:` in `_subscribe_one` now requires per-channel idempotence instead of all-or-nothing.
- `platform/dydx_collector/collector.py`: `_apply_deltas` refuses to start a book from an incremental delta (counted).
- `platform/collection_control/application/status.py`: `pending_ids(plan, status)`, used by `status_messages` and the change poll.
- Tests:
  - `dydx_collector/tests/test_client.py`: the 5 rollback tests replaced by 8 convergence tests; its pre-existing mypy error fixed.
  - `collector_core/tests/test_apply.py`: +4 tests, 1 updated.
  - `dydx_collector/tests/test_collector_resilience.py`: +1 test, 5 start from a Clear.
  - `collection_control/tests/test_control.py`: +1 test.
- `platform/ARCHITECTURE.md`: the applied-set paragraph.
- `_bmad-output/implementation-artifacts/deferred-work.md`: one new entry.

**Review findings:** 21 after deduplication.
- 7 patches applied (high 1, medium 5, low 1). See this pass's Review Triage Log entry.
- 1 deferred: enabling `store_order_book_deltas` for an already-collected id starts its raw delta archive with no snapshot. Pre-existing in `_apply_config`.
- 13 rejected:
  - already deferred: the hand-edit overwrite race, and `start` of an unlisted id;
  - by spec or documented `Known limit:`: `prune_catalog` through the one loader; the in-place save truncation risk; Bybit/Hyperliquid re-add semantics (static plans);
  - verified harmless: cross-check, backfill-count and per-side delta-time state after a removal (a snapshot's Clear resets the per-side times); a `CancelledError` mid-`apply` (only at shutdown); lingering reads outside capture's lock; the refused-reload ledger repeating while the incident lasts (by design); integer-only Bybit cadences (every committed config is an integer); backfill of a just-removed id archiving trades from while it was collected.

**Verification:**
- Platform suite (`-W default`, the Verification command's list): 3 failed / 1750 passed / 61 warnings. The 3 failures are the known Redis `data_api` tests.
- HEAD run twice from an exported copy: 59 and 60 warnings. The changed tree's runs gave 56 and 61. Every warning type is the same GC-timed `unclosed database`/event-loop ResourceWarning set noted in the previous pass, so no new warning.
- Mutation check: each of 7 mutations is caught by at least one new or updated test:
  - capture: no release of pending ids; no re-subscribe on a lingering re-add;
  - status: pending from capture's own set;
  - client: no fresh-snapshot forcing; no replayable release; subscribe of a held channel;
  - dYdX: no snapshot guard.
- ruff format/check: clean on every changed file.
- mypy: `collection_control`, `collector_core/collector.py` and `dydx_collector/{client,collector}.py` show only the pre-existing `raw_log_flush_loop` arg-type error; `dydx_collector/tests/test_client.py` is now clean.

**Residual risks:**
- The client mirror assumes `DydxClient` is the only per-instrument subscriber on its WS client, which is true today. It also assumes dYdX answers a duplicate subscribe without a snapshot. The fix is correct either way, at the cost of two extra frames per retried orderbook.
- A duplicate trades subscribe after a replay is left to dYdX's harmless "already subscribed" error.
- None of this has been exercised against the live venue. Only the Rust source and fake clients were used.
- Follow-up review recommended: this pass replaced dYdX's wire state handling with a new per-channel state machine and changed capture's removal semantics.
