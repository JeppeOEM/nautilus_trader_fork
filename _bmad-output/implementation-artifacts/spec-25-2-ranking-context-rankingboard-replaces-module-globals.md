---
title: 'Story 25.2: ranking/ context: RankingBoard replaces the module globals'
type: 'refactor'
created: '2026-09-26'
status: 'done'
baseline_revision: 'a046e0839a873301239eb93ea5984a6d87428ecb'
final_revision: 'f7c332107be1cb42528cf09c86fb8644436d56bb'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-25-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/25-2-ranking-context-rankingboard-replaces-module-globals.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `ranking_engine/engine.py` keeps the ranking state in twelve mutable module globals. Its volume polls hold their own venue URL maps. It hand-indexes `snapshots:raw` dicts. The pct/volatility math and the catalog price read still live in `ml_signals`. So the engine cannot be unit-tested without resetting globals, and the math has no single owner.

**Approach:** Create the `ranking/` bounded context (`domain/` · `application/` · `infrastructure/` · `__main__`). A `RankingBoard` aggregate owns the mode, the per-instrument `InstrumentMetrics`, the volume book and the `RankingsPublisher`. A `RankingEngine` built at `__main__` drives it through four ports. Make `ranking_engine` a pure re-export shim, delete `ml_signals`, and prove the `rankings:live` bytes are unchanged with a replay recorded from the pre-move engine.

## Boundaries & Constraints

**Always:**
- These wire and store contracts stay byte-for-byte: the `rankings:live` payload (field names, order, values, publish decisions), `ranking:control` parsing (`{"mode": "volume"|"volatility"}`; anything else is logged and ignored), the `metrics.db` schema and rows, ledger site names (`ranking_engine.volume24h`, `ranking_engine.snapshot_entry`, `ranking_engine.price_backfill`, `catalog_stats.mark_prices`), the compose service name `ranking_engine`, and every env var name and default.
- Layering (AD-D2): `domain/` imports only the stdlib, `kernel`, numpy and Nautilus model/core; it does no I/O and no ledgering (it returns what to ledger). `application/` holds the Protocol ports, the engine loops and `queries.py`. `infrastructure/` is imported by `__main__`, by `application/queries.py` (the candles precedent) and by tests.
- No module-level mutable runtime state anywhere in `ranking/` (non-test modules): no mutable literals or factories, no `global`, no memoising cache, and no import-time call other than the kernel's sanctioned ones plus `logging.getLogger`. Env vars are read only inside a `__main__` function.
- `snapshots:raw` is decoded only by `DydxSecondSnapshot.from_dict`. The kernel indicator functions receive `DydxSecondSnapshot.to_dict(snap)`. Venue REST goes only through `kernel.venue_http` (its URL maps, `USER_AGENT`, `get_request`/`post_json_request`/`http_json`).
- Every aggregate/port docstring names its invariant (DESIGN-01), and every tolerated failure is ledgered (DATA-07).

**Block If:**
- Byte identity with the recorded pre-move output cannot be reached without changing the wire payload.

**Never:**
- Rename a published field, ledger site, service, env var or the `metrics.db` columns.
- Touch `nautilus_trader/` or `crates/`, or write `sprint-status.yaml`.
- Leave a second `price_stats_from_series`.
- Import `ranking` from `research` or `data_api`.
- Re-add a TUI ranking consumer.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Recorded burst | Generated `snapshots:raw` batches (digest pinned), fixed clock, three venues' volumes, a slow-loop cycle, a mode switch | The sequence of published `rankings:live` strings is identical (sha256 per publish; the final message in full) to the fixture recorded from the pre-move engine | — |
| Malformed entry | A batch with one entry that `from_dict` rejects | The other entries are ingested; the bad entry is neither fresh nor ranked | One `ranking_engine.snapshot_entry` ledger entry |
| No USD volume | A fresh iid absent from every live source | Absent in volume mode; present with `volume24h: null` in volatility mode | One `ranking_engine.volume24h` ledger entry per poll cycle |
| Source expired / poll failed | Last good poll older than 3 × 60 s, or a fetch raising or timing out (45 s) | The expired source's rows leave volume mode; a failed poll keeps its last good values | Ledgered once per cycle / per failure |
| Stale / dead instrument | Not received for > 30 s / > 1 h | > 30 s: out of ranks, listed in `stale_instrument_ids`. > 1 h: state dropped by `age_out`, and the instrument is backfilled again if it returns | — |

</intent-contract>

## Code Map

- `platform/ranking_engine/engine.py` -- the twelve globals (`:113-202`), parsers/fetchers (`:238-452`), ingest (`:561-628`), ranks and message (`:631-980`), publisher and loops (`:983-1114`).
- `platform/ranking_engine/{metrics_store,price_series,volatility}.py` -- move. `metrics_store` has module `_lock`/`_connections` → becomes a class.
- `platform/ml_signals/` -- `catalog_stats.price_series`/`price_stats_from_series` (move), `list_instruments`/`price_stats`/`metrics_computer.compute_*` (dead since 13.2: delete), `rank_history` (HTTP client of data_api, no caller: moves to `research/`), 24.4 shims, `tests/`, and two preference TOMLs (views' stores, bind-mounted from `./ml_signals/`).
- `platform/kernel/{venue_http,catalog_files,second_snapshot,indicators}.py` -- reused, unchanged.
- `platform/views/coin_detail.py:28-40` -- `metrics_store.history`/`nearest` → `ranking.application.queries`.
- `platform/data_api/tests/{test_data_api,test_metrics}.py`, `data_api/routes/{indicators,rankings}.py` -- store seeding; the TOML default paths.
- `platform/tests/test_boundaries.py` -- maps, `LEGACY_*_UNTIL` 25-2 entries, `VIEWS_QUERY_SERVICES`, `NON_VENUE_HTTP_CLIENTS`, the kernel state rule to reuse; `tests/test_images.py`, `tests/test_namespace.py` (static shim scan).
- Wiring and docs: `docker-compose.yml:12-14,147,165,192,210`, `collector.dockerfile:23,25`, `data_api.dockerfile:34-35`, `Makefile:149`, `ARCHITECTURE.md`, `CLAUDE.md` (SSOT-02, Adding a venue step 7, NAUT-03), `docs/DATA_DICTIONARY.md` §3, `docs/DEPLOY_CHECKLIST.md`, the parent spine `:305`, `_bmad-output/project-context.md`.

## Tasks & Acceptance

**Execution:**
- [x] Scratch (not committed) -- drive the **pre-move** engine with a deterministic generated burst, a fake clock, fake volume fetchers, one `_slow_loop_once` over an empty tmp catalog/db and a mode switch; record the input digest, the sha256 of every publish, the final message and the persisted `metrics.db` rows into `ranking/tests/fixtures/replay_burst.json` -- the byte-identity evidence.
- [x] `platform/ranking/domain/` -- move the math and state into the domain:
  - `values.py`: `RankingMode` (StrEnum, with `parse` → `None` if unknown), `VolumeReading(value_usd, observed_ns)`, `type VolatilityScore = float`, `parse_usd_volume`.
  - `price_series.py`: `PriceSeriesStore`, `_RingBuffer`, `PRICE_LOOKBACK_HOURS`, plus `drop`.
  - `volatility.py`: `VolatilityTracker` plus `drop`.
  - `metrics.py`: `price_stats_from_series`, `pct_change_from`.
  - `board.py`: `InstrumentMetrics` (indicators, 300-deque, last_seen/last_fed, backfilled, slow metrics) and `RankingBoard` (`ingest(snap, received_ns)`, `switch_mode`, `record_volume_poll`/`refresh_volumes(now_ns)`/`missing_volume_iids`, `current_ranks`, `ranks_by_iid`, `stale_ids`, `build_message`, `age_out`, `slow_snapshot`), plus `RankingsPublisher` taking an injected `now_fn`.
- [x] `platform/ranking/application/ports.py` -- declare the Protocol ports:
  - `VolumeSource`: `name`, `fetch() -> dict[str, float]` (blocking).
  - `PriceHistory.series(iid, start_ns)`.
  - `RankingHistory`: write/latest/history/nearest/price_near_days_ago.
  - `LivePublisher.publish(message: str)`.
- [x] `platform/ranking/application/engine.py` -- `RankingEngine(board, ports, config, clock)`:
  - Methods: `handle(channel, data)`, `ingest_snapshot_batch`, `switch_mode`, `volume_cycle`/`volume_loop`, `slow_loop_once`/`slow_loop`, `heartbeat`, `maybe_publish`.
  - The lock and the backfill read the old behaviour verbatim.
  - `RankingConfig` is a frozen dataclass.
- [x] `platform/ranking/application/queries.py` -- `history`/`nearest(…, db_path)` over read-only per-call connections. A missing db file gives `[]`/`None`.
- [x] `platform/ranking/infrastructure/` -- implement the adapters:
  - `redis.py`: the publisher adapter plus the listener loop with its reconnect.
  - `metrics_store.py`: `COLS`, `SqliteMetricsStore(db_path)` (lock, `close()`) and the read-only readers.
  - `catalog_prices.py`: close prices via `kernel.catalog_files.query_second_ohlc`, keeping the mark-price fallback.
  - `volume_dydx.py`/`volume_bybit.py`/`volume_hyperliquid.py`: the pure parsers kept, fetch built via `kernel.venue_http`.
- [x] `platform/ranking/__main__.py` -- read the env inside `main()`, validate the environments against the kernel maps (fail fast), build the engine and gather the four loops.
- [x] `platform/ranking/tests/` -- port `ranking_engine/tests` onto the new API and add:
  - AC-2 invariant tests (both scores on every row, the volume-mode exclusion with its ledger, the stale age-out, mode last-write-wins, publish on change and on heartbeat).
  - The replay test.
  - A port-contract test that `SqliteMetricsStore` satisfies `RankingHistory`.
  - `test_ad8_boundary.py` over all `ranking/*.py`.
- [x] `platform/ranking_engine/` -- replace the package with shims (`__init__`, `engine`, `metrics_store`, `price_series`, `volatility`) that re-export the surviving public names, `REMOVE_AFTER = "25-4-collection-control-plan-intent-vs-applied-set"`. Delete its tests.
- [x] `platform/ml_signals/` -- delete the package:
  - `git mv` the two TOMLs to `platform/data/{chart_indicators,screener_columns}.toml`, mounted at `/app/preferences/…`, with the data_api defaults updated.
  - `rank_history` and its test move to `research/`.
  - The `views/chart_series.py` AD-8 guard moves to `views/tests/test_ad8_boundary.py`.
- [x] `platform/views/coin_detail.py`, `views/__init__.py`, `data_api/tests/{test_data_api,test_metrics}.py` -- read through `ranking.application.queries`, and seed through `ranking.infrastructure.metrics_store`.
- [x] `platform/tests/test_boundaries.py` -- make these changes:
  - Set `THIS_STORY` to 25-2.
  - Drop the `ml_signals` map entries and package.
  - Retire the 25-2 `LEGACY_VENUE_HTTP_UNTIL`/`LEGACY_SNAPSHOT_INDEXING_UNTIL` entries.
  - Update `VIEWS_QUERY_SERVICES` → `ranking.application.queries`.
  - Update `NON_VENUE_HTTP_CLIENTS` → `research.rank_history`.
  - Update the forbidden-package sets.
  - Add `test_ranking_holds_no_module_level_runtime_state` (with a positive self-test).
  - Add a grep test that exactly one `def price_stats_from_series` exists.
- [x] Wiring and docs:
  - Compose: `command: python3 -m ranking`, plus the TOML mounts/env.
  - The collector/data_api dockerfiles COPY `ranking` and drop `ml_signals`.
  - `Makefile:149`: `ranking/tests`, no `ml_signals/tests`.
  - `ARCHITECTURE.md`; `CLAUDE.md` SSOT-02, step 7 and NAUT-03; `DATA_DICTIONARY.md` §3 paths.
  - `DEPLOY_CHECKLIST.md`: the VPS TOML-move step.
  - Strike the parent spine `:305` with `[amended 2026-09-26: Story 25.2]`.
  - `project-context.md` package list.

**Acceptance Criteria:**
- Given the moved code, when the platform suite runs, then there are no new failures or warnings against the baseline (3 Redis-dependent failures; ResourceWarning set unchanged), and `grep -rn ml_signals platform` hits only history notes.
- Given `ranking/`, when `test_boundaries.py` runs, then an injected module-level `dict`, `global` or env read in a ranking module is reported. The layering, venue-http and snapshot-indexing rules pass with no ranking exemption.
- Given the compose file, when `test_images.py` runs, then the `ranking_engine` service's `python3 -m ranking` closure and the data_api closure are satisfied by their dockerfiles' COPY sets.

## Spec Change Log

## Review Triage Log

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8 (high 0, medium 2, low 6)
- defer: 3 (high 0, medium 2, low 1)
- reject: 15 (high 0, medium 0, low 15)
- addressed_findings:
  - `[medium]` `[patch]` The dYdX volume parser still turned a missing/empty/NaN/negative `volume24H` into a ranked value (`or 0`), contradicting the new `VolumeSource` contract and DATA-01. It now goes through `parse_usd_volume` (ledgered and skipped). The test that pinned the old 0 is replaced by a parametrized skip-and-ledger test.
  - `[medium]` `[patch]` These sites now call `error_ledger.record` instead of only logging (DATA-07), under the new sites `ranking_engine.{message,publish,metrics_history,slow_loop,redis}`:
    - a message decode/publish failure;
    - a heartbeat publish failure;
    - the 1w/1m `metrics.db` lookup;
    - a slow-loop cycle failure;
    - the Redis listener reconnect.
  - `[low]` `[patch]` `age_out`'s docstring now states that a returning instrument starts fresh and is backfilled once more: one read per return, not the recurring read Story 13.2 removed.
  - `[low]` `[patch]` `RankingBoard`'s docstring claimed volatility mode never ranks on a fabricated 0. It now carries a `Known limit:` for the `volatility_score or 0.0` sort (kept for byte identity), with its upgrade path.
  - `[low]` `[patch]` The `metrics.db` readers return `[]`/`None` when the file exists but the writer has not created the table yet. Before, the data_api routes returned a 500 in that window. Test added.
  - `[low]` `[patch]` `__main__` now fails fast on a non-positive `RANKING_HEARTBEAT_SECONDS`/`RANKING_VOLATILITY_LOOKBACK_SECONDS`. `RankingConfig`'s false "validated in `__main__`" docstring is corrected.
  - `[low]` `[patch]` `__main__` now closes `SqliteMetricsStore` on shutdown.
  - `[low]` `[patch]` Stale doc references fixed: `DATA_DICTIONARY.md` §4 (`metrics_computer.compute_all`), in-app docs `kbData.ts` ("written once in ml_signals") and `data.ts` (a `ml_signals/dashboard.py` ref).

### 2026-09-26 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8 (high 0, medium 2, low 6)
- defer: 2 (high 0, medium 0, low 2)
- reject: 14 (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` data_api's per-call read-only `metrics.db` readers failed (`attempt to write a readonly database`, reproduced) once the writer's clean `close()` had checkpointed and deleted the `-wal`/`-shm` sidecars on the `:ro` mount. The old reader kept a long-lived connection whose lock kept the sidecars alive. `SqliteMetricsStore` now sets `SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`, so the sidecars outlive a close. A test was added for a read-only directory after the writer closes.
  - `[medium]` `[patch]` Four tolerated data drops in the domain only logged, breaking the spec's DATA-07 rule and the board's own "returns what to ledger" docstring. `RankingBoard.ingest`, `RankingBoard.backfill` and `PriceSeriesStore.ingest`/`backfill` now return them, and the engine ledgers them at the existing sites `ranking_engine.snapshot_entry`/`ranking_engine.price_backfill`. The four drops:
    - a non-finite or non-positive close_price;
    - an out-of-order price point;
    - a non-positive mid;
    - a live/Parquet backfill mismatch.
  - `[low]` `[patch]` Three docs still said a dYdX market with an absent/null `volume24H` reads as 0, which the previous pass's `parse_usd_volume` patch made false: `DATA_DICTIONARY.md` §3.3, `DATA_INTEGRITY_AUDIT.md` D-54 and the in-app `data.ts`. All three are corrected.
  - `[low]` `[patch]` `DATA_DICTIONARY.md` §3's amendment claimed every payload and store row was "proven byte-identical". It now states what `test_replay.py` actually covers (no backfill, no `age_out`) and names the two deliberate changes.
  - `[low]` `[patch]` A `snapshots:raw` payload that is not a list is now one `ranking_engine.message` failure, not one ledger entry per key or character. Test added.
  - `[low]` `[patch]` `SqliteMetricsStore.write` rolls back on failure, so a half-applied prune never stays open for the next commit. Test added.
  - `[low]` `[patch]` The `DEPLOY_CHECKLIST.md` Story 25.2 step now stops `data_api` before copying the TOMLs, so a UI save cannot land between the copy and the `git checkout`.
  - `[low]` `[patch]` Stale docs:
    - `DATA_DICTIONARY.md` §3.2: `rolling` holds `to_dict` rows, not decoded snapshots.
    - `kbData.ts`: the `metrics.db` column list now includes `pct_1w`/`pct_1m`.
    - A comment now marks `catalog_stats.mark_prices` as a published ledger site name.

## Design Notes

- **Why the extra decisions.** The `ml_signals` deletion forces three choices the story text does not spell out:
  - **The TOMLs move to `platform/data/`**, following the `dydx_config.toml` precedent: durable stores never live in a package directory.
  - **`rank_history` moves to `research/`.** Its only consumer is research, and research may not import `ranking`.
  - **`compute_all` is deleted, not moved.** It is Parquet I/O with no caller since 13.2.
- **One behaviour change: `age_out`.** Before, state for a dead instrument grew forever (MEM-02), and its stale values kept being written to `metrics.db` every minute (DATA-01). The rankings:live bytes are unaffected, because such ids already appear nowhere after 1 h.
- **The 45 s whole-fetch bound stays.** It is an application policy (a trickling body), not the socket timeout the kernel owns.
- **Replay determinism.** The burst is generated by a seeded function in the test, and its digest is pinned in the fixture. The clock is injected, so `time` is never patched in the new code.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ranking/tests ranking_engine bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -W default` -- expected: only the 3 baseline Redis failures.
- The scratchpad ruff/mypy venv (`ruff==0.15.16`, `mypy==1.20.2`) on `ranking/` -- expected: clean.


## Auto Run Result

Status: done

**Summary.** This is a follow-up review pass on the finished Story 25.2. Story 25.2 moved the ranking state into the `platform/ranking/` bounded context: `RankingBoard` in the domain, `RankingEngine` and its ports in `application/`, and the adapters in `infrastructure/`. `ranking_engine/` is left as shims and `ml_signals/` is deleted. This pass had two fresh reviewers (adversarial and edge-case). It fixed one runtime regression and closed a DATA-07 gap; the rest are small robustness and documentation fixes.

**Files changed in this pass:**
- `platform/ranking/infrastructure/metrics_store.py`: the WAL sidecars survive a clean writer close (`SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`), and a failed `write` rolls back.
- `platform/ranking/domain/board.py`, `domain/price_series.py`: `ingest`/`backfill` return what they drop rather than only logging it.
- `platform/ranking/application/engine.py`: ledgers those drops and rejects a non-list `snapshots:raw` payload as one failed message.
- `platform/ranking/infrastructure/catalog_prices.py`: a comment marking the published ledger site name.
- `platform/ranking/tests/{test_board,test_engine,test_metrics_store,test_price_series}.py`: tests for all of the above.
- Docs: `docs/DATA_DICTIONARY.md` (dYdX volume, replay coverage, `rolling`), `docs/DATA_INTEGRITY_AUDIT.md` D-54, `docs/DEPLOY_CHECKLIST.md` (stop data_api first), `frontend/src/pages/docs/{data,kbData}.ts`.

**Review findings:**
- 8 patches applied (2 medium, 6 low); see the second triage-log entry.
- 2 deferred to `deferred-work.md`:
  - the mark-price fallback can overflow the 1 Hz ring buffer;
  - the kbData overview paragraph is stale since 25.1a.
- 14 rejected. Some were already deferred, some are decisions the spec mandates (the 45 s bound, the shims, the `ml_signals` deletion, the value types), and the rest are pre-existing behaviour kept for byte identity.

**Verification:**
- Full platform suite (`python3 -m pytest -o addopts="" --rootdir=. … -W default`): 3 failed / 1688 passed. The 3 are the baseline Redis-dependent failures. The one `StarletteDeprecationWarning` is emitted at HEAD too; it comes from the installed fastapi/starlette versions, not from this change.
- The replay byte-identity test still passes.
- `ruff format`/`ruff check` are clean on `ranking/`. mypy shows the same 13 errors as HEAD, all in `kernel/`.
- The read-only-mount failure was reproduced in a scratch script before the fix, and the new test covers it.

**Residual risks:**
- The newly ledgered drops (`ranking_engine.snapshot_entry` for a bad close/mid or an out-of-order point) may appear on `/api/errors` after deploy. Each one is a real data defect that was previously invisible, not noise.
- `NO_CKPT_ON_CLOSE` leaves the `-wal` file on disk after shutdown. It is bounded by auto-checkpoint and replayed on the next open.
- The VPS TOML step in `DEPLOY_CHECKLIST.md` still has to be run by hand.
