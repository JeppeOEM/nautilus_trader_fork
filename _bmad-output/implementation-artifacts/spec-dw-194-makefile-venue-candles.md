---
title: 'DW-194: per-venue make build-candles'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: '3b3d6b67e882f00be5962feaee0d7d7206c569ff'
baseline_revision: '6b44413d3f3f01b4700f78c7ddebe3faee822d2d'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `make build-candles` runs `candles.rebuild` with no `--venue` and a hard-coded `--db .../candles_dydx.db`. On the shared three-venue catalog, it folds Bybit and Hyperliquid instruments into the dYdX store, where `data_api` never reads them. It also never repairs `candles_bybit.db` or `candles_hyperliquid.db`. Yet DATA-05 and the audit point the operator at this target as *the* repair for `collector.candle_store`, a ledger site that all three collectors emit (DW-194).

**Approach:** Give `make build-candles` a required `VENUE=` variable, mirroring `make nightly`. It passes `--venue $(VENUE) --candles-dir /app/candles_dir` to `candles.rebuild`. The CLI gains `--candles-dir` as an alternative to `--db`, which resolves the store path through the one frozen formula `db_path_for_venue`. `--venue` is validated against the known venues, so a typo is refused instead of rebuilding nothing and exiting 0.

## Boundaries & Constraints

**Always:** Derive the per-venue store path only through `candles.infrastructure.sqlite_store.db_path_for_venue` (AD-D12). Never restate the formula in shell or Make. Validate venues against `kernel.venues.VENUE_KINDS` (`candles` must not import `archive`). Keep `--db` working unchanged for existing callers (`archive.nightly`, tests). Missing `VENUE` in make must fail before docker runs, with a usage message like `nightly`'s.

**Block If:** none expected.

**Never:** Edit the deferred-work ledger. Change `archive.nightly`'s invocation. Touch `nautilus_trader/` or `crates/`. Add an "all venues" loop target (out of the intent).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Per-venue rebuild | `--candles-dir D --venue BYBIT` | Store `D/candles_bybit.db` holds only `.BYBIT` ids | exit 0 |
| Dir without venue | `--candles-dir D` (no `--venue`) | refused before any store is opened | argparse error, exit 2 |
| Both/neither path | `--db X --candles-dir D` or neither | refused | argparse error, exit 2 |
| Unknown venue | `--venue Bybit` / `BINANCE` | refused (choices) | argparse error, exit 2 |
| make, no VENUE | `make build-candles` | usage line on stderr, no compose run | exit non-zero |
| make, VENUE set | `make build-candles VENUE=BYBIT` | compose runs `candles.rebuild ... --candles-dir /app/candles_dir --venue BYBIT` | — |

</intent-contract>

## Code Map

- `platform/Makefile:351-356` -- `build-candles` target to change. `nightly` at :314-330 is the VENUE-guard precedent.
- `platform/candles/rebuild.py` -- CLI: `_parser` (:61), `_jobs` (:85, uses `args.db`), `main` (:98), module docstring usage.
- `platform/candles/infrastructure/sqlite_store.py:118` -- `db_path_for_venue(candles_dir, venue)`, the frozen formula.
- `platform/kernel/venues.py:42` -- `VENUE_KINDS` (DYDX/HYPERLIQUID/BYBIT), the kernel-level venue set.
- `platform/candles/tests/test_rebuild.py` -- CLI tests (`_run`, `catalog` fixture; :153 venue filter test).
- `platform/tests/test_compose_profiles.py:64-84` -- `_makefile_recipes`/`_compose_commands` static Makefile helpers.
- Docs naming the target: `platform/README.md:414`, `platform/docs/DATA_INTEGRITY_AUDIT.md:339-341`, `platform/CLAUDE.md` DATA-05 ("repaired by `python -m candles.rebuild`").

## Tasks & Acceptance

**Execution:**
- [x] `platform/candles/rebuild.py` -- Make `--db`/`--candles-dir` a required mutually exclusive group. Give `--venue` `choices=sorted(VENUE_KINDS)`. When `--candles-dir` is given without `--venue`, call `parser.error`. Otherwise resolve `args.db = db_path_for_venue(...)` in `main` before use. Update the docstring usage. -- one formula, typo-proof venue.
- [x] `platform/candles/tests/test_rebuild.py` -- Add tests: `--candles-dir` + `--venue` writes `candles_<venue>.db` in that dir and the store holds that venue's ids only. `--candles-dir` without `--venue` exits 2. An unknown venue exits 2. -- I/O matrix.
- [x] `platform/Makefile` -- In `build-candles`, add a `@test -n "$(VENUE)" || { echo usage >&2; exit 1; }` guard and pass `--candles-dir /app/candles_dir --venue $(VENUE)`. Update the `##` comment (VENUE required, DYDX|BYBIT|HYPERLIQUID, one store per venue). -- the fix.
- [x] `platform/tests/test_compose_profiles.py` -- Add a static test: the `build-candles` compose command passes `--venue $(VENUE)` and `--candles-dir`, and never passes a literal `--db`. -- regression for DW-194.
- [x] `platform/README.md`, `platform/docs/DATA_INTEGRITY_AUDIT.md`, `platform/CLAUDE.md` -- Point the operator at `make build-candles VENUE=<VENUE>` (per venue, for the venue whose store failed). -- docs match the target.

**Acceptance Criteria:**
- Given the shared catalog, when the operator runs `make build-candles VENUE=HYPERLIQUID`, then only `.HYPERLIQUID` ids are folded, into `/app/candles_dir/candles_hyperliquid.db`.
- Given the existing `--db` callers, when `archive.nightly` runs `build_candles`, then its behavior is unchanged and its tests pass.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 0, medium 1, low 6)
- defer: 2 (high 0, medium 2, low 0)
- reject: 9 (high 0, medium 0, low 9)
- addressed_findings:
  - `[medium]` `[patch]` The `--candles-dir` test proved nothing for Bybit because the fixture held no Bybit ids. It now writes a Bybit instrument and asserts each store holds only its own venue's id.
  - `[low]` `[patch]` The "neither" case asserted a file nothing pointed at. It now asserts no `*.db` was created at all.
  - `[low]` `[patch]` A `--candles-dir` that is missing or not a directory silently created a store under that path. It is now refused with a usage error (`_venue_store`) and has a new test.
  - `[low]` `[patch]` Updated the `--venue` help: with `--candles-dir` it also names the store.
  - `[low]` `[patch]` Added the `[amended 2026-10-06: DW-194]` tag to the DATA-05 edit in `platform/CLAUDE.md`.
  - `[low]` `[patch]` Audit step 5 now names the three venues and is re-wrapped.
  - `[low]` `[patch]` The Makefile comment now says "that venue's collector" instead of "the collector".

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5 (high 0, medium 0, low 5)
- defer: 0
- reject: 14 (high 0, medium 0, low 14)
- addressed_findings:
  - `[low]` `[patch]` The docstring's `--db` example marked `--venue` optional, which presented the cross-venue fold as a valid call. It now shows the nightly form with `--venue`, and a `Known limit:` names the `--db`-without-`--venue` ceiling and its upgrade path.
  - `[low]` `[patch]` The docstring promised "never a run that rebuilds nothing and exits 0", which was too broad. It now says a misspelled venue is refused, venues are upper case, and a valid venue with no snapshots is a real exit 0.
  - `[low]` `[patch]` `--candles-dir ""` resolved to the cwd and created a store there. It is now refused, with a new test (`test_an_empty_candles_dir_is_refused_not_the_cwd`).
  - `[low]` `[patch]` The `is_dir` comment claimed it caught any wrong mount. It now names the case it cannot catch, an empty directory mounted in the wrong place.
  - `[low]` `[patch]` Nothing tested the Makefile `VENUE` guard. Added `test_build_candles_refuses_a_missing_venue_before_compose_runs` (static: the guard is the recipe's first line and exits 1).

## Verification

**Commands:**
- `cd platform && python3 -m pytest candles/tests tests/test_compose_profiles.py archive/tests/test_nightly.py archive/tests/test_step_ledgers.py -q` -- expected: all pass.
- `cd platform && make build-candles COMPOSE=echo; echo $?` -- expected: usage line, non-zero. `make build-candles COMPOSE=echo VENUE=BYBIT` -- expected: echoed command with `--candles-dir /app/candles_dir --venue BYBIT`.
- `ruff check` + `ruff format --check` + `mypy` on the changed Python files -- expected: clean.


## Auto Run Result

**Summary:** `make build-candles` requires `VENUE=` and fails before docker runs without it. It runs `candles.rebuild --candles-dir /app/candles_dir --venue $(VENUE)`, so only that venue's ids are folded, into its own `candles_<venue>.db`. The path comes from `db_path_for_venue` (AD-D12). `--venue` is validated against `kernel.venues.VENUE_KINDS`. The `--db` callers (`archive.nightly`) are unchanged. This follow-up review pass hardened the edges: an empty `--candles-dir` is refused, the docstring is accurate and carries a `Known limit:`, and the Makefile guard has a test.

**Files changed:**
- `platform/candles/rebuild.py`: `--db`/`--candles-dir` group, `--venue` choices, and `_venue_store` resolution and checks (the empty dir is now refused); the docstring has a `Known limit:` for `--db` without `--venue`.
- `platform/candles/tests/test_rebuild.py`: tests for the per-venue store, a missing venue, a missing dir, an empty dir, exactly-one-store-option and an unknown venue.
- `platform/Makefile`: the `build-candles` VENUE guard and arguments, and the comment.
- `platform/tests/test_compose_profiles.py`: static regressions for the `build-candles` command and its VENUE guard.
- `platform/README.md`, `platform/docs/DATA_INTEGRITY_AUDIT.md`, `platform/CLAUDE.md` (DATA-05): point the operator at the per-venue target.

**Review (follow-up pass):** 5 low patches applied, 0 deferred, 14 rejected. The rejects were duplicates of the first pass's two deferrals, or pre-existing or out-of-scope items: the `--db`/`--venue` mismatch, case sensitivity, the `collector` service env, no all-venues loop, the `--include-open-day` make variable, read-only dirs, and test-shape nits.

**Carried over from the first pass for the orchestrator to record (not written to the ledger):**
- `--db` without `--venue` still folds every venue into one file (now a documented `Known limit:` in `candles/rebuild.py`).
- Earlier `make build-candles` runs may have left Bybit/Hyperliquid rows in `candles_dydx.db`. Nothing detects or purges them.

**Verification:**
- `cd platform && python3 -m pytest candles/tests tests/test_compose_profiles.py tests/test_boundaries.py archive/tests/test_nightly.py archive/tests/test_step_ledgers.py -q`: 259 passed.
- `ruff check`, `ruff format --check` and `mypy` on the 3 changed .py files: clean.
- `make build-candles COMPOSE=echo` prints the usage line and exits non-zero. `VENUE=BYBIT` echoes `... candles.rebuild --catalog /app/catalog --candles-dir /app/candles_dir --venue BYBIT`.

**Residual risk:** the operator runs the target once per venue. A VPS with old store state should get a full per-venue rebuild (already owed by D-145 / DEPLOY_CHECKLIST 31-11).
