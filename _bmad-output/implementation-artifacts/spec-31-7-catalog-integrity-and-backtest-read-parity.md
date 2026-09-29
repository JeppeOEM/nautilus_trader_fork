---
title: 'Story 31.7: Catalog integrity and backtest-read parity'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: '9626ad42c7'
final_revision: 'ca9254ec20'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Nothing proves the catalog is internally consistent, or that a backtest sees exactly the stored rows. Readers never dedupe, so a duplicate or overlapping file reaches every consumer. D-24 showed that a second schema silently drops columns through `pds.dataset`. Consolidation rewrites every file with only a row-count check. The candle store's bars were never compared with the rows a strategy reads.

**Approach:** Add `python3 -m verification.catalog --venue V --day D`, which checks four things over one closed UTC day:
1. **Structure:** schema classes, file intervals, sort order, snapshot duplicates, and every file opening through `ParquetDataCatalog`.
2. **Consolidation rehearsal:** the archive's real intraday and daily consolidation run on a linked scratch copy, and a per-type order-independent row digest must be identical before and after.
3. **Backtest-read parity:** per instrument, the TradeTick and snapshot rows a `BacktestNode` actor receives must equal the stored rows and an hourly bounded `catalog.query`, by count and digest.
4. **Candle parity:** the candle store's bars must equal the reference fold of the received snapshot rows.

Nautilus, archive and the snapshot codec are driven only from a new `verification/subject/` package. They are the code under test, never part of the oracle.

## Boundaries & Constraints

**Always:**
- **Oracle/subject split (DATA-02):**
  - **Oracle:** `verification/domain/catalog_check.py` (pure) and `verification/infrastructure/catalog_scan.py` (raw pyarrow, stdlib `sqlite3`).
    - Its own file-name parser: `YYYY-MM-DDTHH-MM-SS-<9 digits>Z_<same>`, UTC ns, inclusive `ts_init` span. It does not use `kernel.clocks`.
    - The digest.
    - The reuse of `reference_signals.fold_candles`/`RefBook.from_stored` and `signal_compare.at_places`.
  - **Subject:** new package `verification/subject/`, holding `nautilus_reads.py`, `backtest_probe.py` and `consolidation.py`.
    - It is the only non-test verification code that may import `nautilus_trader`, `kernel.second_snapshot`, `kernel.open_interest` and archive consolidation.
    - It may import `verification.domain`, so both sides use one digest.
    - It is imported only by the `verification.catalog` root.
    - It never imports `capture`, `candles`, `ranking`, `views`, `research`, `bots`, or `nautilus_pyo3` directly.
  - `application/catalog.py` declares the subject's ports as `Protocol`s and never imports the subject.
- **Boundary tests** (`tests/test_boundaries.py`):
  - The transitive DATA-02 walk and the allowlist walk stop at `verification.subject`.
  - New tests:
    - the subject is imported only from `SUBJECT_ROOTS = {"verification.catalog"}`;
    - the subject's own transitive reach avoids the denied contexts listed above;
    - a runtime probe: importing `verification.application.catalog`, `verification.domain.catalog_check` and `verification.infrastructure.catalog_scan` loads no `nautilus_trader` and no denied module.
  - `COMPOSITION_ROOTS["verification.subject.consolidation"] = {ARCHIVE}`.
  - `verification.catalog` is added to `VERIFICATION_ROOTS`.
  - The existing probe stays unchanged for the other roots.
- **Scope:** the plan instruments (`plan_of`) and every `<catalog>/data/<type>/` directory holding a leaf for one. The day files of a leaf are those whose name span intersects `[D − M, D+1 + M)`, with `M = READ_MARGIN_NS = 60 s`. The measured `ts_init − ts_event` on the soak was: snapshots 1.000-1.006 s (Bybit) and 3.000-3.005 s (Hyperliquid); trades ≤ 9.8 s. Write this justification beside the constant.
- **Row digest:**
  - Per row, `blake2b(digest_size=16)` over `repr` of the `(column name, value)` pairs, sorted by name. Values come from pyarrow `to_pylist()`, where a dictionary column yields its string.
  - The multiset digest is `(count, Σ digests mod 2**128)`, taken over every column of the stored file.
  - The subject re-encodes the objects it receives with `ArrowSerializer.serialize_batch`, the serializer the catalog writes with. It projects them to the stored column names; a missing column is `read_mismatch`.
  - Validated in planning: re-encoded query rows equal the stored rows for `trade_tick` and `custom_dydx_second_snapshot`.
- **Structure classes.** A failing count > 0 fails the day.
  - Per leaf, all failing:
    - `bad_name`;
    - `overlap`: pairs of day files whose inclusive spans intersect;
    - `name_span`: the name ≠ `[min, max]` of the file's `ts_init`;
    - `unsorted`: `ts_init` decreases within a file;
    - `empty`;
    - `null_ts`;
    - `open_failed`: `ParquetDataCatalog.query(cls, identifiers=[iid], files=[f], start, end)` raises, in hour windows over the file's span;
    - `open_count`: the decoded total ≠ the file's rows.
  - `unknown_type` (fails): a type directory the subject has no class for. Its class map is built from the Nautilus classes through Nautilus's own file naming, not hand-typed.
  - Per type: `schemas`, where the signature is the ordered (name, Arrow type) list plus the sorted metadata **keys**. Metadata values carry per-instrument precisions, and 31.6 checks the labels. More than one class fails, and the report lists each class with its file count and one path.
  - `custom_dydx_second_snapshot`: `duplicate_ts_event` counts extra rows sharing `(iid, ts_event)` among rows with `ts_event` in D.
- **Consolidation rehearsal:**
  1. Hardlink the day files into a `TemporaryDirectory` under `--scratch-dir`, falling back to a copy. The default is `<VERIFY_DATA_DIR>/scratch`. Keep the `data/<type>/<iid>/` layout.
  2. Take digest H0.
  3. Run `archive` `run_closed_hours(writer, scratch, now_ns=D_end − 1)` and take H1.
  4. Run `run(writer, scratch, None, None, True, now_ns=clock)` and take H2. The writer comes from `maintenance(scratch)`.

  Per type, report rows, file counts at each stage and the digests.
  - `identical`: all three digests are equal and a file was rewritten.
  - `not_exercised`: no file changed. This is printed and does not fail.
  - `different` fails, as do `leaves_failed` or `days_refused` > 0.
  - The writer is injectable, for the planted test.
- **Backtest parity:**
  - One `BacktestRunConfig` per plan instrument, all in one `BacktestNode(...).run()`:
    - `BacktestDataConfig` for `TradeTick` and `"kernel.second_snapshot:DydxSecondSnapshot"` (`client_id` = venue);
    - `start_time = D − M`, `end_time = D+1 + M − 1`, bounded on `ts_init`;
    - `chunk_size` streaming;
    - a `BacktestVenueConfig` as in `archive/tests/test_catalog_files_backtest.py`;
    - logging bypassed and `dispose_on_completion=False`.
  - The recording actor is loaded by `ImportableActorConfig` string path and read back from the engine's trader afterwards. There is no module-level state.
  - Rows with `ts_init` in `[D, D+1)` go into the parity digest.
  - Only the trade columns of the snapshots with `ts_event` in D are kept, for the fold.
  - The same count and digest are computed for:
    - `stored`: an oracle raw read by `ts_init`;
    - `query`: 24 hourly `catalog.query(cls, identifiers=[iid], start=h, end=h+1h−1)`;
    - `received`.

    Any disagreement is `read_mismatch` (fails), naming the pair that differs.
  - `beyond_margin` fails: a row whose `|ts_init − ts_event| > M`, so the margin can never hide a row.
- **Candle parity:**
  - The store is `<--candles>/candles_<venue lowercased>.db`, where `--candles` defaults to env `CANDLES_DIR`, else `<catalog>/../candles`. It is opened read-only (`mode=ro`).
  - Compare the rows of the instrument with `t` in D for the widths `STORE_BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)`, cited from the dictionary's candle section. A stored width outside the tuple is `unknown_width` (fails).
  - The reference is `fold_candles` over the received rows. Each `RefBook` is built from the trade columns, with empty book lists because the fold reads only trade columns; comment this.
  - Per bucket:
    - o/h/l/c are compared with `at_places(stored, ref, price_precision)`, and v with `at_places(..., size_precision)`;
    - `seconds_observed` must be int-equal.

    The places are the maximum precision among the bucket's rows.
  - Classes:

    | Class | Fails |
    |---|---|
    | `exact` | no |
    | `float_noise` | no; reported apart as a DEVIATION, per the `candles/domain/fold.py` Known limit |
    | `both_undefined` | no |
    | `different` | yes |
    | `undefined_mismatch` | yes |
    | `missing` | yes |
    | `extra` | yes |
- **Refusals** are ledgered at the new site `verification.catalog.refused` (`sites.py`, `test_sites.py` `_PREFIXES`):
  - a day that is not closed;
  - a missing catalog or candles directory, or a missing store file;
  - a plan instrument without a stored definition;
  - a day file vanishing mid-run: "catalog changed during the check (maintenance ran?)";
  - an uncreatable scratch directory.

  Crashes are recorded at the same site and re-raised. Exit 0 when every failing count is 0, else 1.
- **Code rules:**
  - The house CLI pattern (`derivs.py`): `--venue --day [--json] [--catalog] [--raw-dir] [--candles] [--scratch-dir]` and `main(argv, clock)`.
  - The LGPL header, full typing, functions of about 30 lines or fewer, complexity ≤ 10, no module-level mutable state.
  - Memory: streaming batches on the oracle side. The subject side holds one hour window or chunk of objects at a time, plus ≤ 86,400 trade-column tuples per instrument. Write a `Known limit:` comment with the measured peak and runtime.

**Block If:**
- The subject side proves to need `capture`, `candles`, `ranking` or `views` code.
- An oracle module would need Nautilus.

Either one changes the DATA-02 exemption itself and needs the operator.

**Never:**
- Never modify `nautilus_trader/`, `crates/`, `sprint-status.yaml`, the live catalog or the live candle store. The tool is read-only outside its scratch directory.
- Never add a dependency. Never use a value tolerance.
- Never set `awaiting-operator`. The real-day verdict belongs to 31.11 (OPS-01).
- Do not build 31.8-31.11, and do not add `verify_day`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Clean day | minute files, a consistent store | every failing count 0; rehearsal `identical`; stored = query = received; bars `exact`/`float_noise`; exit 0 |
| Planted duplicate | a second snapshot file repeating one `ts_event` | `duplicate_ts_event` 1 and `overlap` ≥ 1; exit 1 |
| Planted overlap | two trade files with intersecting spans | `overlap` 1; exit 1 |
| Planted schema split | one trade file with an extra column | `schemas` 2, listed; exit 1 |
| Planted name lie | a file whose name starts after its first `ts_init` | `name_span` 1 (and any `read_mismatch` the name-pruned query really produces); exit 1 |
| Unsorted | rows out of `ts_init` order | `unsorted` 1; exit 1 |
| Lossy consolidation | an injected writer drops one row | rehearsal `different`; exit 1 |
| Already merged | one file per leaf-day | rehearsal `not_exercised`; pass |
| Candle altered | one 60 s `c` changed, one bar deleted | `different` 1, `missing` 1; exit 1 |
| Float noise | a stored `c` one ulp off | `float_noise` 1; pass |
| Midnight second | snapshot `ts_event` 23:59:59.5, `ts_init` 00:00:02.5 | in D's fold, not in D's parity digest; pass |
| File vanishes | deleted mid-run | refused, ledgered; exit 1 |

</intent-contract>

## Code Map

- `platform/verification/derivs.py`, `application/derivs.py` -- the CLI root and orchestration pattern to mirror (`_parser`, `main`, `run`, `inputs_of`, `check_day`, `render_text`, `report_json`).
- `platform/verification/conservation.py:83-120` -- `Refused`, `directory`, `plan_of`; `application/conservation.py:153,293-320` -- `day_start_ns`, `day_hours`, `is_closed`.
- `platform/verification/infrastructure/catalog_reader.py:56-135` -- `SNAPSHOT_DIR`/`TRADE_DIR`, `_row_groups`, the raw-read pattern (the new scan filters on `ts_init`).
- `platform/verification/domain/reference_signals.py:138-187,453-507` -- `RefBook.from_stored`, `bucket_start`, `fold_candles`, `RefCandle`.
- `platform/verification/domain/signal_compare.py:66-135` -- `Agreement`, `FAILING`, `at_places`.
- `platform/archive/application/consolidate_day.py:318-420` -- `consolidate_directory`, `run`, `run_closed_hours`, `RunStats`; `archive/infrastructure/maintenance_lock.py:63` -- `maintenance(catalog)`.
- `platform/archive/tests/test_catalog_files_backtest.py:54-93`, `archive/tests/conftest.py:18-45` -- the BacktestNode config precedent and the `nautilus_log_guard` (a second logging init aborts the process).
- `platform/research/tests/test_backtest_runner.py:440-469` -- a strategy subscribing to `DydxSecondSnapshot` by string path.
- `platform/candles/infrastructure/sqlite_store.py:52-70,120` -- the store schema and `candles_<venue>.db` (documentation only, never imported).
- `nautilus_trader/persistence/catalog/parquet.py:1578` (`query(..., files=)`), `nautilus_trader/serialization/arrow/serializer.py:255` (`serialize_batch`).
- `platform/tests/test_boundaries.py:120-140,1983-2150` -- `COMPOSITION_ROOTS`, the verification rules, the probe.
- `platform/verification/tests/test_derivs.py:261-392`, `test_trades.py:192-280` -- helpers that build a real catalog and env.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/catalog_check.py` (new, pure) -- the file-name parser, `row_digest`/`Digest` multiset, `STORE_BAR_SECONDS`, `READ_MARGIN_NS`, the structure checks over spans and `ts_init` sequences, schema signatures, the duplicate counter, candle judging, the report dataclasses with `passed`.
- [x] `platform/verification/infrastructure/catalog_scan.py` (new) -- leaf and day-file listing, streaming raw file scans (spans, sort, nulls, digest by `ts_init` window, snapshot trade columns by `ts_event`), and the read-only candle store read.
- [x] `platform/verification/subject/__init__.py`, `nautilus_reads.py`, `backtest_probe.py`, `consolidation.py` (new) -- the per-file opens, hourly queries, re-encode digest, the recording actor and node run, and the scratch rehearsal. The module docstrings state the subject rule.
- [x] `platform/verification/application/catalog.py` (new) -- the ports, `check_day`, `render_text` (structure, rehearsal, parity, candles), `report_json`.
- [x] `platform/verification/catalog.py` (new root) -- the CLI, the wiring, the refusals.
- [x] `platform/verification/application/sites.py`, `verification/tests/test_sites.py` -- `CATALOG_REFUSED` and the prefix.
- [x] `platform/tests/test_boundaries.py` -- the subject rules, the root, `COMPOSITION_ROOTS`, the new probe.
- [x] `platform/verification/tests/test_catalog.py` (new) -- end to end over a real `write_data` catalog, a real candle store built by writing SQLite rows directly, and a Nautilus log guard: every I/O row, each planted defect asserting a non-zero count and exit 1. Units: the name parser, the digest (order independence, a duplicate changes it), overlap, schema signature, the candle classes.
- [x] Real-data smoke -- `main(..., clock=<closed>)` over a hardlinked copy of the soak catalog's closed hours and an sqlite `backup` of each candle store, for both venues. Record per type and instrument: the structure counts, the rehearsal, the parity counts and digests, the candle classes, runtime and peak RSS. Root-cause every non-zero failing count: fix it, or register it OPEN with a follow-up.
- [x] Docs:
  - `platform/docs/DATA_DICTIONARY.md`: new §1.20 covering classes, digest, margin, rehearsal stages, subject rule and repro.
  - `docs/VERIFICATION_REPORT.md`: fill the catalog row, pending 31.11. Note that 31.11 must run the tool before the nightly consolidates D, or the rehearsal reads `not_exercised`.
  - `docs/DATA_INTEGRITY_AUDIT.md` from D-113: every smoke finding; the `float_noise` DEVIATION, if non-zero; the Known limit that intraday merges are rehearsed only while unmerged hours remain.
  - `platform/CLAUDE.md` DATA-02 and the verification context line: the `verification.subject` exception.

**Acceptance Criteria:**
- Given a closed day, when `python3 -m verification.catalog --venue V --day D` runs, then it prints the structure counts per type and leaf, the rehearsal per type, parity per instrument and type (stored/query/received counts and digests), and candle classes per width. It exits 1 exactly when a failing count is non-zero or the inputs are refused.
- Given `tests/test_boundaries.py`, when run, then the oracle modules reach nothing denied and load no `nautilus_trader`, and `verification.subject` is imported only by `verification.catalog`.
- Given the story ships, then every smoke finding has an audit row and §1.20 documents the tool.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 16: (high 1, medium 3, low 12)
- defer: 0
- reject: 3: (high 0, medium 0, low 3)
- addressed_findings:
  - `[high]` `[patch]` The change guard compared whole leaf listings, so a run on the live catalog (capture flushing every minute) was always refused, and a same-name rewrite went unseen.
    - Now only the day files are fingerprinted (inode, size, mtime_ns), at the start, after the rehearsal and at the end.
    - The candle bars are read once at the start.
    - A vanished file during scope selection or the definitions lookup maps to the change refusal.
    - Tested.
  - `[medium]` `[patch]` A row could escape the window: a late row (`ts_event` in D, `ts_init` > D+1+M), or a file whose name lies.
    - Day-file selection now also uses the `ts_init`/`ts_event` row-group statistics (it reads the columns when they are absent).
    - Overlap is also counted against every other file in the leaf.
    - Tested: `beyond_margin` and `name_span`/`overlap`.
  - `[medium]` `[patch]` Verify the verifiers: planted lossy query and backtest readers (drop, change, duplicate) now give `read_mismatch` > 0 on each leg. There is also end-to-end coverage for `extra`, `undefined_mismatch`, `unknown_width`, `empty`, `null_ts`, a metadata-key schema split and a Hyperliquid day; `open_count` has a unit test.
  - `[medium]` `[patch]` Neighbour-day consolidated files fall inside the margin, which costs about 2× and lets a neighbour defect fail D. This is documented as a Known limit beside `READ_MARGIN_NS` and in §1.20, with the upgrade path.
  - `[low]` `[patch]` The remaining fixes:
    - a null stamp is no longer read as skew;
    - an unknown type no longer double-counts;
    - `settlement()` takes the definition with the max `ts_init`;
    - the memory Known limit is corrected (every plan instrument's hour, measured 1.56 GB);
    - the runtime probe derives the oracle modules itself;
    - the log guard disposes its engine;
    - a line over 100 characters is wrapped;
    - the hour windows are clipped to the rows' real span;
    - a missing snapshot column is `read_mismatch`, not a KeyError;
    - the sqlite URI is quoted.
  - Rejected:
    - trade duplicates, which Story 31.4 owns;
    - the `files=` fallback misattribution, which arises only when a defect already fails the day;
    - the margin before D, which the contract makes symmetric.

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 2, low 5)
- defer: 0
- reject: 17: (high 0, medium 0, low 17)
- addressed_findings:
  - `[medium]` `[patch]` One stray `ts_init` far from the rest (0, or far in the future) turned the per-file open into hundreds of thousands of hourly queries, because the windows covered every hour of the rows' `[min, max]`. `FileScan` now carries the distinct UTC hours holding a stamped row. `hour_windows(span, hours)` queries only those hours, each clipped to the rows' span, and the `open_file` port takes the windows. Tested.
  - `[medium]` `[patch]` On a live catalog, `stamps_meet` read the footer of every non-window file. A collector file caught mid-write (Nautilus's `write_data` writes in place) raised `ArrowInvalid` outside every handler and crashed the run. Such a file is now left to its own day's check, which lists it by name and counts `name_span`/`beyond_margin`. A vanished file still maps to the change refusal. Tested. The listing's footer cost is now a documented `Known limit:` with an upgrade path.
  - `[low]` `[patch]` Several places read as if files were opened over their name span: the `nautilus_reads` module and `open_file` docstrings, and §1.20's `open_failed` row. All now describe the rows' hours.
  - `[low]` `[patch]` `judge_bar` returned `different` for a `seconds_observed` difference before the definedness check, contradicting `_worst`'s rule. `undefined_mismatch` now wins. Tested.
  - `[low]` `[patch]` A file whose every `ts_init` is null counted `open_count` as well as `null_ts`, although it is never opened. It now counts `null_ts` only. Tested.
  - `[low]` `[patch]` The subject's allowed-import check used `startswith("verification.domain")`, which also admits a sibling such as `verification.domain_x`. It now matches the package or its children.
  - `[low]` `[patch]` A scratch directory inside the catalog root is now refused before anything is created, so the rehearsal can never write under the live catalog. Tested.
  - Rejected:
    - hard-linked scratch: mandated by the contract, and guarded by the post-rehearsal fingerprint;
    - metadata outside the digest and precision labels: by contract, 31.6 checks them;
    - trade duplicates: 31.4 owns them;
    - the `files=` fallback reading neighbours, the schema split double-counted as `read_mismatch`, and a neighbour-day schema drift: each arises only when a failing class already fails the day, or it is covered by the margin Known limit;
    - the settlement currency taken from the latest definition;
    - `--raw-dir` naming: mandated by the contract;
    - the missing `ts_event` column, a corrupt day file, a decode crash in the parity legs, and an unreadable candle store: each is a loud crash, ledgered and re-raised as the contract says;
    - no disk-space guard on the copy fallback: fails loudly with ENOSPC;
    - an empty day passing: coverage belongs to `verification.conservation`;
    - hour 23 not intraday-rehearsed: production never merges hour 23 intraday either (`closed_hours_needing_work` skips the current hour, and the nightly takes the closed day).

## Design Notes

Why a subject package: AC1 requires opening every file through `ParquetDataCatalog`, AC2 the archive's own consolidation and AC3 a `BacktestNode`. These are the readers and writers under test, so the tool must drive them. DATA-02 forbids the *reference* from sharing code with the checked code. Keeping every driven call in one package that the oracle never imports preserves this: the stored side is read raw, and the received side is re-encoded by the subject.

The digest is computed on both sides by the same pure function:

```python
def row_digest(row: Mapping[str, object]) -> int:
    h = hashlib.blake2b(digest_size=16)
    for name in sorted(row):
        h.update(repr((name, row[name])).encode())
    return int.from_bytes(h.digest(), "big")
```

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform && ruff format --check verification tests && ruff check verification tests/test_boundaries.py && mypy verification` -- expected: clean.

## Auto Run Result

Status: done

- **Change:** this was a follow-up review pass over the Story 31.7 catalog tool (`python3 -m verification.catalog`): structure, the consolidation rehearsal, backtest-read parity, and candle parity. The implementation was committed at `cc56c18fbb`, and this pass hardened it with 7 patches.
- **Files changed in this pass:**
  - `platform/verification/domain/catalog_check.py`: `FileScan.hours`; `hour_windows(span, hours)`; `judge_bar` class order; an unopened null-stamp file no longer counts `open_count`.
  - `platform/verification/infrastructure/catalog_scan.py`: the scan collects the hours; `stamps_meet` skips a footer-less file (mid-write); the listing-cost Known limit.
  - `platform/verification/application/catalog.py`: files are opened over the windows of their rows' hours; the `open_file` port takes the windows.
  - `platform/verification/subject/nautilus_reads.py`: `open_file(windows)`; the docstrings are corrected.
  - `platform/verification/catalog.py`: a scratch directory inside the catalog is refused.
  - `platform/tests/test_boundaries.py`: an exact `verification.domain` package match.
  - `platform/verification/tests/test_catalog.py`: 4 new tests and 1 new assertion.
  - `platform/docs/DATA_DICTIONARY.md` §1.20: the `open_failed`/`open_count` rows and the refusals.
- **Review:** 2 reviewers (Blind Hunter: 17 findings; Edge Case Hunter: 9), deduplicated to 24 distinct findings. 7 patched (2 medium, 5 low), 0 deferred, 17 rejected. Reasons are in the Review Triage Log.
- **Verification:**
  - `python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q`: 557 passed (553 before this pass, plus 4 new).
  - `ruff format --check verification tests`: clean.
  - `ruff check verification tests/test_boundaries.py`: clean.
  - `mypy verification`: clean.
  - The real-data smoke was not rerun. The patches change which hours a file is opened over (identical for contiguous rows), the class order of a bar that already fails, and two refusal and skip paths.
- **Follow-up review:** not recommended. The fixes are localized; the two medium ones are covered by new tests.
- **Residual risks:** unchanged from the first pass.
  - A full Bybit day is estimated at about 16 min and more than 1.5 GB.
  - Neighbour-day files double the cost after the nightly.
  - Each listing reads the leaf's footers (a new Known limit).
  - The real-day verdict belongs to 31.11, which must run the tool before the nightly consolidates D.
