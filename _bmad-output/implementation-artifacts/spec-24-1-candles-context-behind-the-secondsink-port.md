---
title: 'Story 24.1 — `candles/` context behind capture''s `SecondSink` port'
type: 'refactor'
created: '2026-09-25'
status: 'done'
baseline_revision: '7cd2f91aa24d5e6ad50946512c45b61cbed2d777'
final_revision: '93913b9e5609a9ad27a4b4440b055d2ada69bc13'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-24-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/24-1-candles-context-behind-the-secondsink-port.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The candle store is owned by nobody: `collector_core/collector.py` (capture) opens
`candles_<venue>.db` itself and calls `ml_signals.candle_store` directly, three independent
seconds→bars folds disagree (`candle_store.fold_arrays`, `ml_signals.candles.aggregate_ohlc`, and
`data_api/live_candles.py`'s forming bar), and two archive tools open the same SQLite file
themselves — so a bar can lead the archive, a chart's forming candle can disagree with the stored
one, and the parent spine's "writer→reader imports contradict AD-4" defect stays open.

**Approach:** Extract a `platform/candles/` bounded context in the spine's three-layer shape
(`domain/`, `application/`, `infrastructure/`), have capture feed it through a new `SecondSink`
port declared in `collector_core/ports.py` and injected by each venue entrypoint, collapse the
platform to exactly two folds (`kernel.fold.fold_trades` and `candles.domain.fold.fold_arrays`),
and give the archive tools a `VerifiedDays` port instead of a database connection.

## Boundaries & Constraints

**Always:**
- The `candles` SQLite `_SCHEMA` and `_UPSERT` text stay byte-identical (AD-D12 freeze); assert the
  schema against a recorded copy in a test.
- The `CANDLES_DB_PATH` env var, its default (`<catalog>/../candles/candles.db`), the
  `candles_<venue>.db` filename formula, the compose service names and the `platform/data/` mounts
  are unchanged.
- The `/ws/live` candle payload (`{"channel": ..., "bar": {t,o,h,l,c,v}}`), `/api/candles`'s
  `CandleItem` fields and the `nightly` summary line stay byte-identical.
- `SecondSink.apply` is called only with rows whose `ParquetDataCatalog.write_data` succeeded, and
  the catch-up completes before any live apply — the store is never ahead of the archive.
- Every moved import path becomes a pure re-export shim (`REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"`),
  defining nothing, with every in-repo caller repointed in the same commit.
- `domain/` imports only stdlib, `numpy`, `kernel` and `nautilus_trader.model`/`core`; no I/O,
  asyncio or SQLite. `application/` holds the `Protocol` ports and loops. `infrastructure/` is
  imported only by a composition root. No module-level mutable runtime state.
- Every aggregate and port docstring names the invariant it protects (DESIGN-01).
- `platform/CLAUDE.md` DATA-01..08 (notably DATA-07: no silent skip — every continue-past-failure
  calls `observability.error_ledger.record`), TEST-01..04, READ-03, SSOT-01..05, MEM-01..03,
  NAUT-01..03, FORK-01.
- Working dir `platform/`; a new `DeprecationWarning` in the test run is a failure (TEST-04).

**Block If:**
- The frozen `_SCHEMA`/`_UPSERT` text or a frozen wire payload cannot be preserved without a
  contract change.
- `make test` shows a regression that is not one of the pre-existing failures recorded during
  Epic 23 (dydx `trade_ohlc` ×5, `ofi_strategy` ×4, rankings redis ×1).

**Never:**
- Touch `nautilus_trader/` or `crates/` (FORK-01).
- Write `sprint-status.yaml`.
- Leave a third seconds→bars fold anywhere in `platform/`.
- Let `candles` import any context other than `kernel`/`observability` (no `candles` →
  `collector_core`/`data_api` edge; the port is satisfied structurally, never by inheritance).
- Widen `test_boundaries.py`'s `_exempt` or add a blanket context exemption — the composition-root
  whitelist names each module **and** the single extra context it may reach.
- Delete or loosen a failing test to make the suite pass.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Flush applies only written rows | `_flush_once` where `write_data` raises for instrument A and succeeds for B | `sink.apply` called for B only; A's rows never reach the store | A's failure already ledgered by `collector.flush_write` |
| Sink failure is loud, not fatal | `sink.apply` raises for one instrument | Remaining instruments still applied; ingestion continues | `error_ledger.record("collector.candle_store", ...)` |
| Exactly-once | Same second batch applied twice | Second call applies 0 rows; `v`/`seconds_observed` unchanged | None |
| Catch-up bail-out | Watermark older than 24 h | Instrument skipped with the existing "run build_candles" warning | Warning, no store write |
| No sink injected | `Collector(..., second_sink=None)` | Apply and catch-up are no-ops; collector runs | None (tests only; all three entrypoints inject one) |
| `forming_bar`, no trade | Buffer whose every row has `close_price is None` | Returns `None` | None — a no-op, not an error |
| `forming_bar`, traded | N seconds in one bucket | `{"t","o","h","l","c","v"}`: open of first traded second, max high, min low, close of last traded, summed `buy+sell`; `t` in ms | None |
| Arbitrary bar width | `forming_bar(rows, 600)` (not in `BAR_SECONDS`) | Folds at 600 s | None |
| `VerifiedDays` on a missing store | `candles_<venue>.db` absent | `verified_status` returns `None` (day unverified, file kept) | None |
| `VerifiedDays` on a pre-`verified_days` store | Old read-only DB without the table | Returns `None` | None |
| `rebuild` CLI parity | `python -m candles.rebuild` with today's `build_candles` args | Same bars, idempotent on re-run | Unchanged exit codes |

</intent-contract>

## Code Map

Moved out:
- `ml_signals/candle_store.py` — `_SCHEMA`, `_UPSERT`, `connect_rw`/`connect_ro`, `fold_arrays`,
  `apply_seconds`/`apply_batch`, `rebuild`/`rebuild_from_arrays`, `window`/`latest`/`oldest_t`/
  `watermarks`, `mark_verified`/`verified_status`, `prune`, `BAR_SECONDS`, `RETAIN_DAYS`.
- `ml_signals/candles.py` — `Candle`, `TIMEFRAMES`, `build_candles`, `aggregate_ohlc`,
  `candle_dicts_from_snapshots`, `is_valid_candle`, `PARTIAL_OBSERVED_FRACTION`,
  `candle_dicts_for_window`.
- `collector_core/build_candles.py` — `rebuild_instrument`, `all_instruments`,
  `venue_instruments`, `data_range_ns`, `day_chunks`, `_parse_date_ns`, `main`.

Capture side:
- `collector_core/collector.py:151,561-565,1085,1126-1141,1143-1166,1176-1183,1871,1881` — the
  import, the `connect_rw` + `CANDLES_DB_PATH` read, `_apply_to_candle_store`,
  `_catch_up_candle_store`, `_candle_prune_loop` and their call sites.
- `collector_core/collector.py:375-399` and `dydx_collector/collector.py:667-700` — Story 23.1
  `__getattr__` shims whose `REMOVE_AFTER` is **this** story.
- `ml_signals/error_ledger.py` — whole-module shim whose `REMOVE_AFTER` is **this** story.
- `dydx_collector/collector.py:205-222`, `bybit_collector/collector.py:99-108`,
  `hyperliquid_collector/collector.py:41-48` — the three composition roots.

Consumers to repoint:
- `data_api/live_candles.py:42,249-265`, `data_api/routes/candles.py:36-38,140-198`,
  `data_api/routes/rankings.py:35,38,193-227`, `ml_signals/footprint.py:40`.
- `collector_core/compare_klines.py:97,100,382,405,492-499,518`,
  `collector_core/prune_catalog.py:55,212-222`, `collector_core/repair_catalog.py:46-49`,
  `collector_core/rebuild_seconds.py:88-90`, `collector_core/nightly.py:84-126`.

Guards and infra:
- `platform/tests/test_boundaries.py` (`GRAPH`, `LEGACY_EDGES_UNTIL`, `LEGACY_MODULE_TO_CONTEXT`,
  `LEGACY_PRIVATE_IMPORTS_UNTIL`, `_exempt`), `platform/tests/test_images.py` (`_nightly_steps`,
  line 416), `platform/tests/test_namespace.py` (static — nothing to edit).
- `collector.dockerfile`, `data_api.dockerfile`, `Makefile` (`test`, `nightly`, line 222),
  `docker-compose.yml:55-57`, `ARCHITECTURE.md:45-61,127,135,312`, `CLAUDE.md:50` (DATA-05),
  `docs/DATA_DICTIONARY.md:126,223-224,424-438,681-684,741`, `docs/DEPLOY_CHECKLIST.md:18`,
  `README.md:207-217`.
- Parent spine `.../architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md:292`.

## Tasks & Acceptance

**Execution:**

- [x] `platform/kernel/second_snapshot.py` -- add a `SecondRow` `Protocol` (`ts_event`,
      `open_price`, `high_price`, `low_price`, `close_price`, `buy_volume`, `sell_volume`) --
      both capture and candles need one name for the duck-typed row, and only the kernel may be
      imported by both (AD-D2/AD-D3). Test that `SecondOHLC` and `DydxSecondSnapshot` satisfy it.
- [x] `platform/candles/__init__.py` -- context docstring in the `kernel/__init__.py` idiom:
      charter, the named invariants (exactly once; rebuildable from seconds; never ahead of the
      archive; one seconds→bars fold), dependency direction, closing with the
      `platform/tests/test_boundaries.py` enforcement note. No code.
- [x] `platform/candles/domain/fold.py` -- `fold_arrays` moved verbatim from
      `ml_signals/candle_store.py:138`, plus `BAR_SECONDS`, `RETAIN_DAYS`; add a keyword-only
      `bars: Sequence[int] = BAR_SECONDS` so one fold serves both the store's six widths and an
      arbitrary forming-bar width. Store path behaviour unchanged.
- [x] `platform/candles/domain/candle.py` -- `Candle`, `TIMEFRAMES`, `PARTIAL_OBSERVED_FRACTION`,
      `is_valid_candle`, and `is_partial(seconds_observed, bar_seconds)` extracted from
      `candle_store._candle:291`. Pure, no I/O.
- [x] `platform/candles/domain/candle_series.py` -- `CandleSeries` aggregate: per-instrument
      watermark, `accept(rows) -> (fresh_rows, new_through_ns)` implementing exactly-once, and the
      `seconds_observed`/`partial` semantics. Docstring names the three invariants. Pure.
- [x] `platform/candles/application/verified_days.py` -- `VerifiedDays` `Protocol`
      (`mark_verified(iid, day, status, mismatches, checked_at_ms)`, `verified_status(iid, day)`);
      docstring names "one store of day status" (AD-D9).
- [x] `platform/candles/application/sink.py` -- `CandleSink`: `apply(instrument_id, rows) -> int`
      and `watermarks() -> Mapping[str, int]`, structurally satisfying `SecondSink`. Docstring
      names "the store is never ahead of the archive".
- [x] `platform/candles/application/forming.py` -- `forming_bar(rows, bar_seconds) -> dict | None`
      over `fold_arrays`, returning exactly `{"t","o","h","l","c","v"}` (`t` in ms) or `None` when
      no row in the bucket traded. See Design Notes for why it is a dict, not a Nautilus `Bar`.
- [x] `platform/candles/application/queries.py` -- `window`, `latest`, `oldest_t`, `watermarks`,
      and `candle_dicts_for_window` re-expressed over `fold_arrays` (so `aggregate_ohlc` dies).
      Return shapes byte-identical to today, including `source` and `partial`.
- [x] `platform/candles/application/rebuild.py` -- `rebuild_instrument`, `all_instruments`,
      `venue_instruments`, `data_range_ns`, `day_chunks`, and `parse_date_ns` (made public: a
      `_private` name must not cross a context).
- [x] `platform/candles/application/prune.py` -- `loop(store)` returning a zero-arg coroutine
      factory with the body of `collector.py:1176-1183` (prune once, then hourly, ledgering via
      `collector.candle_store_prune`).
- [x] `platform/candles/infrastructure/sqlite_store.py` -- `CandleStore`: the only rw opener.
      `_SCHEMA`/`_UPSERT` byte-identical, `connect_rw`/`connect_ro`, `apply`, `rebuild_day`,
      `prune`, `mark_verified`, `verified_status`, plus `db_path_for_venue(candles_dir, venue)`
      and `store_from_env(catalog_path)` preserving the `CANDLES_DB_PATH` default exactly.
- [x] `platform/candles/infrastructure/verified_days.py` -- `VerifiedDaysStore(db_path)` and
      `VerifiedDaysDir(candles_dir)` (resolves venue → file via `db_path_for_venue`), both
      implementing `VerifiedDays`.
- [x] `platform/candles/rebuild.py` -- the `python -m candles.rebuild` CLI: argparse flags
      identical to `collector_core/build_candles.py:113-129`, delegating to `application.rebuild`.
- [x] `platform/collector_core/ports.py` -- new. `SecondSink` `Protocol`
      (`apply(instrument_id: str, rows: Sequence[SecondRow]) -> int`,
      `watermarks() -> Mapping[str, int]`) with a docstring naming the archive-ordering invariant.
- [x] `platform/collector_core/collector.py` -- add keyword-only `second_sink: SecondSink | None = None`;
      drop the `candle_store` import, `self._candle_db`, the `CANDLES_DB_PATH` read and
      `_candle_prune_loop` (and its `loops` entry); rewrite `_apply_to_candle_store` to loop the
      flushed dict calling `self._second_sink.apply(iid, rows)` and `_catch_up_candle_store` to
      drive `self._second_sink.watermarks()`; both no-op when the sink is `None`. Keep the
      `_flush_once` ordering at 1071-1085 intact. Delete the expired 23.1 shim block at 375-399.
- [x] `platform/dydx_collector/collector.py`, `platform/bybit_collector/collector.py`,
      `platform/hyperliquid_collector/collector.py` -- construct `CandleStore` via
      `store_from_env(config.catalog_path)`, pass `second_sink=CandleSink(store)` and append
      `candles.application.prune.loop(store)` to `extra_loops` (hyperliquid gains its first).
      Delete the expired 23.1 shim block in `dydx_collector/collector.py:667-700`.
- [x] `platform/data_api/live_candles.py`, `platform/data_api/routes/candles.py`,
      `platform/data_api/routes/rankings.py` -- call `candles.application.forming_bar` /
      `queries.window` / `queries.candle_dicts_for_window` / `domain.is_valid_candle`; use
      `db_path_for_venue` for the `candles_<venue>.db` derivation. Payloads unchanged.
- [x] `platform/ml_signals/footprint.py` -- import `Candle` from `candles.domain.candle`.
- [x] `platform/collector_core/compare_klines.py`, `platform/collector_core/prune_catalog.py` --
      take a `VerifiedDays` argument threaded from their `main()`; neither opens the DB. Keep the
      `window` reads in `compare_klines` via `candles.application.queries`.
- [x] `platform/collector_core/nightly.py` -- generalise `module()` to a full dotted path, run
      `candles.rebuild` for the `build_candles` step (step **name** unchanged, so the frozen
      summary line and README stay valid), derive `db` via `db_path_for_venue`.
- [x] `platform/collector_core/repair_catalog.py`, `platform/collector_core/rebuild_seconds.py` --
      import the rebuild helpers from `candles.application.rebuild` (`parse_date_ns` now public).
- [x] `platform/ml_signals/candle_store.py`, `platform/ml_signals/candles.py`,
      `platform/collector_core/build_candles.py` -- replace with pure re-export shims in the
      `collector_core/second_snapshot.py` idiom, `REMOVE_AFTER = "24-3-alerting-context-as-forming-bar-observer"`.
      Names whose successor changed shape (`aggregate_ohlc`, `candle_dicts_from_snapshots`,
      `build_candles`) go in `_REPLACED_NAMES` and raise, naming `candles.application.forming_bar`.
- [x] `platform/ml_signals/error_ledger.py` -- delete (its `REMOVE_AFTER` is this story); update
      `tests/test_boundaries.py:182,497` and `tests/test_images.py:416` accordingly.
- [x] `platform/tests/test_boundaries.py` -- delete the `(CAPTURE, CANDLES)` entry; map every new
      `candles.*` module to `CANDLES` and `collector_core.ports` to `CAPTURE`; repoint the three
      `LEGACY_PRIVATE_IMPORTS_UNTIL` keys to `candles.tests.test_candle_store`; add
      `COMPOSITION_ROOTS: dict[str, frozenset[str]]` (the three venue entrypoints and
      `dydx_collector.tests.test_candle_feed`, each allowed **only** `CANDLES`), honour it in
      `test_cross_context_edges_follow_the_graph`, and add tests that every entry is still needed
      and that a root reaching any other context still fails.
- [x] `platform/tests/test_images.py` -- update `_nightly_steps` to read each step's module from
      the `module(...)` call rather than assuming the `collector_core.` prefix.
- [x] `platform/collector.dockerfile`, `platform/data_api.dockerfile` -- add
      `COPY platform/candles ./candles`.
- [x] `platform/Makefile` -- add `candles/tests` to the `test` list; line 222 runs
      `python3 -m candles.rebuild`. (Not added to `test-live-paper`: see Design Notes.)
- [x] `platform/candles/tests/` -- `__init__.py` (LGPL header only) plus the moved
      `test_candle_store.py` / `test_candles.py` and new tests per Acceptance Criteria below.
- [x] Repoint every remaining test importer of the three old paths (`collector_core/tests/{test_collector,test_compare_klines,test_venue_time,test_prune_catalog,test_consolidate_catalog}.py`,
      `dydx_collector/tests/{test_build_candles,test_repair_catalog,test_candle_feed}.py`,
      `data_api/tests/{test_candles,test_live_candles,test_screener_columns}.py`,
      `ml_signals/tests/test_footprint.py`). Rewrite `collector_core/tests/test_collector.py` to a
      fake `SecondSink` so capture's tests no longer touch candles at all.
- [x] Docs in the same commit -- `ARCHITECTURE.md` (rewrite 45-61 past tense; update 127/135/312),
      `CLAUDE.md` DATA-05, `docs/DATA_DICTIONARY.md` §2.5 (rewritten around `forming_bar`/`window`;
      the current text still describes a pre-store world) and §5/§6 paths,
      `docs/DEPLOY_CHECKLIST.md:18`, `README.md`, `docker-compose.yml:55-57`.
- [x] Parent spine `ARCHITECTURE-SPINE.md:292` -- append a second
      `[amended 2026-09-25: Story 24.1 — ...]` marker to the "Writer→reader imports contradict
      AD-4" bullet, narrowing "what remains" to the Story 25.1 `repair_catalog` read.

**Acceptance Criteria:**
- Given a flush where one instrument's `write_data` raised, when `_flush_once` completes, then
  `SecondSink.apply` was called for the successful instruments only and never for the failed one.
- Given a collector whose sink raises for one instrument, when the flush completes, then the other
  instruments were still applied and `collector.candle_store` appears in the error ledger.
- Given a fixture day of recorded seconds, when it is rebuilt into a store and every closed bucket
  is also passed to `forming_bar`, then the `o/h/l/c/v` values and the `partial` flags agree for
  1 m, 5 m and 1 h.
- Given `platform/`, when the source tree is searched for seconds→bars aggregation, then exactly
  two folds exist: `kernel.fold.fold_trades` and `candles.domain.fold.fold_arrays`.
- Given the new store module, when its `_SCHEMA` is compared with the recorded pre-move text, then
  they are byte-identical.
- Given `python -m candles.rebuild` run twice over the same day, when the stores are compared, then
  the bars are identical (idempotent rebuild).
- Given `make test`, when it runs, then `test_boundaries.py`, `test_images.py` and
  `test_namespace.py` pass, no `DeprecationWarning` is raised, and the only failures are the ten
  pre-existing ones.

## Spec Change Log

## Review Triage Log

### 2026-09-25 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 0, medium 4, low 12)
- defer: 1: (high 0, medium 0, low 1)
- reject: 3
- addressed_findings:
  - `[medium]` `[patch]` `bybit_collector/tests/test_sequence_canary.py` stopped pinning `CANDLES_DB_PATH`, so three tests opened one shared candle store under pytest's tmp root read-write and never closed it — breaking the store's own single-writer invariant inside the suite. Restored via `monkeypatch.setenv` into each test's `tmp_path`.
  - `[medium]` `[patch]` Per-instrument `error_ledger.record` in `_apply_to_candle_store` could emit one line per subscribed instrument per flush, blowing the ledger's 60-lines-per-site-per-minute cap exactly during a store-wide fault. Now one line per flush naming every failed instrument; per-instrument isolation of the write itself is unchanged.
  - `[medium]` `[patch]` `prune_catalog._leaf_statuses` parsed the instrument id lazily inside the day comprehension, so a malformed leaf with no day past the cutoff lost its "not an instrument id, never pruned" warning. The id is parsed up front again.
  - `[medium]` `[patch]` `VerifiedDaysDir` cached an absent store as permanently absent while its own `mark_verified` creates that file, so a verdict it had just written read back as unverified. Only open connections are cached now; the false `Known limit:` was removed.
  - `[low]` `[patch]` `fold_arrays` accepted `bar_seconds <= 0`, degrading the old `ZeroDivisionError` into a numpy `RuntimeWarning` plus one bogus `t=0` bar. Now raises `ValueError`.
  - `[low]` `[patch]` The collector's catch-up bail-out still told the operator to "run build_candles"; now names `python -m candles.rebuild`.
  - `[low]` `[patch]` `docs/DATA_INTEGRITY_AUDIT.md` D-37 still asserted the store is fed "in one transaction" per flush; updated to the per-instrument transaction.
  - `[low]` `[patch]` `CLAUDE.md` DATA-07 and `docs/DATA_DICTIONARY.md` still described `ml_signals.error_ledger` as a live deprecated re-export after this story deleted it.
  - `[low]` `[patch]` `candles/__init__.py` claimed `infrastructure/` is imported only by a composition root, which `application/sink.py` and `application/rebuild.py` contradict; corrected and recorded as a `Known limit:` with its upgrade path.
  - `[low]` `[patch]` `CandleSeries.buckets` docstring claimed a watermark guard it does not perform; corrected to state that exactly-once is `accept`'s job.
  - `[low]` `[patch]` The retention loop's wiring was asserted for dYdX only, though `Collector` no longer starts it and a venue that forgot would silently stop pruning. Added `bybit_collector/tests/test_candle_wiring.py` and `hyperliquid_collector/tests/test_candle_wiring.py` (also covering the `second_sink=None` silent-no-op risk), registered as composition roots.
  - `[low]` `[patch]` The multi-bar-width consequence of one flush (a 30 s flush landing in the 3600 s bucket with `seconds_observed == 30` and `partial`) was no longer asserted anywhere; restored in `dydx_collector/tests/test_candle_feed.py`.
  - `[low]` `[patch]` The Story 21.5 pairing proving `kernel.catalog_files.query_second_ohlc` and `ml_signals.catalog_stats.query_second_snapshots` agree was dropped when its test moved into `candles/` (which may not import `ml_signals`); restored in `ml_signals/tests/test_catalog_stats.py`, which owns the decoder.
  - `[low]` `[patch]` `epic-24-context.md` defined `domain/` as stdlib/kernel/Nautilus only while the boundary test now admits `numpy`; aligned.
  - `[low]` `[patch]` `test_a_composition_root_may_reach_only_its_one_named_context` asserted against `next(iter(...))`, silently testing one arbitrary entry; parametrized over every root and split out the negative case.
  - `[low]` `[patch]` The story file's ticked AC text still read "all three dockerfiles" and "struck as fully resolved"; both are sanctioned spec deviations, now recorded in the story's Completion Notes rather than left as unexplained mismatches.

### 2026-09-25 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 1, medium 3, low 5)
- defer: 5: (high 0, medium 1, low 4)
- reject: 3
- addressed_findings:
  - `[high]` `[patch]` `/ws/live`'s `_parse_candle_channel` bounded `bar_seconds` only below (`> 0`), and this story replaced `live_candles`' pure-Python bucket arithmetic with `fold_arrays`' numpy int64 arithmetic — so a client-written subscribe channel such as `candles:BTC-USD-PERP.DYDX:99999999999999999999` now raises `OverflowError: Python int too large to convert to C long` inside the fold. That escapes `LiveCandleBus.handle_batch` into `run`'s reconnect loop while the listener stays registered, so it re-raises on every `snapshots:raw` batch: live candles stop for **every** connected client, permanently. Bounded at both ends — `candles.domain.fold.MAX_BAR_SECONDS` raises a `ValueError` where the int64 limit actually lives, and `ws/live.py` rejects anything above `604_800` (the same 1w ceiling `/api/candles` already clamps to), rejected rather than clamped so no bar is published on a channel the client never asked for. Boundary cases added to the parse table and to `candles/tests/test_candles.py`.
  - `[medium]` `[patch]` `CLAUDE.md` DATA-05 was amended in the landing commit with the pre-fix wording — "one entry per instrument since Story 24.1's per-instrument `SecondSink.apply`" — while the first review pass had already consolidated the ledger line to one per flush. An operator reading the rule during a store-wide fault would expect N lines in `/api/errors`, see 1, and conclude the ledger was dropping entries. Rewritten to state both halves (per-instrument *commit*, per-flush *line*) and why. The matching stale docstring on `test_one_sinks_failure_is_loud_and_never_stops_the_other_instruments` was corrected too.
  - `[medium]` `[patch]` `CLAUDE.md`'s "Adding a venue" step 4 still prescribed only `super().__init__(config, client)`, though this story moved candle-store ownership out of the base class into the three composition roots. A fourth venue written from the recipe would connect, subscribe and archive Parquet while producing **zero** bars and never pruning, forever. Step 4 now names all three required pieces (`store_from_env`, `second_sink=CandleSink(store)`, `prune.loop(store)`), the two failure modes, the per-venue wiring test to copy and the `COMPOSITION_ROOTS` registration; its stale `hyperliquid_collector/collector.py:42-44` citation was dropped.
  - `[medium]` `[patch]` `second_sink=None` made both `_apply_to_candle_store` and `_catch_up_candle_store` an unconditional `return` — no log, no ledger, no counter — where the base class opening the store itself had previously made a mis-wired venue structurally impossible. The three per-venue wiring tests are a convention, not a structural guard, so a deployed process with no sink was exactly the silent gap DATA-01 forbids. `_catch_up_candle_store` (the one path that runs exactly once per start) now logs a `No SecondSink injected` warning naming the fix, asserted in `test_a_collector_without_a_sink_still_flushes`. The I/O matrix's sanctioned behaviour is unchanged: apply and catch-up are still no-ops and the collector still runs.
  - `[low]` `[patch]` The consolidated per-flush ledger line kept `first = e if first is None else first`, so the one recorded traceback was whichever instrument `flushed.items()` happened to yield first — an incidental `TypeError` on one instrument would mask `OSError: disk I/O error` from the other 29, the exact detail consolidating the line was meant to preserve. Now one exception is kept per distinct type and every type is named in the detail; covered by a new test using a fake sink that raises a different type per instrument.
  - `[low]` `[patch]` `CandleStore`'s docstring named a stricter invariant than the code keeps — "exactly one process holds one per file" — while the shipped `candles.rebuild --workers` defaults to `os.cpu_count()` and every worker opens its own `connect_rw` on that same file. Restated as one writer *per instrument-day* (which the worker partitioning does hold) plus a `Known limit:` naming the 60 s busy timeout, the `database is locked` failure mode, `--workers 1` as the nightly chain's avoidance and the upgrade path (DESIGN-01).
  - `[low]` `[patch]` `ARCHITECTURE.md` still listed `ml_signals` as a reader of `candles_*.db` in two rows, though after this story its only reference is the dead re-export shim that `test_namespace.py` proves nothing imports; the store row also credited only "that venue's collector" as writer, which no longer opens the file at all. Both rows corrected with the ports that replaced those edges.
  - `[low]` `[patch]` The headline AC #2 test (`test_forming_matches_stored.py`) built its "stored" side with one `store.rebuild` over the very rows it then handed `forming_bar`, so both sides reduced to a single `fold_arrays` call and the test could not fail for any reason the two paths could actually diverge. Its fixture is now parametrized over both real write paths — the rebuild CLI's whole-day fold and the collector's 30 s `SecondSink.apply` batches — so `_UPSERT`'s cross-flush accumulation (`v = v + excluded.v`, `h = max(...)`, `seconds_observed = seconds_observed + ...`) is exercised against the single fold, which is what actually has to agree for a chart's bar not to jump at close. Volume comparison switched from an absolute `1e-9` (magnitude-blind above ~1e4) to `pytest.approx(rel=1e-12)`.
  - `[low]` `[patch]` The three venue retention-loop assertions matched on `getattr(loop, "__name__", "") == "prune_loop"`, which passes for a venue that started *another* venue's prune loop or two venues sharing one store — the copy-paste mistake the tests were added to catch. Each now reads the store out of the closure's cell and asserts it is the same object the venue's sink writes.

### 2026-09-25 — Review pass (third)
- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 0, medium 2, low 8)
- defer: 3: (high 0, medium 1, low 2)
- reject: 9
- addressed_findings:
  - `[medium]` `[patch]` A deployed collector with no `SecondSink` reported itself with a bare `logger.warning`, which DATA-07 names outright as unacceptable at a data-dropping site ("A bare `logger.warning(...); continue` ... is not acceptable"). A venue entrypoint that forgets `second_sink=` archives Parquet and builds **zero bars forever**, and that gap reached neither `GET /api/errors`, nor the non-dismissible `<ErrorBar>`, nor `crosscheck_errors` — only one Dozzle line among the startup chatter. Worse, `test_a_collector_without_a_sink_still_flushes` asserted `error_ledger.counts() == {}`, locking the silence in. Now `error_ledger.record("collector.no_second_sink", ...)`, once per start, with the test asserting the entry instead of its absence. The I/O matrix's sanctioned behaviour is unchanged: apply and catch-up are still no-ops and the collector still runs.
  - `[medium]` `[patch]` `CLAUDE.md` DATA-05 — the binding rules file every session is told to read before touching `platform/` — was left grammatically corrupt by the previous pass's edit: a new sentence was inserted between `python -m candles.rebuild` and its relative clause, so the file read "...would exceed the 60-lines-per-site-per-minute write cap exactly during the store-wide fault whose detail matters most, **which recomputes whole UTC days from raw 1s and is idempotent**", attributing day-rebuilding to the write cap. Rewritten so the clause sits back on the rebuild, and extended to state the ledger line's actual content (per-type counts plus the dominant cause's traceback) and the new `collector.no_second_sink` site.
  - `[low]` `[patch]` The previous pass consolidated the per-flush ledger line to keep "one exception per distinct type" so a store-wide fault would not be masked — but then picked the reported traceback with `by_type[sorted(by_type)[0]]`, i.e. alphabetically. An `AttributeError` on one instrument still masks `OSError: no space left on device` on the other 29, which is precisely the case the fix existed for. The traceback now goes to the cause that hit the most instruments (ties broken on the name, so the line stays reproducible) and the detail names each type with its instrument count. Covered by a new three-instrument test asserting `record.exc_info[0] is OSError` against a 2-vs-1 split.
  - `[low]` `[patch]` `fold_rows` short-circuited on an empty batch *before* `fold_arrays` validated `bar_seconds`, so the same out-of-range width was refused for a busy instrument and silently accepted for a quiet one — a bad `/ws/live` channel would look accepted until its first trade arrived. Validation extracted to `domain.fold.check_bars` and called on both paths; boundary cases added over the empty batch.
  - `[low]` `[patch]` `candles.application.queries.watermarks` was a byte-identical, zero-caller duplicate of `infrastructure.sqlite_store.watermarks` (the shim and `CandleSink` both use the infrastructure one). Two copies of the same read inside the context whose charter is "one fold ... so they cannot disagree"; deleted.
  - `[low]` `[patch]` `BAR_SECONDS`' unwritten "must divide 86 400 s" constraint: `sqlite_store.rebuild` deletes one UTC day and refolds that day's seconds alone, so a width that straddles midnight (1 w is already offered by the chart, and `TIMEFRAMES` lists 10 m/30 m/45 m) would be rewritten from half its input and then *accumulate* through the `_UPSERT` on the next day's rebuild — a permanently understated bar no read could detect. Recorded as a `Known limit:` on the constant naming the ceiling and the upgrade path (DESIGN-01); `RETAIN_DAYS` beside it already carried such a note, `BAR_SECONDS` did not.
  - `[low]` `[patch]` `candles/__init__.py`'s `Known limit:` named a smaller ceiling than the code has — it admitted only that `application.sink`/`application.rebuild` name `CandleStore` concretely, while `application.queries` takes a `sqlite3.Connection` in every signature and holds the `SELECT` text itself, forcing every reader to import `infrastructure.connect_ro` to call the application layer at all. Restated with a read-model port as the upgrade path.
  - `[low]` `[patch]` `rebuild_instrument` called `store.close()` outside a `finally`, so a day whose Parquet read or fold raised leaked an open read-write SQLite/WAL handle — and a `ProcessPoolExecutor` worker outlives the failing job (`pool.map` surfaces the error only when the result is consumed), so the handles pile up in a process that keeps taking work.
  - `[low]` `[patch]` `python -m collector_core.build_candles` — the stale operator/cron invocation the shim exists for — printed no deprecation notice at all: under `-m` the module-level warning is attributed to `<frozen runpy>`, so the default `__main__`-only `DeprecationWarning` filter drops it (verified both ways in the worktree). The CLI branch now forces the filter and re-emits the same message; `sys` could not be imported for a plain stderr write because `tests/test_namespace.py` allows a re-export shim no bare `import` but `warnings`.
  - `[low]` `[patch]` `prune.loop`'s inner function name and its single free variable are a real contract — the three venue wiring tests find the retention loop in `extra_loops` by `__name__ == "prune_loop"` and read the store out of `__closure__` — with nothing in `prune.py` saying so, so a rename or a switch to a partial/class would break three venue suites with an opaque `IndexError`. Noted in the docstring.

Deferred (pre-existing, ledgered): `make build-candles` has no `--venue` on a shared three-venue catalog; the hourly prune runs synchronously on the collector's event loop against an index the frozen schema does not have. Rejected as noise or as spec-mandated: the three zero-caller re-export shims and the `TIMEFRAMES`/`Candle` placement (both required verbatim by the intent contract), `CandleSeries` "having no state" (it holds and advances the watermark `accept` enforces exactly-once with), N-commits-per-flush (a sanctioned Design Note; `test_hotpath`'s `CANDLES_DB_PATH` removal is correct because `_collectors` builds a bare `Collector` that never opens a store), and five edge cases verified byte-identical to the pre-change code (`CANDLES_DB_PATH=""`, the rebuild watermark's ms truncation, a duplicate `ts_event` inside one `accept`, `VerifiedDaysDir` inode caching, `compare_klines`' `sqlite3.OperationalError`-only catch).

## Design Notes

**`forming_bar` returns a dict, not a Nautilus `Bar`.** AD-D8 writes the signature as
`-> Bar | None`, but AD-D12 freezes the `/ws/live` payload (`{t,o,h,l,c,v}`, pinned by
`frontend/src/hooks/useLiveCandle.ts:19-31` and by byte-for-byte assertions in
`data_api/tests/test_live_candles.py:72,180`) and `queries.window` already returns dicts of the
same shape. A `Bar` would need a `BarType`/`InstrumentId` this path never constructs plus a
serializer at the WS boundary — a published-language change the epic forbids. The frozen contract
wins; record the deviation as a `Known limit:` comment on `forming_bar` naming `Bar` as the
upgrade path once views owns the wire format.

**Per-instrument apply replaces `apply_batch`'s single transaction.** The port's
`apply(iid, rows)` commits once per instrument instead of once per flush. A crash mid-loop now
leaves some instruments applied rather than none — both states are recoverable (the watermark is
per-instrument and `rebuild` is idempotent), and one instrument's failure no longer discards the
whole batch. Note it in the sink docstring.

**Catch-up must precede the first live apply.** `CandleSeries.accept` drops rows at or below the
watermark, so if a live second landed first the archive backfill would be silently skipped. Keep
the call at `run()`'s existing site (`collector.py:1871`), before subscribe and well before the
first flush, and assert the ordering in a test.

**`candles/rebuild.py` vs `candles/application/rebuild.py`:** the story fixes the CLI at
`python -m candles.rebuild`, so the top-level module is the argparse entrypoint and the
application module holds the logic it calls.

**`test-live-paper` does not gain `candles/tests`.** `live_paper` imports no candles code, and
`test_images.py` asserts a target's test paths are a subset of its image's `COPY` set — adding the
list entry would force a `COPY platform/candles` into `live_paper.dockerfile` for code that image
never runs. This is a deliberate narrowing of AC #4's "Makefile test lists"; `candles/tests` is in
the `test` target, which is the list that can run it.

## Verification

**Commands:**
- `cd platform && CARGO_TARGET_DIR=<repo-root>/target python3 -m pytest -o addopts="" --rootdir=. candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q`
  -- expected: no failures beyond the ten pre-existing ones, and no `DeprecationWarning`.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. tests/test_boundaries.py tests/test_images.py tests/test_namespace.py -q` -- expected: all pass.
- `cd platform && ruff format --check . && ruff check . && mypy candles collector_core data_api` -- expected: clean.
- `cd platform && python3 -m candles.rebuild --help` -- expected: the same flags as
  `python3 -m collector_core.build_candles --help` before the change.
- `cd platform && grep -rn "aggregate_ohlc\|candle_dicts_from_snapshots\|PARTIAL_OBSERVED_FRACTION" --include='*.py' .`
  -- expected: hits only in the `ml_signals/candles.py` shim's `_REPLACED_NAMES` and in
  `candles/domain/candle.py`.


## Auto Run Result

Status: done (third review pass over the `done` spec; `review_loop_iteration` 0, no loopback)

### Summary of implemented change

Story 24.1 itself landed in `1fccbcabea`: `platform/candles/` extracted as a bounded context in the
spine's three-layer shape, capture feeding it through the new `collector_core.ports.SecondSink`
injected by each of the three venue entrypoints, the platform collapsed to exactly two folds
(`kernel.fold.fold_trades` and `candles.domain.fold.fold_arrays`), and the archive tools given a
`VerifiedDays` port instead of a database connection. This pass is a third independent review of
that diff (`7cd2f91aa2..HEAD`, 91 files, 7 378 lines) and applied ten fixes. No intent gap and no
bad-spec loopback: the contract held, and every finding was a patch or pre-existing.

### Files changed in this pass

- `platform/collector_core/collector.py` — the sink-less collector now records
  `collector.no_second_sink` in the error ledger instead of logging a bare warning (DATA-07); the
  per-flush ledger line's traceback goes to the cause that hit the most instruments and its detail
  names each exception type with its instrument count.
- `platform/candles/domain/fold.py` — `check_bars` extracted so an out-of-range `bar_seconds` is
  refused on the empty-batch path too; `Known limit:` on `BAR_SECONDS` naming the "must divide a
  day" constraint the day-scoped rebuild depends on.
- `platform/candles/application/queries.py` — the dead, duplicated `watermarks` read deleted.
- `platform/candles/application/rebuild.py` — `store.close()` moved into a `finally` so a failing
  day does not leak a read-write handle inside a pool worker.
- `platform/candles/application/prune.py` — the closure contract the three venue wiring tests
  assert on, written down.
- `platform/candles/__init__.py` — the layering `Known limit:` widened to cover `queries`' raw
  `sqlite3.Connection` signatures.
- `platform/collector_core/build_candles.py` — the shim's deprecation notice now reaches the
  `python -m` caller it exists for.
- `platform/CLAUDE.md` — DATA-05's corrupted sentence repaired and brought in line with the code.
- `platform/collector_core/tests/test_collector.py` — the no-sink test asserts the ledger entry
  rather than its absence; new test proving the dominant cause wins the one traceback.
- `platform/candles/tests/test_candles.py` — bar-width boundaries over an empty batch.

### Review findings breakdown

Ten patches applied (2 medium, 8 low). Three findings deferred to
`_bmad-output/implementation-artifacts/deferred-work.md` (`make build-candles` has no `--venue` on
the shared three-venue catalog; the hourly prune blocks the collector's event loop against an index
the frozen schema lacks; the `CandleStore` lifecycle entry was already ledgered by an earlier pass).
Nine findings rejected: four contradicted explicit intent-contract requirements or sanctioned Design
Notes, and five were verified byte-identical to the pre-change code.

### Verification performed

- `python3 -m pytest -o addopts="" --rootdir=. candles/tests collector_core/tests dydx_collector/tests
  bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests
  bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` — **1527 passed, 10
  failed**, and the ten are exactly the pre-existing set the spec names (dydx `trade_ohlc` ×5,
  `ofi_strategy` ×4, rankings-redis ×1).
- The same run over `candles/tests collector_core/tests tests kernel/tests observability/tests` with
  `-W error::DeprecationWarning` — **774 passed**, so no `DeprecationWarning` escapes (TEST-04).
  `tests/test_boundaries.py`, `test_images.py` and `test_namespace.py` are in that set.
- `ruff format --check` and `ruff check` clean on every file touched (the one remaining `D401` in
  `collector_core/migrate_open_interest.py` is pre-existing and untouched).
- `mypy candles collector_core data_api` — no error in `candles/` or `collector_core/collector.py`;
  the 20 reported are all pre-existing test-file annotations unchanged by this pass.
- `python3 -m candles.rebuild --help` — same flags as before. `python -m collector_core.build_candles
  --help` now prints the deprecation notice; plain `import collector_core.build_candles` still does.
- `grep -rn 'def fold_arrays|def fold_trades'` — exactly two folds.

### Residual risks

- The `collector.no_second_sink` ledger site is new: a venue entrypoint under construction will now
  surface an entry in `/api/errors` at startup rather than a log line. That is the intent, but it
  is user-visible behaviour a deployment should expect.
- The per-flush ledger detail's wording changed (`OSError on 2, TypeError on 1`). Nothing parses it
  — `crosscheck_errors` keys on the site, not the detail — but it is operator-facing text.
- The deferred `make build-candles` gap means an operator following DATA-05 after a `collector.candle_store`
  failure on Bybit or Hyperliquid still repairs the wrong store; the ledger entry carries the fix.
