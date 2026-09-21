# Adversarial Review — `platform/` DDD Spine (2026-09-21)

**Target:** `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`
**Parent (binding):** `architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` AD-1..AD-11
**Lens:** construct two units one level down (strangler stories, order per AD-D12: observability → kernel → views → candles → alerting → research → archive → ranking → bots → collection_control → capture) that each obey every AD to the letter yet build incompatibly. Every pair is a hole; every hole gets a new or tightened AD.

**Code read:** `troll/collector_core/{collector,feed,trade_backfill,fold,config,second_snapshot,open_interest,archive_gaps,rebuild_seconds,consolidate_catalog,prune_catalog,compare_klines,nightly,build_candles,venue_http}.py`, `troll/dydx_collector/collector.py`, `troll/bybit_collector/collector.py`, `troll/hyperliquid_collector/client.py`, `troll/ranking_engine/engine.py`, `troll/ml_signals/{candle_store,candles,catalog_stats,venue}.py`, `troll/common/venues.py`, `troll/live_paper/{config,bot_status,trade_history}.py`, `troll/data_api/{alerts,live_candles,redis_bus,app}.py`, the three dockerfiles, `docker-compose.yml`, `Makefile`, `troll/CLAUDE.md`, `nautilus_trader/serialization/arrow/serializer.py`.

**Verdict:** the spine's context table is a faithful restatement of today's writer set, but it leaves four entities with two owners (the `_archive_gaps` marker, `ArchiveDay` state, the collected-instrument set, the seconds→bars fold) and one shared invariant (the `ts_init`↔`ts_event` skew every catalog reader widens by) scattered across four contexts with no kernel home. Each of those lets two obedient stories build incompatible code. Not shippable as a build substrate until the Critical and High items below are folded in as ADs.

---

## CRITICAL

### C1 — `_archive_gaps/<iid>.jsonl` has two writing contexts and no owner row; `ARRIVAL_MARGIN_NS` is a cross-context constant with no kernel home

**Evidence.** `collector_core/archive_gaps.py` is listed under `archive/` (AD-D1). Its docstring: "Only one collector writes a given instrument's file … and the maintenance tools write it under the maintenance lock" — two writers by design. `collector_core/collector.py:145` imports `ARRIVAL_MARGIN_NS` and `record_gap` from it; capture writes gaps at `_mark_lost_trades` (`:1104-1112`) and `_mark_quarantined_trades` (`:286-300`), and refuses backfilled trades older than `ARRIVAL_MARGIN_NS` (`:1639`). `rebuild_seconds.py:82-84` (`_TS_INIT_MARGIN_NS = ARRIVAL_MARGIN_NS`, `load_gaps`, `in_gap`) and `prune_catalog.py:53` (`_file_days` widening) consume the same constant. `trade_backfill.py` docstring pins the 5-minute window to the same value. AD-D2 has no `capture → archive` edge. AD-D4: "across contexts the only event transport is the existing Redis published language".

**Two obedient units.**
- *Capture story (11th)* obeys AD-D2 by not importing `archive`. It needs the refusal bound and a gap marker. Options that obey every AD: (a) define its own `ARRIVAL_MARGIN_NS` in `capture/domain/policies.py`; (b) emit `ArchiveGapRecorded` — but the only cross-context transport is Redis (AD-D4), and the nightly rebuild is a cron subprocess, not a subscriber.
- *Archive story (7th)* keeps `archive_gaps.py`, its 300 s constant and its `.jsonl` format as archive infrastructure, reading gaps written "by the collector" that, after (a)/(b), no longer arrive in that file.

**Incompatible result.** Either two constants (one raised by an operator after a long outage — the docstring's stated upgrade path — while the other stays 300 s: backfilled trades are accepted by capture but invisible to `rebuild_day`'s `ts_init` window, so the rebuild zeros those seconds' trade columns and `reconcile_day` fails on a day that is complete on disk), or the rebuild never learns of a `write_failed` gap and overwrites live values with zeros — the exact failure `_mark_lost_trades` exists to prevent (DATA-05).

**Fix (new AD-D17 + tighten AD-D3, AD-D1, AD-D4).**
- **AD-D17 — Archive markers are kernel-defined, capture-written, archive-read.**
  - *Binds:* `kernel/archive_markers.py`; `capture/`; `archive/`.
  - *Prevents:* two definitions of the arrival window; a gap the rebuild never sees; a Redis-only event bus that a cron subprocess cannot receive.
  - *Rule:* `kernel/archive_markers.py` holds `ARRIVAL_MARGIN_NS` and the pure `GapMarker` encode/decode of `<catalog>/_archive_gaps/<iid>.jsonl`. The venue's capture process is the only writer of that file for its instruments; archive tools read it under the maintenance lock and never append. A kernel test asserts every writer-side skew constant (`hold_back_seconds` + `_VENUE_AHEAD_NS`, `_MAX_CATCH_UP_SECONDS`, `stale_trade_seconds`, backfill refusal) is ≤ `ARRIVAL_MARGIN_NS`.
- **AD-D1 table:** add row `_archive_gaps/` — writer `capture`, reader `archive`.
- **AD-D4 tighten:** "across contexts the only event transports are the Redis published language **and the kernel-defined file markers under the catalog root (AD-D17), each with exactly one writing context**".

---

### C2 — `ArchiveDay` has no store; its `verified` state is owned by `candles/` and its `rebuilt` state by nobody

**Evidence.** AD-D9 makes `ArchiveDay(venue, instrument, day)` archive's aggregate with states provisional → rebuilt → verified/mismatched → released. AD-D1: archive writes "`verified_days` rows". AD-D8: those rows are written "through `candles.mark_verified`" into `candles_<venue>.db`, whose sole writer is `candles/`. Today `compare_klines.py:489` opens `candle_store.connect_rw(db_path)` itself (archive holding candles' rw connection, WAL pragmas and `_SCHEMA` creation), `prune_catalog.py:209-215` opens `connect_ro` and calls `verified_status` directly. `rebuild_seconds.py` records nothing durable: `rebuilt` exists only as a subprocess exit code inside `nightly.run_steps`. AD-D1 bans "a context reaching into another's aggregate state"; AD-D2 allows `archive → candles` for `mark_verified, rebuild_day` only (no `verified_status` query).

**Two obedient units.**
- *Candles story (4th)* builds `CandleStore` (repository, AD-D4-justified: "one writer, WAL, watermark") that owns the SQLite connection, `_SCHEMA` (incl. `verified_days`, frozen by AD-D12), and exposes `mark_verified` as its command and `window/latest/…` as queries.
- *Archive story (7th)* builds `ArchiveDay` with `DayStatus`, and — because AD-D1 forbids reaching into candles' state and AD-D2 offers no `verified_status` edge — persists its own `archive_days` table (or `.jsonl`) so `RetentionPolicy` can read `verified` and so `rebuilt` is recorded at all. It still calls `candles.mark_verified` for the frozen `verified_days` row.

**Incompatible result.** Two records of one day's verdict. `prune_catalog` (archive) reads archive's copy; `data_api`/`views` and any future kline-coverage view read candles' `verified_days`. A `reconcile_day` crash between the two writes leaves them disagreeing forever. Independently, `rebuilt` never being persisted means `reconcile_day` on a day whose `rebuild_day` step was skipped (saga stopped at consolidate, operator reran only `compare_klines`) can pass on provisional arrival-timed trade columns and gate a prune on it — the "reconciliation that passes by tolerance" AD-D7 exists to prevent, achieved by omission.

**Fix (tighten AD-D9, AD-D2, AD-D8).**
- **AD-D9 tighten:** "The only persisted `ArchiveDay` state is the `verified_days` row in `candles_<venue>.db`, written by `candles.mark_verified` on archive's command and read by archive through `candles.verified_status`. No second store of day status may exist. `rebuilt` is not persisted: `reconcile_day` runs only inside a saga run in which `rebuild_day` for the same (venue, day) returned success (`StepResult` carried in-process), and refuses — ledgered `reconcile.not_rebuilt` — when invoked standalone without `--rebuilt-by <run id>`. Archive never opens the candle store's file; the connection is candles'."
- **AD-D2 edge:** `archive → candles` = `mark_verified, verified_status, rebuild_day`.
- **AD-D8 add:** "`CandleStore` is the only code that opens `candles_<venue>.db` rw; `compare_klines`/`prune_catalog` receive a `VerifiedDays` port implemented by candles and injected by the nightly entrypoint."

---

### C3 — `capture/` and `collection_control/` both own the collected-instrument set, the per-instrument book state, and a catalog pruner

**Evidence.** `dydx_collector/collector.py:380-399` `_apply_config` diffs plan ids, calls `_subscribe`/`_unsubscribe` (capture's `VenueFeed`), sets `_delta_store`/`_delta_retain_hours`, then `save_config`; `_clear_book_state` (`:268`) is invoked from the unsubscribe path and wipes `_live_books`, `_level_msg_id` — `LiveBook` state. `_instrument_ids()` (`:265`) returns the *plan* (`self._config.instruments`), and that is what `_sample_tick` (`collector_core/collector.py:1187`) iterates and what `_drop_unsampled_trades` (`:1298`) uses, so the gate's universe is the plan, not the subscribed set. `_prune_loop` (`:595-612`) calls `collector_core.prune_catalog.prune_instrument` for dropped instruments (`non_config_retain_hours`) and per-instrument delta retention — a second catalog pruner. AD-D1 lists control as writing "`config.toml`; `collector:status`" only; AD-D9 makes `prune` archive's with a verification-gated `RetentionPolicy`; AD-D2 allows `control → capture` "application API only".

**Two obedient units.**
- *Control story (10th)* builds `CollectionPlan` (pins, exclude, `store_order_book_deltas`, `retain_hours`) that emits `InstrumentAdded/Removed` to capture's application API and publishes `collector:status` from the plan. It keeps `_prune_loop` because AD-D1's row does not forbid it and archive's `RetentionPolicy` (verified-gated, `trade_tick`-only semantics) has no "dropped instrument after N hours" or "delta retention per instrument" case.
- *Capture story (11th)* builds `FeedGroup` owning subscribe/unsubscribe state and `SecondSampler` sampling every `LiveBook` that exists (the natural domain reading of AD-D6: "one per instrument").

**Incompatible result.** (1) Two universes: an `unsubscribe` that fails on the wire (dYdX 2/s throttle, reconnect in flight) leaves the instrument in `FeedGroup`/`LiveBook` — sampled and archived by capture, absent from the plan, reported "removed" on `collector:status`, and its `trade_tick/` leaf grows with no retention (MEM-02) because archive's prune only touches plan instruments. Conversely a plan `add` whose subscribe never completes is reported live. (2) Two pruners on one catalog: control's hours-based ungated `prune_instrument` and archive's `RetentionPolicy`, each unaware of the other's lock (`maintenance_lock` is archive-only) — a consolidate and a control prune on the same leaf at once.

**Fix (new AD-D18; tighten AD-D9).**
- **AD-D18 — The sampled set is the applied plan, and capture reports application.**
  - *Binds:* `collection_control/`, `capture/application`, `collector:status`.
  - *Prevents:* the plan and the feed disagreeing silently; a subscribed instrument no context owns; an unsubscribed one still archived.
  - *Rule:* `CollectionPlan` is the intent; `FeedGroup.applied` is the fact. `CaptureService.apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)`; the sampler iterates `applied ∩ plan`; an instrument in `failed` is `pending` on `collector:status` and ledgered (`collector.subscribe_failed`) once per attempt, retried by capture, never assumed by control. `LiveBook` is created only for an applied instrument and disposed on `Unsubscribed`; an unsolicited message for a non-applied instrument is counted (`collector.unplanned_message`), not booked.
- **AD-D9 tighten:** "`archive.RetentionPolicy` is the only code that deletes a catalog file, for every reason: verified-and-aged `trade_tick/`, dropped-instrument retention (`non_config_retain_hours`), and per-instrument `order_book_deltas` retention. Control expresses retention as plan attributes archive's nightly reads; no venue package or control loop holds a prune loop."

---

### C4 — Seconds→bars has three folds today and the migration order manufactures a fourth; `alerting` depends on `views` through an edge the graph forbids

**Evidence.** Folds of seconds into bars: `ml_signals/candle_store.py:fold_arrays` (candles/), `ml_signals/candles.py:aggregate_ohlc`/`candle_dicts_from_snapshots` (candles/ per AD-D1, but every caller is views: `data_api/live_candles.py:45,220`, `data_api/routes/rankings.py:38,209`, `data_api/routes/candles.py:43-44`), and `candle_store.py:12` itself admits the two are kept "matching" by hand. `data_api/app.py:98` wires `alerts.engine.on_snapshot` as an observer of `live_candles.live_candle_bus` — `alerting` consumes `views`' forming-candle output; AD-D2's graph has `API → AL`, `API → V`, `AL → K`, `AL → OBS` and **no** `AL → V`. AD-D8 says `fold_arrays` is "this context's own fold" — not the only one. AD-D2 lets views call candles' "query services (`window`, `latest`, `history`, `nearest`)" — a fold over caller-supplied rows is not on that list.

**Two obedient units.**
- *Views story (3rd — before candles)* moves `live_candles.py`, `routes/*` reads. It may import unmoved `ml_signals.candles` (Deferred exemption). When candles moves (4th) and its shim dies (≤ 2 stories, AD-D12), views cannot import `candles.candle_dicts_from_snapshots` (not a listed query service) → the views story, obeying AD-D11 ("one function in views"), keeps its own forming-bar fold in `views/chart_series.py`.
- *Alerting story (5th)* obeys AD-D2 (no `AL → V`): `AlertEngine` subscribes to `snapshots:raw` itself and folds the bar close it evaluates on (`evaluate(alert, state, price, ts_ns)` needs a bar's close at `bar_seconds`) — a fold of its own.

**Incompatible result.** Four implementations of "which second closes which bar, what is `partial`, is a no-trade second an empty bar" (`PARTIAL_OBSERVED_FRACTION` lives in `ml_signals/candles.py`, `candle_store._candle` reads it, views' forming bar computes `partial` separately): the chart's forming candle, the stored candle, the ranking sparkline and the alert trigger can disagree on the same instant — SSOT-02 violated by construction.

**Fix (tighten AD-D8, AD-D2).**
- **AD-D8 tighten:** "Exactly two folds exist in `platform/`: `kernel.fold.fold_trades` (trades → second) and `candles.domain.fold_arrays` (seconds → bars of any width, closed or forming). `ml_signals/candles.py`'s `aggregate_ohlc`, `candle_dicts_from_snapshots`, `build_candles` and `PARTIAL_OBSERVED_FRACTION` are retired in the candles story; the forming bar is `candles.application.forming_bar(rows: Sequence[SecondOHLC], bar_seconds) -> Bar | None` — a query service in AD-D2's sense — and `SecondOHLC` (the seven-field second row) moves to `kernel/second_snapshot.py`. The candles story precedes the views story in AD-D12's order."
- **AD-D2 tighten:** views' allowed candles calls: `window, latest, history, nearest, forming_bar, verified_status`. Add: "A cross-context in-process observer (today `LiveCandleBus.observers`) is legal only when the observed context declares it as a port (`views.ports.BarObserver`) and the *interface adapter's composition root* (`data_api/app.py`) does the wiring; the observer's input type is a kernel type. `alerting` implements `BarObserver`; no `alerting → views` import exists."

---

## HIGH

### H1 — The catalog file-span skew bound is four different numbers in four contexts

**Evidence.** A catalog file's name is its batch's `ts_init` span (`_timestamps_to_filename`, used by `consolidate_catalog.py:74`); rows' `ts_event` may precede the file's first stamp. Readers widen by their own constant: `ml_signals/catalog_stats.py:96` `_FILE_MARGIN_NS = 60 s` (views/research); `archive_gaps.ARRIVAL_MARGIN_NS = 300 s` (archive: `rebuild_seconds`, `prune._file_days`); capture's `_MAX_CATCH_UP_SECONDS = 30` whose comment (`collector.py:218-223`) says "Kept well under `catalog_stats._FILE_MARGIN_NS` (60 s) … readers only widen file spans by that margin"; `hold_back_seconds` (2.5 s HL) + `_VENUE_AHEAD_NS` (5 s); `_TRADE_CARRY_NS` = 5 s for `write_data`'s non-overlap. `_stamp_to_ns` is a private `catalog_stats` symbol imported by five archive modules. AD-D3 moves `_stamp_to_ns` to kernel; the bound is unmentioned.

**Two obedient units.** *Capture story* raises `_MAX_CATCH_UP_SECONDS` to 120 after a host-suspend incident (AD-D5/D6 satisfied; the "upgrade path" comment invites it). *Views story* keeps `_FILE_MARGIN_NS = 60 s` (AD-D11 satisfied).

**Incompatible result.** A caught-up row's `ts_event` sits 120 s before its file's first stamp; `query_second_ohlc` (`catalog_stats.py:112`) skips the file → chart gap; `build_candles._files_by_day` misses the rows → `rebuild_day` reports `not_covered`, `reconcile_day` mismatches, the day never verifies, `trade_tick/` never prunes — for a day that is complete on disk.

**Fix (tighten AD-D3).** "`kernel/clocks.py` holds `CatalogFileSpan` (stem parse — the former `_stamp_to_ns` — and `covers(ts_event)`) and the single `MAX_TS_INIT_SKEW_NS`: the largest `ts_init − ts_event` any writer may produce for a row and the only margin any reader widens a file span by. `_FILE_MARGIN_NS`, `_TS_INIT_MARGIN_NS`, `_MAX_CATCH_UP_SECONDS`-derived and `hold_back`-derived bounds are all expressed as ≤ this constant, asserted by `kernel/tests`." (`ARRIVAL_MARGIN_NS` of C1 is this constant.)

### H2 — No rule gives a catalog leaf exactly one writer process; `write_data`'s `ts_init` non-overlap turns a second writer into silent data loss

**Evidence.** `collector.py:207-211`: "`write_data` refuses a file whose `[first, last]` `ts_init` interval touches an existing one — the next flush would lose its whole batch". `quarantine_corrupt_parquet`'s docstring (`:245-258`) states "Only this process writes its own ids' directories" — a docstring invariant. `repair_catalog.py:77` calls `write_data` (archive, per parent AD-6). Consolidate/prune take `maintenance_lock`; capture takes no lock. The parent's "one producer per (channel, venue)" covers Redis only.

**Two obedient units.** *Archive story* ships `repair_catalog` as an archive application service under AD-6/AD-D9 (offline, official API). *Capture story* ships `ArchiveWriter`. Nothing prevents an operator running `repair_catalog` on an instrument while its venue's capture is up (today: convention only).

**Incompatible result.** Repair writes a file into an open-day leaf; capture's next flush hits the overlap → `collector.flush_write … LOST` and, for trades, an archive gap; each hour until restart. Same shape if two capture processes ever share a leaf (a Bybit spot/linear service split, or the planned dYdX connection sharding).

**Fix (tighten AD-D9; new consistency row).** "A leaf `data/<type>/<iid>/` has one writer process at a time: the venue's capture process for the current UTC day (it holds `<catalog>/.capture-<venue>.lock` for its whole run), archive tools for closed days under `maintenance_lock`. No archive tool writes a file whose `ts_init` span intersects the current UTC day; `repair_catalog` refuses (`repair.capture_running`) while the venue's capture lock is held. A second capture process for a venue partitions instruments and never shares a leaf."

### H3 — Dockerfile `COPY` closure drift is already live, and the migration's "every caller chases the shim" rule guarantees more

**Evidence.** `troll/data_api.dockerfile:50-53` still copies only `dydx_collector`, `ml_signals`, `ranking_engine`, `data_api` — the parent's Deferred item ("does not ship the packages its code imports": `collector_core`, `common`) is unfixed today. `troll/live_paper.dockerfile:76-77` copies `ml_signals` + `live_paper`; `live_paper/config.py:62` imports `ml_signals.venue`, `strategy.py` imports `ml_signals.indicators`, `trade_history.py:48` `ml_signals.performance_metrics`. AD-D12: a move "updates … the dockerfile `COPY` lines … in the same commit", and the shim's `DeprecationWarning` "makes every caller chase it" — so the kernel story (2nd) edits `live_paper/config.py` to `from kernel.venues import venue_of`. The Deferred says unmoved packages are exempt from the boundary test.

**Two obedient units.** *Kernel story* adds `COPY troll/kernel ./kernel` to `collector.dockerfile` and `data_api.dockerfile` (the images whose `make test` runs), chases the shim into `live_paper/config.py`, and — since `live_paper` is exempt/unmoved and `make test` does not run `live_paper/tests` — omits `live_paper.dockerfile`. *Bots story (9th)* inherits an image that has failed to start since story 2.

**Incompatible result.** `live-paper` (profile-gated, not in `make test`, `restart: on-failure:5`) crash-loops on its next deploy with `ModuleNotFoundError: kernel`. The same mechanism already produced the `data_api` gap in story 22.3 and it went unnoticed for weeks.

**Fix (tighten AD-D12).** "`platform/tests/test_images.py` walks `ast` imports from every `command:` entrypoint in `docker-compose.yml` (and every `-m` module in `Makefile`/cron), computes the top-level-package closure, and asserts each package appears in that service's dockerfile `COPY` set; a shim counts as its target. It runs in `make test` and is a required check of every migration story. The existing `data_api` gap is closed in the observability story (first)."

### H4 — SSOT-02's rolling metrics are computed by a formula the spine assigns to `views`/`research`, imported by `ranking`

**Evidence.** `ranking_engine/price_series.py:179-182` computes `price/pct_1h/pct_24h/volatility` via `catalog_stats.price_stats_from_series`; `engine.py:808` backfills through `catalog_stats.price_series` (a catalog read); `ml_signals/metrics_computer.py:57-65` (assigned to `ranking/`) computes the same `pct_1h/volatility` through `catalog_stats.price_stats` — a second computer already. AD-D1 splits `catalog_stats` into "query half → research" and "series reads → views"; `ranking → views/research` is not an AD-D2 edge; `build_candles` (candles) uses `data_file_ranges`/`second_ohlc_arrays`; `rebuild_seconds` (archive) imports `build_candles._files_by_day` (archive → candles internals).

**Two obedient units.** *Views story (3rd)* moves `price_stats_from_series`/`price_series` to `views/chart_series.py` (it is a "series read"). *Ranking story (8th)*, barred from importing views, re-implements the formula in `ranking/domain/price_series.py` (AD-D10: "one aggregate root").

**Incompatible result.** Two `pct_1h`/`volatility` formulas (ranking's live one feeding `rankings:live` + `metrics.db`, views'/research's catalog one feeding `/api/metrics` and backtests) that drift on the first edit — the precise SSOT-02 failure. And the catalog-read helpers every context needs (`data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc`, `_files_by_day`) have no importable home: kernel bans I/O, views is a leaf.

**Fix (tighten AD-D1, AD-D3).** "(1) `price_stats_from_series`, `price_series`, `PriceSeriesStore` and `metrics_computer` are `ranking/domain` — the sole computer of pct/volatility; `views` and `research` read those values from `rankings:live`/`metrics.db`, never recompute. (2) AD-D3 admits a second I/O helper, the read twin of `venue_http`: `kernel/catalog_files.py` — pure file-span/leaf listing and column-projected Parquet reads over the catalog root (`data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc`, `files_by_day`), no writes, no catalog object construction. `rebuild_seconds` stops importing `build_candles` internals."

### H5 — Policies live outside `domain/` and may ledger; domain aggregates may not — two ledger sites per event

**Evidence.** AD-D2 lets only `application/` import `observability/`; `domain/` may not. AD-D6 requires `FeedGroup` "abandonment ledgering", `TradeIntake` counters "none silent", and places policies in `capture/venues/<v>/policies.py` — not under `domain/`, so AD-D2's domain rule does not bind them. Today `bybit_collector/collector.py:116-130` ledgers `collector.book_sequence` inside `_apply_deltas` (the future `SequenceCanary`), `collector.py:869` ledgers `collector.pending_deltas` inside the overflow check, `_check_impossible_ohlc` logs at ERROR from inside the sampler.

**Two obedient units.** *Capture domain story* makes `LiveBook.apply` return `SequenceBroken` (AD-D5's allowed per-transition event) which `CaptureService` ledgers. *Bybit venue story* ports `SequenceCanary` as a policy value that calls `error_ledger.record("collector.book_sequence", …)` directly — legal, since `venues/` is not `domain/`.

**Incompatible result.** One sequence break → two ledger entries under two sites (or one site with double count); `GET /api/errors` and the flush-time "Trade feed arbitration"/sequence summaries disagree; a fourth venue copies whichever it read first.

**Fix (tighten AD-D6, AD-D2).** "A policy value (`CrossedBookPolicy`, `LevelTagger`, `SequenceCanary`, `BookTimeSource`, `BackfillCapability`) is domain code wherever its file lives: pure, synchronous, returns a verdict or event, never logs, ledgers or awaits. `capture/venues/<v>/policies.py` is bound by AD-D2's `domain/` import rule (the boundary test treats it as `domain/`). `CaptureService` is the only ledger caller in capture: one site per event type, listed in `capture/application/sites.py`."

### H6 — The boundary test's unmoved-package exemption lets a moved context import an unmoved package's *internals*, which the later move must then preserve or break

**Evidence.** Deferred: "`test_boundaries.py` enforces edges only for packages already moved; the old packages are exempt until their story lands". `views` (3rd) needs `ml_signals.candle_store._fold`/`candle_store.window` and `catalog_stats._stamp_to_ns`; `archive` (7th) needs `build_candles._files_by_day`, `catalog_stats._stamp_to_ns` — all private today.

**Two obedient units.** *Views story* imports `ml_signals.candle_store._fold` (target exempt → no edge → test passes). *Candles story* (4th) renames `_fold` into `CandleSeries.apply` (AD-D8 says one fold, and a private helper is not a contract).

**Incompatible result.** Views breaks at story 4, or candles keeps a private-name shim forever (AD-D12 forbids), or the boundary test is loosened again.

**Fix (tighten AD-D2, retire the Deferred).** "`platform/tests/test_boundaries.py` ships in the observability story with a static `LEGACY_MODULE_TO_CONTEXT` map (every `troll/` module → its AD-D1 row). An import from a moved context into an unmoved module is checked as an edge to that module's *future* context, and an import of a `_private` name across any two rows fails from day one. The graph binds from story 1; only edges *within* an unmoved package are exempt."

---

## MEDIUM

### M1 — `views/` writes two durable stores while its row says "nothing durable"
`data_api/routes/rankings.py:168` (via `ml_signals/screener_columns_config.py:69`) and `ml_signals/chart_indicator_config.py:76-77` write `screener_columns.toml` / `chart_indicators.toml` with `tomli_w` full rewrites; both are mounted `:rw` into `data_api` (`docker-compose.yml:14,170`). *Views story* keeps them (violates AD-D1) or drops them (feature loss); *a later story* adds a `preferences/` context nobody decided. **Fix (AD-D1 table):** views row → Writes: `screener_columns.toml`, `chart_indicators.toml` (UI preference stores, one loader each, full rewrite; no data-integrity content). Add both files to AD-D12's frozen list.

### M2 — Two outbound operator-notification transports
`collector.py:388` `_notify` → ntfy via `WATCHDOG_NTFY_URL` (observability per AD-D16); `data_api/alerts.py:199-245` `post_webhook`/`post_telegram` via `TELEGRAM_*` (alerting per AD-D1). *Observability story* builds `notify()` on ntfy; *alerting story* builds `deliver()` on Telegram; both legal. Result: an operator who configures Telegram still gets watchdog pages nowhere (ntfy unset → CRITICAL log only), and a fourth transport arrives with bots (`bots:incidents`). **Fix (AD-D16):** "`observability.notify(channel, title, body)` is the one outbound transport (ntfy, Telegram, generic webhook as its adapters, chosen by env); `alerting.deliver` and the capture watchdog call it. An `Alert` names a channel, never a transport."

### M3 — `bots:incidents:{bot_id}` is a live key outside the freeze
`live_paper/bot_status.py:33,78` writes it; `bot_tui/bot_incidents_state.py:92` GETs it. Not in AD-D12's frozen list nor the parent channel table. *Bots story* reshapes it under AD-D15 (legal); *bot_tui* (unmoved) logs "payload not a list, ignoring". **Fix:** add `bots:incidents:*` (JSON list, GET-only) to AD-D12 and to the parent's channel row as an amendment.

### M4 — A third `InstrumentId` suffix parser survives the kernel merge
`venue_http.bybit_category` (`-LINEAR.BYBIT`/`-SPOT.BYBIT` → category, `ValueError` otherwise) alongside `common.venues.market_kind` (`PERP|LINEAR|INVERSE` → perp) and `ml_signals.venue.venue_of`. AD-D3 merges two of three. A Bybit `INVERSE` id is `perp` to one and an error to the other; `trade_backfill.fetch_trades` dispatches on `instrument.id.venue.value`, a fourth way. **Fix (AD-D3):** "`kernel/venues.py` is the only module that parses an `InstrumentId` string; `bybit_category` becomes `venues.bybit_category`, defined over `market_kind`, and every venue dispatch uses `venues.venue_of`."

### M5 — Ranking's own venue URL maps and User-Agent duplicate `kernel.venue_http`
`ranking_engine/engine.py:205-222` (`_USER_AGENT`, `_BYBIT_URLS`, `_HYPERLIQUID_URLS`, own timeouts, own dYdX `get_dydx_http_url` call). AD-D3 admits `venue_http` "because `capture` and `archive` must issue byte-identical requests" — ranking is not bound. *Kernel story* corrects `HYPERLIQUID_URLS["testnet"]`; *ranking story* keeps its map: `HYPERLIQUID_ENVIRONMENT=testnet` now hits two hosts. **Fix (AD-D3):** "Every stdlib REST request to a venue, in any context, is built through `kernel.venue_http` (URL map, UA, timeout, `HttpJson`); a literal venue URL outside kernel is a boundary-test failure."

### M6 — `SecondSampler` is declared pure but the crossed-book/missing-book paths do I/O and mutate `LiveBook` from inside a policy
`collector.py:1207-1214`: `_sample_instrument` awaits `_handle_missing_book → _resync` (client I/O) and `_handle_crossed_book`; dYdX's `uncross.handle_crossed_book` (`uncross.py:285-320`) takes a `resync` coroutine and the ladder calls it (`:282`), and `_resync_book` (`dydx_collector/collector.py:346-351`) calls `_clear_book_state`. *Capture domain story* makes `sample()` a pure function returning `ResyncRequested`; *dYdX venue story* ports the ladder as an async `CrossedBookPolicy` that resyncs. **Fix (AD-D6):** "`CrossedBookPolicy.step(book, tags, now_ns) -> Uncrossed | StillCrossed(since) | ResyncRequested` is synchronous and pure; the escalation timer state is `LiveBook.crossed_since`; `CaptureService` executes `ResyncRequested` after the sample through `VenueFeed.resync_orderbook` and then `LiveBook.resync()`. `_handle_missing_book`'s resync follows the same path."

### M7 — A shim that is a copy, not a re-export, registers a second Arrow class and breaks identity checks silently
`register_arrow` keys `_ARROW_ENCODERS/_SCHEMAS` by class object (`serializer.py:75-78,122-128`); `second_snapshot.py:174` and `open_interest.py:101` call it at import. `collector.py` dispatches on identity (`key[0] is DydxSecondSnapshot`, `key[0] is TradeTick`) for the trade carry and the candle-store apply. AD-D12 says "re-export shim" but does not forbid a copy or a second registration. *Kernel story* leaves the old class body in `collector_core/second_snapshot.py` "for one release" with a warning; *capture story* imports from kernel. Result: two registered classes named `DydxSecondSnapshot`; a venue package still on the old path produces objects the core's `is` checks skip — no carry, no candle apply, no error. **Fix (AD-D12):** "A shim is exactly `from <new> import <names>` plus `warnings.warn`; it defines nothing. `test_namespace.py` asserts `old.X is new.X` for every shim name and that `_SCHEMAS` holds exactly one key per kernel class `__name__`."

### M8 — `snapshots:raw` is parsed two ways
`live_candles.py:183` decodes via `DydxSecondSnapshot.from_dict` (tolerant: `values.get("buy_count") or 0`); `ranking_engine/engine.py:566-580` hand-indexes `snap["bid_prices"]` (strict) and re-validates `close_price` finiteness (AD-3 says readers don't); `bot_tui` hand-indexes too. A kernel story that adds an optional field is fine for both, but one that renames `buy_count` (frozen — fine) or a producer bug that drops a key is "0" to one reader and "malformed, SKIPPED" to another. **Fix (AD-D12/AD-D3):** "`DydxSecondSnapshot.from_dict` is the only parser of a `snapshots:raw` entry in every consumer; indexing the raw dict is a boundary-test failure (grep for `snap["` outside kernel)."

### M9 — One `config.toml` per venue has two schema owners
`CoreConfig` fields (`hold_back_seconds`, `stale_book_seconds`, `trade_feeds`, `book_time_source`; `collector_core/config.py:34-63`) and plan fields (`instruments`, `exclude`, `store_order_book_deltas`, `retain_hours`) share one file that control rewrites in full (`dydx_collector/config.py:139-140`) and capture hot-reloads with a loader that "rejects unknown keys". *Control story* adds a plan key (`pinned_at`); *capture story*'s loader (moving later) rejects the file control just wrote → the venue refuses its own config at the next reload. **Fix (AD-D1/AD-D12):** "The venue `config.toml` is one file with one loader, `capture/infrastructure/config.py`, which returns `(CoreConfig, CollectionPlan)`; control validates through that loader before `save`, and the file's key set is frozen by AD-D12 until the plan moves to `plan.toml` in the control story."

---

## LOW

### L1 — The generic observability subdomain would own a dYdX regex
`dydx_collector/collector.py:669` `_IID_RE = r"([A-Z0-9]+-USD-PERP\.DYDX)"` inside the incident handler AD-D16 moves to `observability/`; Bybit/HL incidents classify as instrument `-`. **Fix (AD-D16):** the handler takes `iid_pattern` from the venue entrypoint; observability holds no venue token.

### L2 — AD-D5's baseline is recorded last, after ten stories have touched the hot path's imports
`error_ledger.record` (observability, 1st) is called from `_on_replayed_trade` for every replayed trade (dYdX replays up to 1000 per subscribe) and from `_process_data`'s except; a structured-record refactor in story 1 changes per-message allocation before the baseline exists. **Fix (AD-D5):** the `tracemalloc` baseline is recorded in the observability story against today's `collector_core`, stored under `platform/tests/fixtures/hotpath_baseline.json`, and every story's CI runs the comparison.

### L3 — `SecondOHLC` is a second "second row" shape outside the kernel
`ml_signals/catalog_stats.py:81` duck-types `DydxSecondSnapshot` for the fold and is what `candle_store._fold`, `live_candles` and `build_candles` actually pass around. Covered by C4's fix (move to `kernel/second_snapshot.py`); listed so the kernel story does not miss it.

### L4 — `_watchdog_transition` is capture's feed-liveness state machine, placed in the generic subdomain
AD-D16 moves `_watchdog_transition` (`collector.py:353`) to observability; AD-D14 names `FeedLiveness` a capture term; `live_paper/bot_status.py:135` keeps its own OBS-01 proxy. Two staleness state machines with the same 30 s doctrine. **Fix:** `observability/watchdog.py` holds only the generic `(down_since, reminder)` transition over a boolean; the "what counts as silent" verdict is capture's `FeedLiveness` and bots' `HeartbeatStaleness`, both feeding it.

### L5 — `research → data_api` over HTTP is an edge the boundary test cannot see
`ml_signals/watchlist.py` fetches the watchlist from `data_api`; AD-D2 lists it. Harmless, but the test should assert research imports no `data_api` symbol (only `urllib`/`httpx`), else the HTTP edge silently becomes an import edge.

---

## Cross-check of the seed pairs the reviewer was asked to construct

| Seed pair | Verdict |
| --- | --- |
| `candles/` vs `archive/` both writing `verified_days` | Real — C2 |
| `capture/` vs `collection_control/` owning subscribe/unsubscribe state | Real — C3 |
| `views/` vs `ranking/` computing `pct_1h`/`volatility` | Real, via `catalog_stats` ownership — H4 |
| `kernel/` vs `observability/` on `_stamp_to_ns` | The symbol goes to kernel (AD-D3) cleanly; the *bound* it serves does not — H1 |
| Two venue `BackfillCapability`/`BookTimeSource` semantics | `Fetched`'s contract (oldest-first, `reached_since`) is docstring-only but one shape; port contract tests (Consistency Conventions) cover it. `BookTimeSource` is one `CoreConfig` enum. Not a hole on its own; policy purity is — H5/M6 |
| Dockerfile `COPY` drift | Already live, and the shim-chase rule guarantees recurrence — H3 |
| `register_arrow` import-time side effect under old+new paths | Safe for a true re-export; a copy is silent breakage — M7 |
| `snapshots:raw` dict shape after the kernel move | Frozen; two parsers — M8 |
| `capture` vs `archive` both writing `trade_tick/` (backfill append vs rebuild rewrite) | Rebuild rewrites snapshot files only; consolidate merges closed-day `trade_tick` files under lock; backfill appends with `ts_init=now` and is refused past `ARRIVAL_MARGIN_NS`. Consistent today *only because* one constant couples them (C1/H1) and one process per leaf is a docstring (H2) |
| AD-D5 vs per-second events at 30×3 | ~100 small frozen dataclasses/s is noise against the delta stream; the risk is the baseline's timing — L2 |
| Boundary-test exemption letting a moved package import unmoved internals | Real — H6 |
