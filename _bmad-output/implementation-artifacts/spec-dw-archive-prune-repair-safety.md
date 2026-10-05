---
title: 'DW bundle archive-prune-repair-safety: prune status errors, stale pass, atomic gap markers, kline paging guard, zstd repair'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: 'b05d288af41a025e22d6f9c776d0e3df6449e6a7'
baseline_revision: 'de49dfce2b4dba3e47d79b26ab313a0e7a939c35'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Five archive-maintenance holes (DW-188, DW-203, DW-211, DW-212, DW-260): a corrupt `candles_<venue>.db` aborts the nightly prune with an unledgered `sqlite3.DatabaseError`; a stored `verified_days = pass` survives a later rebuild/repair that changes the day's seconds, so prune can release trades on stale proof; a failed `_archive_gaps/*.jsonl` append can leave a torn line that wedges that instrument's rebuild every night; the three venue kline paging loops can spin forever on a non-advancing page; `archive.repair_catalog --apply` writes snappy files.

**Approach:** Prune ledgers an unreadable status store (`prune.verified_days`) and keeps that venue's days provisional. Every writer that changes a closed day's seconds (the rebuild, the repair) clears that instrument-day's stored verdict through a new `VerifiedDays.clear_verified` port method *before* the change lands, so any later outcome (reconcile error, refusal, skip, standalone run) leaves the day unverified. Gap-marker appends are serialized with an exclusive `flock` and truncated back on failure; the reader takes a shared lock. Kline paging gets a strict-progress check plus a page cap (`KlineError`). Repair calls `apply_zstd_default()` at import.

## Boundaries & Constraints

**Always:** fail safe (an unreadable or cleared status keeps files); every continue-past-failure ledgered at a named site (DATA-07); verdict cleared before the seconds change, never after; never modify `nautilus_trader/` or `crates/`; tests with real types, no class-based tests; ruff/mypy clean; Known-limit comments updated, not left contradicting the code.

**Block If:** a fix needs a schema change to `verified_days`, or a change to the frozen gap-marker line format.

**Never:** edit `_bmad-output/implementation-artifacts/deferred-work.md`; clear a verdict when no row of that day changed; silently drop or "heal" a torn marker line (a torn tail from a crash stays a loud refusal); make the kernel do marker file I/O (`kernel.archive_markers` invariant).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Corrupt store | `candles_BYBIT.db` not a database, old BYBIT trade day | Prune continues; BYBIT days kept `status_unreadable`; other venues decided normally; exit 2 | `prune.verified_days` ledgered once per venue |
| Rebuild changes a passed day | `pass` row, rebuild `--apply` changes ≥1 row | Row deleted before the renames; files rewritten | — |
| Rebuild no-op on passed day | `pass` row, 0 changed rows | Row kept | — |
| Clear fails | store write raises `sqlite3.Error`/`OSError` | Staged rewrites discarded, instrument-day refused, files untouched | `rebuild.verdict` |
| Repair on a passed day | flagged row in a `pass` day, `--apply --candles-dir` | Verdict of that (iid, UTC day of `ts_event`) cleared before the delete | clear failure: `repair.error`, instrument not repaired |
| Marker append ENOSPC | write/fsync raises mid-append | File truncated back to its pre-append size; returns False | `archive_gaps.write` (message says if truncate also failed) |
| Non-advancing page | venue returns a full page whose cursor does not move | `KlineError` → `reconcile.error`, day unverified | — |
| Page cap | > `MAX_KLINE_PAGES` (1440) pages | `KlineError` | — |

</intent-contract>

## Code Map

- `platform/archive/application/prune.py` -- `day_statuses` reads statuses via `VerifiedDays`; `PruneReport.has_findings`.
- `platform/candles/application/verified_days.py` -- `VerifiedDays` Protocol (add `clear_verified`).
- `platform/candles/infrastructure/sqlite_store.py` -- `mark_verified`/`verified_status` functions + store class at ~L365 (add `clear_verified`).
- `platform/candles/infrastructure/verified_days.py` -- `VerifiedDaysStore`, `VerifiedDaysDir` adapters.
- `platform/archive/application/rebuild_day.py` -- `rebuild_day`/`_rebuild_files`/`_commit`/`run`; refusals via `RefusedError`.
- `platform/archive/rebuild_seconds.py` -- CLI composition root; `platform/archive/nightly.py` `steps()` builds its argv.
- `platform/archive/application/repair.py`, `platform/archive/repair_catalog.py` -- repair path (`write_data`), CLI.
- `platform/archive/domain/archive_day.py` -- AD-D9 docstring with the DW-203 Known limit.
- `platform/archive/infrastructure/gap_markers.py`, `platform/capture/infrastructure/gap_markers.py` -- the two marker writers (archive also reads).
- `platform/archive/domain/reconciliation.py` -- `KlineError`, `DAY_MS`, `MINUTE_MS`.
- `platform/archive/infrastructure/klines_{dydx,bybit,hyperliquid}.py` -- paging loops.
- `platform/kernel/parquet_compat.py` -- `apply_zstd_default()` (pattern: module-level call in `archive/application/backfill_bars.py:147`).

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/application/prune.py` -- in `day_statuses`, catch `sqlite3.DatabaseError` from `verified_status`; ledger `prune.verified_days` once per venue, skip further reads for that venue, keep its days provisional with kept reason `status_unreadable`; count them so `PruneReport.has_findings()` is true -- DW-188.
- [x] `platform/candles/application/verified_days.py`, `platform/candles/infrastructure/sqlite_store.py`, `platform/candles/infrastructure/verified_days.py` -- add `clear_verified(instrument_id, day) -> None` (DELETE the row; no-op when the store file or table does not exist; `VerifiedDaysDir` must not create a missing store) -- DW-203.
- [x] `platform/archive/application/rebuild_day.py` -- thread a `VerifiedDays | None` into `rebuild_day`/`run`; when applying and an instrument-day has staged rewrites, clear its verdict after staging and before `_commit`; a clear failure (`sqlite3.Error`, `OSError`) discards the staged rewrites and refuses at `rebuild.verdict`; update module docstring -- DW-203.
- [x] `platform/archive/rebuild_seconds.py`, `platform/archive/nightly.py` -- `--candles-dir` (required with `--apply`), wired to `VerifiedDaysDir`; nightly passes it -- DW-203.
- [x] `platform/archive/application/repair.py`, `platform/archive/repair_catalog.py` -- `--candles-dir` required with `--apply`; clear each repaired instrument-day's verdict before its first `delete_data_range`; failure ledgered `repair.error`, instrument not repaired; call `apply_zstd_default()` at `repair.py` module level -- DW-203, DW-260.
- [x] `platform/archive/domain/archive_day.py` -- replace the DW-203 Known limit with the invalidation rule.
- [x] both `gap_markers.py` -- binary append under `fcntl.flock(LOCK_EX)`, remember pre-append size, on `OSError` `ftruncate` back + fsync; archive `load_gaps` reads under `LOCK_SH` -- DW-211.
- [x] `platform/archive/domain/reconciliation.py` + three `klines_*.py` -- `MAX_KLINE_PAGES = DAY_MS // MINUTE_MS` and one helper that raises `KlineError` when the next cursor does not move strictly in the paging direction or the page count exceeds the cap; use it in all three loops -- DW-212.
- [x] Tests: `archive/tests/test_prune.py`, `candles/tests/test_verified_days.py`, rebuild/repair tests, gap-marker tests (archive + capture), kline fetcher tests, nightly argv test -- one per matrix row.
- [x] Docs: `platform/README.md` standalone rebuild usage, `platform/docs/DATA_DICTIONARY.md` §6 / CLAUDE.md DATA-05 mention of repair encoding if stated, where they state CLI flags or the repair's snappy encoding.

**Acceptance Criteria:**
- Given the full platform test suite for archive, candles, capture, kernel, tests/, when run, then all pass with no new warnings.
- Given `archive.repair_catalog --apply` without `--candles-dir`, when parsed, then argparse refuses (likewise `rebuild_seconds --apply`).
- Given a repair `write_data` in a fresh process, when the file is inspected, then its codec is ZSTD.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9: (high 1, medium 4, low 4)
- defer: 0
- reject: 5: (medium 1, low 4)
- addressed_findings:
  - `[high]` `[patch]` A typo'd `--candles-dir` made every `clear_verified` a silent no-op; `rebuild_seconds`/`repair_catalog --apply` now refuse a non-existent directory.
  - `[medium]` `[patch]` `_clear_verdict` leaked staged temps on a non-sqlite/OS exception (`MalformedInstrumentId`); any exception now discards the staged rewrites.
  - `[medium]` `[patch]` Operator docs (`DEPLOY_CHECKLIST.md`, Makefile comment, README) still showed `--apply` without `--candles-dir`; updated.
  - `[medium]` `[patch]` Reconcile writes its verdict without the maintenance lock, so a concurrent repair's clear can be overwritten by an in-flight `pass`; documented as `Known limit:` with upgrade path in `archive_day.py` + DATA_DICTIONARY.
  - `[medium]` `[patch]` Bybit/dYdX page cap of 1440 was ~720x the need; tight per-venue cap (3) via `max_pages`.
  - `[low]` `[patch]` `_write_all` looped forever on a 0-byte write; raises EIO. Truncate-back now on any exception.
  - `[low]` `[patch]` Crash-torn marker tail not healed: documented `Known limit:` (upgrade path `coverage_file.repair_torn_tail`); stale `coverage_file.py` docstring fixed.
  - `[low]` `[patch]` "the next nightly re-proves it" was false (watermark advances); reworded to the manual saga rerun.
  - `[low]` `[patch]` Test gaps: capture fsync truncate-back test, fault injection no longer keyed on line prefix, read-wait test asserts returned spans.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 1, medium 1, low 2)
- defer: 0
- reject: 13: (medium 3, low 10)
- addressed_findings:
  - `[high]` `[patch]` An existing but wrong `--candles-dir` (the catalog, a host path) still made every `clear_verified` a silent no-op; new `candle_store_dir_problem` refuses, in both `rebuild_seconds` and `repair_catalog --apply`, a directory holding no `candles_<venue>.db`.
  - `[medium]` `[patch]` `repair_catalog --apply` could rebuild candles in one store (`--candles-db`) while clearing the verdict in another (`--candles-dir`); a `--candles-db` outside `--candles-dir` is now refused.
  - `[low]` `[patch]` A partial multi-day verdict clear in the repair ledgered all days without saying which were already cleared; the `repair.error` detail now names them.
  - `[low]` `[patch]` `archive_day.py`'s invalidation rule stated "never a stale `pass`" absolutely, contradicting its own Known limit, which also omitted a standalone rebuild racing the compare; both reworded.

## Design Notes

DW-203: the ledger's suggested upgrade path (reconcile refuses to leave a stale pass) only covers a reconcile that runs; a skipped reconcile, a stopped saga, a standalone rebuild and the repair all bypass it. Clearing at the writer, before the rename/delete, covers every path and is fail-safe: if the change then fails, the day is merely unverified (trades kept) and the next nightly re-proves it. The row is deleted (provisional), not set `fail`: the old verdict judged seconds that no longer exist. Both `pass` and `fail` are cleared.

DW-211: truncate-back is only safe because capture and archive append to the same file; without the exclusive flock one writer's truncate could cut a line the other already acknowledged durable (archive then deletes a trade file whose `pruned` marker is gone). A torn tail left by a crash (no exception to catch) still refuses the rebuild loudly.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive candles capture kernel tests -q -p no:cacheprovider` -- expected: all pass (deselect the TLS node test per memory).
- `cd platform && ruff check . && ruff format --check . && mypy archive candles capture` -- expected: clean.


## Auto Run Result

**Summary:** Follow-up review of the done bundle (DW-188, DW-203, DW-211, DW-212, DW-260). Four review patches: the CLIs now refuse a `--candles-dir` with no candle store in it (an existing-but-wrong directory left every verdict clear a silent no-op, the exact DW-203 hazard), `repair_catalog` refuses a `--candles-db` outside `--candles-dir`, the repair's partial-clear ledger names the days already cleared, and the `archive_day.py` invalidation docstring no longer contradicts its Known limit.

**Files changed (this pass):** `candles/infrastructure/verified_days.py` (`candle_store_dir_problem`), `archive/rebuild_seconds.py` and `archive/repair_catalog.py` (CLI guards + docstrings), `archive/application/repair.py` (ledger detail), `archive/domain/archive_day.py` (docstring), `archive/tests/test_{rebuild_day,repair}.py` (store-holding `_candles_dir` helper, store-less and mismatched-db refusals), `README.md`, `docs/DATA_DICTIONARY.md`.

**Review:** 4 patches applied, 0 deferred (the ledger is orchestrator-owned and this spec forbids editing it), 13 rejected: cleared-but-unrejudged days invisible to prune / "same run" re-judging weaker after a mid-saga failure (by design and fail-safe: trades kept, rerun path documented); locked store reported as unreadable (fail-safe, exception repr in the ledger, rejected in the first pass too); `verified=None` report path; capture `flock` blocking (rejected in the first pass); interrupt-plus-truncate-failure ledger; duplicated lock helper across contexts (deliberate); `load_gaps` exists/open race (marker files are never removed); unescaped SQLite URI (pre-existing pattern, fixed container paths); shrunken venue page size (surfaces as a loud reconcile mismatch); cap message ambiguity; mixed-image rollout (one shared image); non-`DatabaseError` from the store (the store wrapper raises `OperationalError`).

**Verification:** `python3 -m pytest -o addopts="" --rootdir=. archive/tests candles/tests capture/tests kernel/tests tests --deselect tests/test_tls_node.py`: 1853 passed, 3 failed. The 3 failures are the same pre-existing environment failures as the first run (`platform/data/chart_indicators.toml` is a root-owned directory on this box; `test_legacy_names` x2, `test_notebook_rules` x1). `ruff check .` + `ruff format --check` (0.15.16) clean; mypy 1.20.2 clean on the changed modules.

**Residual risks:** A brand-new deployment whose candle directory holds no store yet will have the nightly's `rebuild_seconds --apply` step refused until a collector has created its venue's store. That is deliberate: a store-less directory is a misconfiguration once collectors run. A `--candles-dir` that points at a different real candle store (e.g. a backup copy) is still not detectable. The reconcile-vs-clear race and the crash-torn marker tail remain documented Known limits.
