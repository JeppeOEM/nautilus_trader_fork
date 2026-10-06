---
title: 'DW-195: secondary index on candles(bar_seconds, t) for the retention prune'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: '2dd9fce9dd'
baseline_revision: '38d3dedeee'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** The hourly retention prune (`candles/application/prune.py`'s `prune_loop`) runs `store.prune()` on the collector's event loop, and its `DELETE FROM candles WHERE bar_seconds = ? AND t < ?` cannot use the `(instrument_id, bar_seconds, t)` primary key of the `WITHOUT ROWID` table. `EXPLAIN QUERY PLAN` shows `SCAN candles`: on a large store the full scan blocks ingestion and per-second sampling for its whole duration (DW-195).

**Approach:** Per the human decision of 2026-10-05, add `CREATE INDEX IF NOT EXISTS` on `candles(bar_seconds, t)` to `_SCHEMA`. `connect_rw` already runs `_SCHEMA` on every writer open, so an existing store gets the index the next time its writer opens it: that is the idempotent migration. Re-record the frozen-schema fixture on purpose, prove the prune's query plan uses the index, and keep the prune on the loop (single-writer invariant), with its remaining cost written up as a `Known limit:`.

## Boundaries & Constraints

**Always:** the prune stays synchronous on the writer's loop; `candles` table DDL, `_UPSERT` and the readers' primary-key plans are unchanged; LGPL header, ruff/mypy clean, function-style pytest tests that return `-> None`.

**Block If:** the index cannot make the prune plan a `SEARCH` on SQLite 3.45 without a schema change beyond one added index.

**Never:** `asyncio.to_thread`/a second connection for the prune; edit the deferred-work ledger; touch `nautilus_trader/` or `crates/`; drop or rebuild the `candles` table.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fresh store | `connect_rw` on a new path | tables + index `candles_by_bar_seconds_t` exist | none |
| Pre-index store | file created with the pre-change DDL holding rows | after `connect_rw`: index present, rows intact, prune deletes the expired rows | none |
| Re-open | `connect_rw` twice on one file | no error, exactly one index | none |
| Prune plan | `EXPLAIN QUERY PLAN` of the prune DELETE | `SEARCH ... USING ... INDEX candles_by_bar_seconds_t (bar_seconds=? AND t<?)`, no `SCAN` | none |

</intent-contract>

## Code Map

- `platform/candles/infrastructure/sqlite_store.py` -- `_SCHEMA`, `connect_rw`, `prune()`; module docstring describes the frozen text
- `platform/candles/application/prune.py` -- `prune_loop`, the on-loop caller (unchanged code; its docstring gets the cost note)
- `platform/candles/tests/test_schema_is_frozen.py` + `fixtures/candle_store_schema.sql` -- the recorded DDL copy
- `platform/candles/tests/test_candle_store.py` -- the store's behaviour tests

## Tasks & Acceptance

**Execution:**
- [x] `platform/candles/tests/fixtures/candle_store_schema_pre_index.sql` -- copy of the current recorded DDL (the pre-DW-195 shape) -- the migration test builds a real old-shape store from it
- [x] `platform/candles/infrastructure/sqlite_store.py` -- append `CREATE INDEX IF NOT EXISTS candles_by_bar_seconds_t ON candles(bar_seconds, t);` to `_SCHEMA`; expose the prune DELETE as a module constant `_PRUNE` used by `prune()`; update the module docstring (re-recorded deliberately, DW-195; `IF NOT EXISTS` on an index is the migration); add a `Known limit:` on `prune()` naming the residual on-loop cost (rows removed + index upkeep; first writer open of an old store builds the index synchronously; a prune after a long outage deletes a backlog in one transaction) and the upgrade path (batched `DELETE ... LIMIT`-style chunks with yields, or a writer thread owning the connection)
- [x] `platform/candles/tests/fixtures/candle_store_schema.sql` -- re-record as the new `_SCHEMA` byte-for-byte
- [x] `platform/candles/tests/test_schema_is_frozen.py` -- docstring: recorded, changed only deliberately (nothing frozen until prod; DW-195 added the index); add a test asserting a fresh store has exactly the one recorded index on `candles`
- [x] `platform/candles/tests/test_candle_store.py` -- tests: prune plan is an index `SEARCH` (no `SCAN`); pre-index store gains the index on `connect_rw` with rows intact and prunes correctly; double `connect_rw` is idempotent
- [x] `platform/candles/application/prune.py` -- docstring: the prune stays on the loop by design (single writer), the index bounds it, see the `Known limit:` on `sqlite_store.prune`

**Acceptance Criteria:**
- Given the candle tests, when run, then all pass, including the re-recorded byte-identity test.
- Given the changed files, when `ruff check`, `ruff format --check` and `mypy` run on them, then they report no errors.

## Design Notes

The index is on a `WITHOUT ROWID` table, so its entries carry the full PK and the DELETE resolves as a `COVERING INDEX` search (verified on SQLite 3.45.1). `_UPSERT`'s conflict branch never changes `bar_seconds`/`t`, so the index costs one extra B-tree insert per new bucket only. Readers keep their PK plan (`instrument_id=? AND bar_seconds=? AND t>?`).

## Verification

**Commands:**
- `cd platform && python3 -m pytest candles -q` -- expected: all pass
- `cd platform && ruff check candles && ruff format --check candles && mypy candles/infrastructure/sqlite_store.py candles/application/prune.py` -- expected: clean

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11: (high 0, medium 2, low 9)
- defer: 0
- reject: 7
- addressed_findings:
  - `[medium]` `[patch]` DEPLOY_CHECKLIST assumed the collector always builds the index first; `archive`'s nightly (`build_candles`, `mark_verified` via `connect_rw`) builds it for `candles_dydx.db` when the dydx profile is off -- entry now names every first writer, the 03:07 UTC window, and a manual one-off build command for the dYdX store
  - `[medium]` `[patch]` the plan test ran only on an empty, unanalysed store -- added a populated, migrated, `ANALYZE`d store test asserting the prune still SEARCHes the index for both pruned widths
  - `[low]` `[patch]` checklist heading said "commit: this change's" -- now `1814a83a0d`
  - `[low]` `[patch]` checklist disk sizing ("the primary key's size again") was wrong for a WITHOUT ROWID table and ignored `CREATE INDEX`'s temp-file sort -- reworded (about a third of the table, temp files in the container filesystem)
  - `[low]` `[patch]` verification step's sentence was broken and ran `mode=ro` from the host (needs a writable `-shm`) -- now runs via `docker compose exec archive` with the expected output
  - `[low]` `[patch]` `connect_rw`'s docstring did not mention the one-time index build it performs -- added, pointing at the Known limit
  - `[low]` `[patch]` `oldest_t`/`newest_t`/`bucket_starts` and the verifier's `catalog_scan` read were not pinned to the primary key -- new plan test
  - `[low]` `[patch]` the migration test checked the index name only -- now also its stored definition
  - `[low]` `[patch]` the two schema fixtures could drift -- test asserts pre-index == recorded minus the index line
  - `[low]` `[patch]` `test_schema_is_frozen` docstring called an added index "safe" -- now states the one-time build cost
  - `[low]` `[patch]` prune Known limit's "about one hour" now names `PRUNE_INTERVAL_SECONDS`

## Auto Run Result

**Summary:** Follow-up review of DW-195 (index `candles_by_bar_seconds_t` on `candles(bar_seconds, t)`, implemented in `1814a83a0d`). The review found no defect in the change itself. It hardened the operator checklist (which writer builds the index, where it spills, how to verify) and the tests (prune plan on a realistic migrated store, every per-instrument reader pinned to the primary key, fixture drift, migrated index definition).

**Files changed (this pass):**
- `platform/docs/DEPLOY_CHECKLIST.md` -- DW-195 entry: commit hash, first-writer cases incl. the dYdX store via `archive`, disk/temp sizing, nightly window, in-container verification
- `platform/candles/infrastructure/sqlite_store.py` -- `connect_rw` docstring notes the one-time build; prune Known limit references `PRUNE_INTERVAL_SECONDS`
- `platform/candles/tests/test_candle_store.py` -- 3 new tests (reader plans, fixture drift, analysed migrated store plan); migration test checks the index SQL
- `platform/candles/tests/test_schema_is_frozen.py` -- docstring states the build cost

**Review:** 11 patches applied (2 medium, 9 low), 0 deferred, 7 rejected (out-of-scope "prints"/"lists" wording, partial-index upgrade note, pre-3.36 SQLite wording, repeated rationale, startup lock >60 s / SQLITE_FULL crash-loop (loud failure at startup, covered by the checklist), defensive check for a same-named index with another definition).

**Verification:** `cd platform && python3 -m pytest candles -q` -- 110 passed. `ruff check` + `ruff format` on `platform/candles` -- clean. `mypy candles/infrastructure/sqlite_store.py candles/application/prune.py` -- no issues.

**Residual risks:** unchanged from the implementation pass. The index is built synchronously on the first writer open, and a prune after a long outage still deletes its backlog in one on-loop transaction. Both are recorded as a Known limit and in the checklist. The deferred-work ledger was not edited.
