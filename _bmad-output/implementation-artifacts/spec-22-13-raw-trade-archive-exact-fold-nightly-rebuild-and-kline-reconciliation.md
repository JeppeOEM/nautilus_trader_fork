---
title: 'Story 22.13: Raw trade archive, exact fold, nightly rebuild and kline reconciliation'
type: 'feature'
created: '2026-09-21'
status: done
baseline_revision: '2266e6753d9c2f50a05202e3344602c4b2dd5c31'
final_revision: '8f78dd6963fc8bf1b798feca96d67450d0cc40f4'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/22-13-raw-trade-archive-exact-fold-nightly-rebuild-and-kline-reconciliation.md'
  - '{project-root}/troll/CLAUDE.md'
warnings: [oversized]
operator_actions:
  - "On nifelheim, deploy this branch with `make redeploy-all` plus `docker compose up -d --build bybit_collector hyperliquid_collector`, so all three collectors start archiving trades."
  - "After the first full UTC day with the new collectors, measure the trade_tick footprint per venue (`du -sh troll/dydx_collector/catalog/data/trade_tick/*.<VENUE>` summed per venue for that day) and record it in troll/docs/DATA_INTEGRITY_AUDIT.md D-45."
  - "Run `make nightly VENUE=DYDX`, `make nightly VENUE=BYBIT` and `make nightly VENUE=HYPERLIQUID` by hand for that first full day (DAY=YYYY-MM-DD). Copy each nightly summary line (per-step seconds, peak child RSS) and the before/after file counts into the audit (D-45/D-51 and troll/docs/DEPLOY_CHECKLIST.md), and confirm the peak RSS stays inside the box's free memory (MEM-01)."
  - "Record each venue's compare_klines pass rate (instruments and minutes) in DATA_INTEGRITY_AUDIT.md D-51, and root-cause every remaining mismatch there (reconnect gap -> 22.14, or a named finding). Never add a tolerance."
  - "Install the nightly cron line from troll/docs/DEPLOY_CHECKLIST.md in the VPS host crontab (CRON_TZ=UTC if the box is not on UTC), replacing the Story 22.11 consolidate-only line."
  - "Record a full day of Hyperliquid trade arrival lag (ts_init - ts_event) in audit D-59 to confirm the 10 s stale-trade filter is not dropping live trades."
---

<intent-contract>

## Intent

**Problem:** Every `TradeTick` is folded into the live 1 s snapshot with float sums and discarded (D-45, D-46), so a fold error, a late trade or a boundary misattribution (D-31, D-44) can never be corrected, and nothing compares our bars with the venues' own klines (D-51).

**Approach:** Archive every accepted trade (both clocks) through `write_data()`, replace the live float accumulators with one pure integer fold (`collector_core/fold.py`) shared by the live loop and a day-scoped rebuild that re-derives snapshot trade fields from the archive on exchange time, reconcile the rebuilt 1 m bars bar-for-bar against each venue's klines into `verified_days`, gate trade retention on `pass`, and chain it all in one nightly job. VPS measurements are operator actions → story ends `awaiting-operator`.

## Boundaries & Constraints

**Always:** `DydxSecondSnapshot` Arrow schema unchanged; trade `ts_event`/`ts_init` stored untouched; volume summed as `Quantity.raw` integers, OHLC chosen by `Price.raw`, one float conversion at the snapshot boundary; rebuild touches only trade columns (`buy_volume`, `sell_volume`, `buy_count`, `sell_count`, `open/high/low/close_price`) of rows inside the day, never book columns or timestamps, temp-then-rename with row count and full-schema (metadata included) checks; today refused unless `--include-open-day`; one instrument-day in memory at a time (MEM-01); every refusal/failure → `error_ledger` + non-zero exit (DATA-07); kline comparison exact (integer units), no tolerance parameter anywhere (DATA-02); kline values parsed from the venue's decimal strings (never through float); real Nautilus objects + real tmp catalog in tests (TEST-01/03); report-only by default for every catalog-mutating CLI.

**Block If:** trades cannot be written/read through `ParquetDataCatalog` without changing `nautilus_trader/` or `crates/`.

**Never:** modify `nautilus_trader/`, `crates/`, `data_api`, `frontend`, `ranking_engine`; add a column to the snapshot or `candles` table; drop, filter or tolerate a mismatch; fold backfilled/late trades live; float-sum volumes in the fold.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Archive | trade passes age + dedup | folded into the live second AND buffered; flush writes `data/trade_tick/<iid>/` | none |
| Flush boundary tie | batch's newest `ts_init` group is < 5 s old (a WS message may be split across the flush) | that group carried to the next flush; final (shutdown) flush writes everything | clock regression → existing `collector.flush_write` LOST path |
| Fold exactness | sizes 0.1 + 0.2 at 8 dp | `Quantity` == `Decimal("0.3")` exactly | mixed size precisions → result at the max precision (exact) |
| Late trade | trade `ts_event` in S, arrived at S+2 (live folded it into S+2's row) | rebuild moves it to the row of second S; S+2 row cleared | none |
| Second not covered | row before the instrument's first archived trade file | keeps live values, counted `not covered` | none |
| Orphan trade | trade second has no snapshot row (collector not sampling) | counted + reported (`orphan trades`), never silently dropped | none |
| Duplicate archived trade | same `trade_id` twice (restart replay) | counted once, reported `duplicates` | none |
| Two rows one second | two snapshot rows share a floor second | instrument-day refused | `rebuild.duplicate_second`, exit 1 |
| Mixed schema day | files with differing full schema / missing OHLC columns | instrument-day refused, files unchanged | `rebuild.mixed_schema`, exit 1 |
| Rerun | same day rebuilt again | `changed 0`, no file rewritten | none |
| Kline equal | venue traded minutes == our `window(iid, 60)` bars | `verified_days` pass | none |
| Kline differs / missing bar either side | one minute differs | `fail`, one `reconcile.kline_mismatch` per minute, exact message format | exit 2 (findings) |
| Venue zero-volume kline | dYdX/Bybit emit no-trade minutes | ignored (no trade either side) | none |
| Fetch error / no instrument definition / value not representable at instrument precision | — | no `verified_days` row (stays unverified) | `reconcile.error`, exit 1 |
| Trade prune | (iid, day) older than N: pass / fail / no row / younger | delete / keep "failed" / keep "unverified" / keep silently | report lists kept (iid, day, reason) |

</intent-contract>

## Code Map

- `troll/collector_core/collector.py` -- `_process_data` (:455 float accumulators), `_discard_second_accumulators` (:515), `_flush_once` (:533), `_sample_tick` (:612), `_second_loop` (:699 drifting `sleep(interval)`), `run` final flush (:879).
- `troll/collector_core/second_snapshot.py` -- schema (unchanged); docstring says trades not persisted → update.
- `troll/collector_core/build_candles.py` -- `all_instruments`, `_files_by_day`, `rebuild_instrument`, `_parse_date_ns`; CLI `--start/--end`.
- `troll/collector_core/consolidate_catalog.py` -- `leaf_dirs`, `run`, `_LOCK_NAME` flock, `_TMP_SUFFIX` precedent; walks `trade_tick` leaves already.
- `troll/collector_core/repair_catalog.py` -- `ohlc_outside_book`-based repair; must not be run on rebuilt days (exchange-timed trades vs mid-second book).
- `troll/dydx_collector/prune_catalog.py` (+ `dydx_collector/tests/test_prune_catalog.py`) -- to move to `collector_core`; imported by `dydx_collector/collector.py:78`; Makefile `prune`/`prune-dry` (:273-282).
- `troll/ml_signals/candle_store.py` -- `_SCHEMA`, `connect_rw`/`connect_ro`, `window`, `_fold`/`fold_arrays` (seconds→bars).
- `troll/ml_signals/catalog_stats.py` -- `_stamp_to_ns`, `data_file_ranges`; `price_series` docstring claims no trades persisted.
- `troll/ml_signals/error_ledger.py` -- `record(site, detail, exc)`.
- `troll/collector_core/backfill_bars.py` -- `-1-MINUTE-LAST-EXTERNAL` bars stamped at close (D-52 f64 caveat).
- `troll/dydx_collector/open_interest.py`, `troll/hyperliquid_collector/book_snapshot.py` -- stdlib `urllib` precedents (UA header, base URLs, `get_dydx_http_url`).
- `troll/dydx_collector/tests/test_collector_trade_ohlc.py`, `troll/collector_core/tests/test_collector.py` -- tests on the live accumulators.
- Wire formats (verified 2026-09-21): dYdX `GET /v4/candles/perpetualMarkets/{ticker}?resolution=1MIN&fromISO&toISO&limit` → `candles[{startedAt, open, high, low, close, baseTokenVolume, trades}]` newest first; Bybit `GET /v5/market/kline?category&symbol&interval=1&start&end&limit=1000` → `result.list[[startMs,o,h,l,c,volume,turnover]]` newest first; Hyperliquid `POST /info {"type":"candleSnapshot","req":{coin,interval:"1m",startTime,endTime}}` → `[{t,o,h,l,c,v,n}]`. Venue symbol = the catalog instrument's `raw_symbol`; Bybit category from the id's `-LINEAR`/`-SPOT` suffix.

## Tasks & Acceptance

**Execution:**
- [x] `troll/collector_core/fold.py` -- NEW: frozen `SecondTradeFields` (`open/high/low/close_price: Price | None`, `buy_volume/sell_volume: Quantity | None`, `buy_count/sell_count: int`) with `snapshot_values()` (the one float conversion: `as_double()`, 0.0/None when empty); pure `fold_trades(trades)` -- stable order by `ts_event` (ties keep input order), high/low by `Price.raw`, volumes summed on `size.raw` and returned via `Quantity.from_raw(total, max precision seen)`.
- [x] `troll/collector_core/collector.py` -- accumulators → `self._second_trades: defaultdict[str, list[TradeTick]]`; `_process_data` appends each accepted trade to it AND to `self._buffer[(TradeTick, iid)]`; `_sample_tick` folds via `fold_trades` (empty → today's None/0 contract) and drops lists of instruments it did not sample (MEM-02); `_discard_second_accumulators` pops the list; `_flush_once(final=False)` sorts each batch by `ts_init` (stable) and, for `TradeTick` batches when not final, carries back the group sharing the batch's max `ts_init` if it is < 5 s old; `run` calls `_flush_once(final=True)`; `_second_loop` sleeps to a drift-free schedule (`_next_sample_at(now, interval, last_tick)`: the next `k*interval + interval/2`, never two ticks in one interval bucket), lag canary kept. Docstrings updated.
- [x] `troll/collector_core/rebuild_seconds.py` -- NEW CLI `--catalog --day [--instrument ...] [--venue] [--apply] [--include-open-day]`: per instrument, snapshot files overlapping D (full-schema check), trades loaded hour by hour via `ParquetDataCatalog.query(TradeTick, ...)` on `ts_init` with a 5-minute margin then filtered on `ts_event` in the hour, deduped by `trade_id`, bucketed `ts_event // 1e9`; rows at or after the instrument's first `trade_tick` file (`covered_from`) get `fold_trades(bucket).snapshot_values()`, earlier rows keep live values; per-file rewrite `<name>.rebuild.tmp` → schema equals original (metadata incl.) → row count equal → `os.replace`; takes the consolidation flock; one line per instrument (`rebuilt, changed, without trades, not covered, orphan trades/seconds, duplicates`) + summary; exit 1 on any refusal.
- [x] `troll/collector_core/build_candles.py` -- add `--day` (start = end = day) and `--venue` (id suffix filter); expose a `venue_instruments(ids, venue)` helper reused by the new tools.
- [x] `troll/collector_core/consolidate_catalog.py` -- add `--venue` (leaf name suffix `.VENUE`); export the lock name for reuse.
- [x] `troll/ml_signals/candle_store.py` -- `verified_days` table in `_SCHEMA`; `mark_verified(db, iid, day, status, mismatches, checked_at_ms)` (upsert), `verified_status(db, iid, day) -> str | None`; `_fold` docstring points to `collector_core/fold.py` for trades→seconds.
- [x] `troll/collector_core/compare_klines.py` -- NEW CLI `--venue --day [--instrument ...] [--db] [--catalog] [--environment mainnet|testnet] [--kline-source venue|catalog]`: `Kline(t_ms, o, h, l, c, v)` raw ints at instrument precision; `fetch_klines(venue, instrument, day, environment)` stdlib per venue (paged, zero-volume dropped, exact `Decimal.scaleb` parse, non-integral → error); `catalog` source reads the `-1-MINUTE-LAST-EXTERNAL` bars (open = `ts_event` - 60 s); ours from `window(db, iid, 60, day_end_ms, 1440)` converted with an exact-residual guard; instruments = venue ids with snapshot or trade files on D; per mismatch `error_ledger.record("reconcile.kline_mismatch", f"{iid} {minute} vol {ours}/{theirs} ohlc {ours}/{theirs}")` (`-` for a missing side); upsert `verified_days`; print per-instrument and per-venue pass rate (instruments and minutes); exit 0 / 2 findings / 1 errors.
- [x] `troll/collector_core/prune_catalog.py` -- `git mv` from `dydx_collector/` (+ its test to `collector_core/tests/`); update `dydx_collector/collector.py` import; CLI becomes report-only unless `--apply` (`--dry-run` kept, conflicts with `--apply`); `trade_tick` refused in `--types`; `--trade-retention-days N` (default 7) + `--candles-dir` (per-venue `candles_<venue>.db`) + `--venue`: deletes a trade file only when every UTC day its name spans is older than N days and `pass` for that instrument, else reports `unverified`/`failed`; takes the maintenance flock.
- [x] `troll/collector_core/nightly.py` -- NEW: `--catalog --candles-dir --venue --day`, runs each step as a subprocess in order (rebuild `--apply` → consolidate `--apply --venue` → build_candles `--day --venue` → compare_klines → prune `--apply --trade-retention-days 7 --venue`), stops at the first exit other than 0 (compare's 2 = findings, continues), `error_ledger.record("nightly.<step>")` for failures/findings, one summary line (per-step seconds, peak child RSS, outcome); exit code of the failing step.
- [x] `troll/Makefile` -- `nightly` target (`VENUE` required, `DAY ?=` yesterday UTC) running `collector_core.nightly` in the collector image; `prune`/`prune-dry` → `python3 -m collector_core.prune_catalog` with `--apply` / report-only in the image; `.PHONY`.
- [x] Tests (NEW `collector_core/tests/test_fold.py`, `test_rebuild_seconds.py`, `test_compare_klines.py`, `test_nightly.py`; extended `test_collector.py`, `test_consolidate_catalog.py`, moved `test_prune_catalog.py`, `ml_signals/tests/test_candle_store.py`; migrated `dydx_collector/tests/test_collector_trade_ohlc.py` asserts through the fold) -- every I/O-matrix row; fold equivalence on 10k real `TradeTick`s (exact vs `Decimal`, within one unit of the last decimal place vs the old float fold); flushed trade readable via `catalog.trade_ticks` with equal clocks and via `BacktestDataConfig(data_cls=TradeTick)`; trade leaf consolidated; drift-free schedule; kline parsers on recorded JSON; nightly stop-at-failure with fake steps.
- [x] Docs -- `troll/docs/DATA_DICTIONARY.md` (`trade_tick` entry, retention `Known limit:` 7 days + upgrade path, rebuild/compare/nightly); new `troll/docs/DEPLOY_CHECKLIST.md` (nightly cron line, first-run measurements owed); `troll/README.md` Nightly maintenance → nightly; `troll/CLAUDE.md` DATA-01 (two clocks) + DATA-05 (rebuild fills live-fold errors); `troll/docs/DATA_INTEGRITY_AUDIT.md` D-45/D-46 fixed, D-31/D-44 fixed for trades by the rebuild (closes on first verified day), D-51 written/owed, new rows for the flush tie and the sampling drift; `troll/ARCHITECTURE.md` catalog tools line; `repair_catalog.py` docstring warning.

**Acceptance Criteria:**
- Given the collector image, when `pytest collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests` runs, then every new/changed test passes and failures equal the pre-existing baseline set.
- Given each venue's live collector run briefly against mainnet into a scratch catalog, when its trade leaf is read back, then real `TradeTick`s round-trip with `ts_event` ≤ `ts_init` untouched.
- Given the three venues' public kline endpoints, when `fetch_klines` runs for one closed day per venue, then it returns parsed exact klines (wire-verified).
- Given VPS measurements are owed, when the story ends, then the spec is `awaiting-operator` with footprint, first nightly run (wall/RSS/files), pass rates and cron install under `operator_actions`.

## Spec Change Log

## Review Triage Log

### 2026-09-21 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 18 (high 1, medium 7, low 10)
- defer: 1
- reject: 3
- addressed_findings:
  - `[high]` `[patch]` Rebuild could zero correct live trade values when the archive lacks trades the live fold used (failed trade write, crash after a carried group, quarantined trade file, pruned day inside the covered range). Carried trade groups now carry their snapshot rows too; persistent archive-gap markers (`collector_core/archive_gaps.py`, reasons write_failed/quarantined/pruned) make those rows "not covered" (live values kept, trades counted as orphans).
  - `[medium]` `[patch]` One bad instrument halted the whole venue's nightly forever: rebuild/compare exit 2 for per-instrument refusals/errors/mismatches (ledgered, nightly continues), 1 only for run-level failures; nightly consolidates `--days 2`; standalone `make consolidate` kept in cron.
  - `[medium]` `[patch]` `--kline-source catalog` (D-52 f64 evidence) could write `pass` and unlock prune: it no longer writes `verified_days`.
  - `[medium]` `[patch]` Hyperliquid days outside candleSnapshot retention / empty venue response produced a false `fail`: now a per-instrument "no history" error.
  - `[medium]` `[patch]` Flush tie guard missed queue lag > 5 s: the newest group is also carried while the ingest queue is non-empty.
  - `[medium]` `[patch]` Prune gated on ts_init file days could delete a previous day's boundary trades: files starting within the 5-min arrival margin after midnight also need the previous day verified.
  - `[medium]` `[patch]` Nightly build_candles used every CPU: runs `--workers 1` (MEM-01).
  - `[medium]` `[patch]` compare_klines crashed on `http.client.HTTPException`/`TypeError`/`sqlite3.OperationalError`: now per-instrument errors.
  - `[low]` `[patch]` Docs: re-running a missed day, lock semantics, Known limit on indefinite retention of failed/unverified trade days; `collector.cadence` ledger + Known limit for a non-1 s cadence; orphans reported for a day with trades but no snapshots; zero-row and unparseable trade files handled; typed `SnapshotTradeValues` replaces a `type: ignore` spread and the import order was fixed; Bybit previous-close seed chaining documented and labelled in mismatch messages.

## Design Notes

Coverage: live and archive are written by the same flush, so every live-folded trade is archived unless the archive did not exist yet. Rows before the instrument's first trade file therefore keep live values; the literal "second with no archived trades keeps live values" would double-count every late trade (its arrival second has no exchange-time trade).

Floor-second mapping needs exactly one row per second: `sleep(1.0)` drifts ~ms per tick and skips a floor second every few hundred seconds, orphaning its trades. Fixed phase at mid-second (± 0.5 s wake margin). 22.12 will re-derive sampling on venue time.

Candle store `v` is a REAL sum; comparison rounds `Decimal(float).scaleb(p)` to an integer only when the residual is < 0.001 unit (a representation guard, not a comparison tolerance: two volumes a unit apart never compare equal). `Known limit:` exact while a bar's volume stays far below 2^53 raw units; upgrade path: integer volume column.

Kline source: the story names Bybit's pyo3 `request_bars`, but that path parses every kline value through `f64` (D-52), which cannot prove raw-unit equality; all three venues are fetched with stdlib and parsed from the decimal strings. The EXTERNAL-bar source stays available, labelled with the D-52 caveat.

Compare exit 2: a kline mismatch is a finding already recorded in `verified_days` (and it gates prune); halting the nightly on it would stop pruning forever while 22.14 gaps remain.

## Verification

**Commands:**
- `docker run --rm -v "$PWD":/app -w /app -e HOME=/tmp -e USER=collector troll-collector:latest python3 -m pytest collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests -q` (from `troll/`) -- expected: new tests pass, only baseline failures remain
- `ruff check` + `ruff format --check` + `mypy --disallow-incomplete-defs` on new/changed Python -- expected: clean
- `make -n nightly VENUE=BYBIT` -- expected: renders with yesterday's date

## Auto Run Result

Status: awaiting-operator

**Summary:** Every accepted trade on dYdX, Bybit and Hyperliquid is now archived raw to `data/trade_tick/` with both clocks untouched. One exact integer fold (`collector_core/fold.py`) replaces the live float accumulators and is shared with a day-scoped rebuild (`rebuild_seconds`). The rebuild re-derives each closed day's snapshot trade columns on exchange time into half-open [S, S+1) seconds. `compare_klines` then reconciles the rebuilt 1 m bars bar for bar against each venue's own klines into `verified_days`. Trade retention is gated on `pass`, and `make nightly VENUE= DAY=` chains all the steps. Four supporting fixes came out of investigation and review:
- A flush tie guard, because `write_data` refuses a file whose `ts_init` interval touches an existing file's, which loses the whole batch.
- Drift-free 1 s sampling, because the old loop skipped a floor second every few hundred seconds.
- Persistent archive-gap markers, so the rebuild can never zero live values the archive cannot back.
- Stdlib, string-exact kline fetchers for all three venues, because the pyo3 Bybit path goes through `f64` (D-52).

A wire finding: Bybit's kline open is the previous close, measured 999/999. Our Bybit bars are seeded the same way, with exact integers (audit D-58).

**Files changed:**
- `troll/collector_core/fold.py` (new): the exact trades-to-second fold.
- `troll/collector_core/rebuild_seconds.py` (new): the closed-day rebuild from the trade archive.
- `troll/collector_core/compare_klines.py` (new): venue kline fetchers, exact comparison, `verified_days`.
- `troll/collector_core/nightly.py` (new): the five-step nightly chain, run as subprocesses.
- `troll/collector_core/archive_gaps.py` (new): lost, quarantined and pruned archive-span markers.
- `troll/collector_core/prune_catalog.py` (moved from `dydx_collector/`): report-only by default, gated trade retention.
- `troll/collector_core/collector.py`: trade archive, fold, flush tie guard (carries rows too), drift-free sampler, cadence canary.
- `troll/collector_core/build_candles.py`: `--day` and `--venue` options.
- `troll/collector_core/consolidate_catalog.py`: `--venue` option and a shared lock.
- `troll/ml_signals/candle_store.py`: the `verified_days` table and its helpers.
- Docstring fixes: `second_snapshot.py`, `repair_catalog.py`, `catalog_stats.py`.
- `troll/dydx_collector/collector.py`: import update; its prune loop skips `trade_tick`.
- Tests:
  - new: `test_fold.py`, `test_rebuild_seconds.py`, `test_compare_klines.py`, `test_nightly.py` and fixtures (real venue JSON);
  - extended: collector, consolidate, prune, candle_store and build_candles tests;
  - migrated: `test_collector_trade_ohlc.py`.
- Build and docs:
  - `troll/Makefile`: `nightly` target; `prune` now runs in the image;
  - `troll/docs/DEPLOY_CHECKLIST.md` (new);
  - `DATA_DICTIONARY.md`, `DATA_INTEGRITY_AUDIT.md` (D-45, D-46, D-31, D-44, D-51, new D-56 to D-59), `README.md`, `ARCHITECTURE.md`, `troll/CLAUDE.md` (DATA-01, DATA-05).

**Review:** 18 patches applied (1 high, 7 medium, 10 low), 1 deferred (dYdX `_prune_loop` takes no maintenance lock; pre-existing), 3 rejected (the tie order of equal-`ts_init` rows returned by `query`, which the live evidence contradicts; private-helper imports; the test's jitter model).

**Verification:**
- Tests in the collector image, all troll test dirs: 980 passed. The only failures are the ones the untouched baseline also has: 4 `test_ofi_strategy*`, 1 `data_api` `test_rankings_live_message_reflected_by_rest_and_ws_relay`, and 3 collection errors.
- `ruff check` and `ruff format --check` are clean on new and changed files; the only remaining findings were already there before this story.
- `mypy --disallow-incomplete-defs` reports 0 errors on the changed files.
- `make -n nightly VENUE=BYBIT` renders with yesterday's date.
- Live mainnet check (scratch catalog, about 200 s, 7 instruments across the 3 venues): 22,037 trades round-tripped with `ts_event <= ts_init`, and `BacktestNode` loaded them.
- Kline fetchers wire-verified for 2026-09-20 on all 3 venues.
- End to end: 19 fully collected minutes matched the venue klines exactly after `rebuild_seconds` (the live fold alone mismatched 18 of the 19). The real nightly chain exited 0 with a peak child RSS of 250 MB.

**Residual risks:**
- Nothing has been measured on the VPS yet: footprint, nightly wall time and RSS, and pass rates (operator actions).
- Reconnect gaps will fail reconciliation until 22.14, and failed days keep their trades indefinitely (documented `Known limit:`).
- A realtime clock step backwards can still lose a trade flush; it is loud (`collector.flush_write`) and leaves an archive-gap marker.
- A snapshot cadence other than 1 s is only canaried, not supported.
- The live sampler's phase moved to mid-second, and 22.12 will revisit sampling.

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- On nifelheim, deploy this branch with `make redeploy-all` plus `docker compose up -d --build bybit_collector hyperliquid_collector`, so all three collectors start archiving trades.
- After the first full UTC day with the new collectors, measure the trade_tick footprint per venue (`du -sh troll/dydx_collector/catalog/data/trade_tick/*.<VENUE>` summed per venue for that day) and record it in troll/docs/DATA_INTEGRITY_AUDIT.md D-45.
- Run `make nightly VENUE=DYDX`, `make nightly VENUE=BYBIT` and `make nightly VENUE=HYPERLIQUID` by hand for that first full day (DAY=YYYY-MM-DD). Copy each nightly summary line (per-step seconds, peak child RSS) and the before/after file counts into the audit (D-45/D-51 and troll/docs/DEPLOY_CHECKLIST.md), and confirm the peak RSS stays inside the box's free memory (MEM-01).
- Record each venue's compare_klines pass rate (instruments and minutes) in DATA_INTEGRITY_AUDIT.md D-51, and root-cause every remaining mismatch there (reconnect gap -> 22.14, or a named finding). Never add a tolerance.
- Install the nightly cron line from troll/docs/DEPLOY_CHECKLIST.md in the VPS host crontab (CRON_TZ=UTC if the box is not on UTC), replacing the Story 22.11 consolidate-only line.
- Record a full day of Hyperliquid trade arrival lag (ts_init - ts_event) in audit D-59 to confirm the 10 s stale-trade filter is not dropping live trades.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
