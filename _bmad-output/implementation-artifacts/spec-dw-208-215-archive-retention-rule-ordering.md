---
title: 'DW-208/DW-215: archive retention rule ordering (definitions wait for trades, plan beats age rule)'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: '67069f5c286b7a186906a3df5807791378cd1df5'
baseline_revision: 'fab514878b982cb6bdc4d90d49f465b99ce60052'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** `RetentionPolicy.decide()` (`platform/archive/domain/retention.py`) decides its rules independently, and two interactions lose data. Rule `dropped_instrument` deletes a dropped dYdX coin's instrument-definition leaves while its unverified `trade_tick` days are kept, so those days can never be reconciled and stay forever (DW-208). The generic `age` rule (`make prune`: `order_book_deltas`, 14 days) deletes raw deltas of a dYdX instrument whose plan says `retain_hours = None` (unlimited), because `make prune` never passes the plan and the age rule runs before, and regardless of, the plan's per-instrument delta retention (DW-215).

**Approach:** Human decisions 2026-10-05. (1) Gate rule `dropped_instrument`'s deletion of definition leaves on the coin's trade days: a definition file goes only when no `trade_tick` file of that instrument is left in the listing (every trade day released). (2) Plan wins: an `order_book_deltas` file of a dYdX instrument the plan gives a delta retention entry (finite or `None`) is never chosen by the `age` rule; its deltas are governed by `delta_retention` alone. `make prune`/`make prune-dry` pass `--dydx-plan` so the domain sees the plan.

## Boundaries & Constraints

**Always:** Keep `RetentionPolicy` pure (stdlib + `kernel` imports only, `tests/test_boundaries.py`). Every deletion still names one of the four rules. Fail-safe direction: when in doubt, keep. Definition directory names come from Nautilus's `class_to_filename`, defined once in `kernel/catalog_files.py` and reused by `verification/infrastructure/derivs_reader.py` (no second literal tuple). Update module docstrings (`retention.py`, `prune_catalog.py`) and the Makefile comment to state the new orderings.

**Block If:** Keeping definitions or deltas would require changing `nautilus_trader/` or `crates/`.

**Never:** No edit of the deferred-work ledger. No change to the trade rule's verification gate, the open-day invariant, the captured-now guard, or `DydxPlanFile`'s plan-read refusals. No age-rule exemption for non-delta types or non-dYdX venues.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Dropped coin, trades kept | dropped DYDX iid: old `crypto_perpetual` file + old unverified `trade_tick` file + old snapshot | snapshot deleted (`dropped_instrument`); definition and trades kept | none |
| Dropped coin, trades released this run | same, trade day verified and old; trade rule deletes it | trades deleted (`trade`); definition still kept (trade file was in the listing) — goes next run | none |
| Dropped coin, no trades left | dropped iid: old definition file, no `trade_tick` file | definition deleted (`dropped_instrument`) | none |
| Dropped coin, unparsable trade name | trade file with `span=None` | counts as a trade file: definition kept | reported unparsable as today |
| Unlimited plan deltas, age rule on | `age_types={order_book_deltas}`, plan `retain_hours=None`, 30-day-old deltas | kept | none |
| Finite plan deltas longer than age | plan `retain_hours=720`, 20-day-old deltas, `age_days=14` | kept (plan wins) | none |
| Finite plan deltas shorter than age | plan `retain_hours=48`, 3-day-old deltas | deleted, rule `delta_retention` (not `age`) | none |
| Age rule without plan / non-plan iid | no plan, or Bybit / DYDX iid without delta entry | `age` rule unchanged | none |

</intent-contract>

## Code Map

- `platform/archive/domain/retention.py` -- `RetentionPolicy.decide`/`_other`/`_dropped`/`_delta`: the four rules; module docstring lists them.
- `platform/kernel/catalog_files.py` -- read-only catalog helpers; home of `class_to_filename`-derived dir names (`SNAPSHOT_DIRNAME`, `TRADE_DIRNAME`).
- `platform/verification/infrastructure/derivs_reader.py:79` -- local `DEFINITION_DIRS` literal tuple (crypto_perpetual, currency_pair, crypto_future) to replace with the kernel constant.
- `platform/archive/prune_catalog.py` -- CLI; docstring describes plan retention and the age rule.
- `platform/archive/application/prune.py` -- `_wanted` lists every type of a dYdX leaf when a plan is set (trade leaves included), so the domain sees trade files for the gate.
- `platform/Makefile:460-472` -- `prune`/`prune-dry` targets (no `--dydx-plan` today; `nightly` already passes `/app/dydx_collector/config.toml`).
- `platform/archive/tests/test_retention.py` -- pure policy tests (`_file`, `_day_file`, `_PLAN` helpers).
- `platform/archive/tests/test_prune.py` -- CLI end-to-end over a tmp catalog (`_write_parquet`, `_dydx_plan`).

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/catalog_files.py` -- add `DEFINITION_DIRNAMES` = `class_to_filename` of `CryptoPerpetual`, `CurrencyPair`, `CryptoFuture` -- one source for instrument-definition leaf names.
- [x] `platform/verification/infrastructure/derivs_reader.py` -- use the kernel constant instead of its literal `DEFINITION_DIRS` -- no drift.
- [x] `platform/archive/domain/retention.py` -- in `decide`, collect instruments with any listed `trade_tick` file; `_dropped` keeps a definition-type file of such an instrument. In `_other`, skip the `age` rule for an `order_book_deltas` file of a DYDX iid present in `plan.delta_retain_hours`. Update module/class docstrings with both orderings -- DW-208, DW-215.
- [x] `platform/archive/prune_catalog.py` -- docstring: age rule cedes plan-governed dYdX deltas; dropped definitions wait for trades.
- [x] `platform/Makefile` -- `prune`/`prune-dry` add `--dydx-plan /app/dydx_collector/config.toml`; comment states plan retention now runs there too.
- [x] `platform/archive/tests/test_retention.py` -- domain tests for every I/O matrix row.
- [x] `platform/archive/tests/test_prune.py` -- one CLI test: `--types order_book_deltas --days 14 --dydx-plan` keeps an unlimited-plan instrument's old deltas and deletes a non-plan dYdX instrument's.

**Acceptance Criteria:**
- Given a dropped dYdX coin with any `trade_tick` file in the catalog, when retention runs with the plan, then none of its instrument-definition files is deleted, while its other non-trade types still age out.
- Given a collected dYdX instrument with a plan delta entry, when `make prune` runs, then its deltas are decided by `delta_retention` alone and an unlimited entry's deltas are never deleted.
- Given the full platform test suite, when run, then it passes with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 1, low 2)
- defer: 0
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` A held definition was kept silently (no report line): the domain now records it in `kept` as `definition_held_by_trades` (`DEFINITION_HELD`, via `_held`), `prune.decide` never relabels it with a same-day trade fault reason, and `log_summary` counts it on the plan line, not the trade line. Tests in `test_retention.py` / `test_prune.py`.
  - `[low]` `[patch]` Makefile `prune` comment still claimed the target bounds all delta storage: reworded (plan-governed deltas are bounded by the plan).
  - `[low]` `[patch]` Spec deviation, one possible reading: the Always clause asked `derivs_reader.py` to import the kernel constant, but `tests/test_boundaries.py` (`VERIFICATION_DENIED_MODULES`, DATA-02) forbids it; it keeps its literal with a drift test (`verification/tests/test_derivs.py`) holding it equal to `DEFINITION_DIRNAMES`.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 0, low 3)
- defer: 1 (high 0, medium 0, low 1)
- reject: 13
- addressed_findings:
  - `[low]` `[patch]` A held definition's kept entry was keyed by the file's end day while `prune._execute_one` keys a kept file by its first day: `_held` now uses `span[0]` (docstring states it).
  - `[low]` `[patch]` The unbounded hold (a trade file never released: `failed` day, unparsable name, or a run without the trade rule such as `make prune`) was only implied: the module docstring now states it as the fail-safe ceiling, reported every run.
  - `[low]` `[patch]` `log_summary`'s held count and the trade line's `- held` subtraction had no test: added `test_the_summary_counts_held_definitions_on_the_plan_line_not_the_trade_line` (`test_prune.py`).

## Design Notes

DW-208 gate reading: the decision says "only once the trade days are verified or released". A verified day whose file the trade rule has not yet deleted (younger than the trade window) still keeps the definition: a later rebuild or repair clears its verdict (DW-203) and the reconcile then needs the definition again. So the gate is "no trade file of the instrument in this listing", i.e. every day released; a trade file deleted in this same run still holds the definition until the next run, which also covers a trade deletion that fails at execution (marker failure, open day). A dropped coin with mismatched days therefore keeps its (tiny) definition files as long as those trades stay -- the fail-safe direction.

DW-215 scope: "plan wins" applies to every plan delta entry, not only `None`: a finite `retain_hours` longer than `age_days` would otherwise still be cut at 14 days. A finite one shorter is unchanged in effect (deleted by `delta_retention`, named correctly).

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests/test_retention.py archive/tests/test_prune.py verification tests/test_boundaries.py -q` -- expected: all pass
- `cd platform && ruff check archive kernel verification && ruff format --check archive kernel verification && mypy archive/domain/retention.py kernel/catalog_files.py` -- expected: clean

## Auto Run Result

Status: done

**Summary:** Follow-up review of the shipped DW-208/DW-215 change (`6ca8d50d6a`). `RetentionPolicy.decide` holds a dropped dYdX coin's instrument-definition files, reported as `definition_held_by_trades`, while any `trade_tick` file of the coin is listed (DW-208). The `age` rule never takes `order_book_deltas` of a dYdX instrument that has a plan delta entry, and `make prune`/`prune-dry` pass `--dydx-plan` (DW-215). This pass made three small fixes and found no spec or intent problems.

**Files changed this pass (platform/):**
- `archive/domain/retention.py` -- held entry keyed by the file's first day; docstring states the unbounded fail-safe hold.
- `archive/tests/test_prune.py` -- summary test for the held count on the plan line and its exclusion from the trade line.

**Review:** Blind Hunter and Edge Case Hunter raised 19 raw findings, 14 after dedup: 3 patched (low), 1 deferred (a mypy `arg-type` error at `kernel/catalog_files.py:382` that existed before this change, appended to the ledger), 10 rejected. The rejected ones: `make prune` failing on an unusable plan / running the dropped-instrument rule (the 2026-10-05 decision; `make nightly` already behaves this way for every venue); a torn plan read (the change only narrows deletion, compared with the old no-plan `make prune`); `--types crypto_perpetual` bypassing the hold (the spec's Never clause: no age-rule exemption for non-delta types); the per-instrument hold (by design); a closed definition list (hypothetical); the domain importing `kernel.catalog_files` (the spec's Always clause, which the boundary test allows); `held` counting days, not files (the log line says day(s)); the trade-line kept count including `marker_failed`/`status_unreadable` (trade-rule outcomes, the same as before); and the `span is None` guard (needed for typing).

**Verification:** `python3 -m pytest archive/tests verification/tests/test_derivs.py tests/test_boundaries.py kernel/tests -q -W error::DeprecationWarning` (from `platform/`): 1261 passed. `ruff check` + `ruff format --check` on archive/kernel/verification: clean. `mypy` on `archive/domain/retention.py archive/application/prune.py kernel/catalog_files.py`: one error, `kernel/catalog_files.py:382`, which reproduces with this pass's changes stashed and is deferred.

**Residual risks:** Unchanged from the first run. `make prune` exits 1 on an unusable dYdX plan, as `make nightly` does. A dropped coin whose trades never release keeps its small definition files indefinitely, now documented and reported every run.
