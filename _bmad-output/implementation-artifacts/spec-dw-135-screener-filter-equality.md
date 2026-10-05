---
title: 'DW-135: screener filters compare at displayed precision'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: 'f779c59498d436292e89b5f754942e2221baae38'
baseline_revision: '57b58c58c277b57198fc1f877f7413cc62e5230d'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** The rankings screener's `=` filter (`platform/frontend/src/pages/filters.ts`) uses strict float equality, so `OBI5 = 0.5` never matches a row whose cell shows `0.500` (actual `0.49996`); `>=`/`<=` disagree with the cell the same way. DW-135's second half (Technicals filters blanking the table while values load/error) is already fixed in `RankingsPage.tsx` (`technicalsPending` skip + note) but has no regression test.

**Approach:** Every numeric filter field carries the display precision its cell uses (`(value / scale).toFixed(decimals)`), and every numeric operator compares the actual value *as displayed* against the typed value, in display units — "a filter compares what the cell shows". The precision is declared once per column and shared by the cell formatter and the filter field so they cannot drift. Add tests for both halves.

## Boundaries & Constraints

**Always:** Text conditions stay case-insensitive `=` only. Missing/NaN values still never match. Rank stays the message rank. The `RANKING_COLS` array stays a flat list of `{ key: "...", label: "...", ... }` literals with unchanged key/label sequence (`data_api/tests/test_ranking_columns_mirror.py` parses it). Every cell renders byte-identically to today.

**Block If:** none.

**Never:** No new dependencies. Do not change filter value units (volume24h is still typed in raw USD). Do not edit `_bmad-output/implementation-artifacts/deferred-work.md`. No strict-equality fallback reachable for a numeric field (precision is required by type).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `=` at display precision | OBI5 actual `0.49996` (shows `0.500`), `= 0.5` | match | none |
| More digits than shown | actual `0.49996`, `= 0.49996`, 3 decimals | no match (shown `0.500` ≠ `0.49996`) | none |
| Operators coherent | actual `0.49996`, 3 decimals | `>= 0.5` and `<= 0.5` match, `> 0.5`/`< 0.5` don't | none |
| Negative zero | actual `-0.001`, 2 decimals, `= 0` | match (`-0.00` reads as 0) | none |
| Scaled field | volume24h actual `1234499.9` (shows `1.234M`), `= 1234000` | match | none |
| Technicals output | actual `29.99996` (shows `30.0000`), `= 30` | match | none |
| Technicals loading/errored | Technicals filter set, values query pending or failed with no data | filter skipped, all rows shown, "not applied yet" note (with error text when failed) | error message shown |

</intent-contract>

## Code Map

- `platform/frontend/src/pages/filters.ts` -- `compare`/`applyFilters`, `FilterCondition`.
- `platform/frontend/src/pages/filters.test.ts` -- unit tests.
- `platform/frontend/src/pages/FilterPanel.tsx` -- builds conditions from `FilterField` (`text?` flag); `add()`.
- `platform/frontend/src/pages/RankingsPage.tsx` -- `RANKING_COLS` + `fmt*` helpers, `filterFields`, Technicals cell `value.toFixed(4)`, `technicalsPending`/`skippedFilters` note.
- `platform/frontend/src/pages/RankingsPage.test.tsx` -- "filter panel" describe (`addFilter`, `shownInstruments`), Technicals filter tests (~509-560).

## Tasks & Acceptance

**Execution:**
- [x] `platform/frontend/src/pages/filters.ts` -- add `DisplayPrecision { decimals; scale? }` and `displayed(value, precision)`; make `FilterCondition` a union: text (`op: "="`, `value: string`) | numeric (`value: number`, `precision: DisplayPrecision`); `compare(actual, condition)`: `eq` = `displayed(actual) === value / scale`; `=` is `eq`, `>=` is `raw > value || eq`, `<=` is `raw < value || eq`, `>` is `raw > value && !eq`, `<` is `raw < value && !eq` (see Design Notes); a `Known limit:` comment naming the fixed column precision (a sub-0.00005 price shows and `=`-matches as 0) with the upgrade path (per-instrument precision via `lib/units.ts`) -- DW-135.
- [x] `platform/frontend/src/pages/FilterPanel.tsx` -- `FilterField` becomes a union (text field, or numeric field with required `precision`); `add()` copies the field's precision into the condition, guarding `fieldDef === undefined` explicitly inside `add()` (not via `canAdd`'s aliased narrowing).
- [x] `platform/frontend/src/pages/RankingsPage.tsx` -- give each `RANKING_COLS` entry its `precision` and derive its cell format from it (volume24h: `scale: 1e6, decimals: 3`); a shared `TECHNICALS_PRECISION` (4 decimals) used by both the Technicals cell and its filter fields.
- [x] `platform/frontend/src/pages/filters.test.ts` -- cover every I/O matrix numeric row plus existing text/missing cases on the new signature, and the inequality cases: a threshold finer than shown orders by the raw value (`price 0.00001234 > 0.00001` true; `0.123449 > 0.12344` at 4 decimals true; volume24h `1234450 > 1234400` true), and exactly one of `<`/`=`/`>` holds for any value.
- [x] `platform/frontend/src/pages/RankingsPage.test.tsx` -- page-level `=` at displayed precision on a ranking column and on a Technicals output; Technicals filter pending and errored → rows kept + note. A describe-level `afterEach` sits beside that describe's `beforeEach`, not mid-block.

**Acceptance Criteria:**
- Given a row whose cell shows `0.500`, when the viewer adds `OBI5 = 0.5`, then the row is shown.
- Given a Technicals filter whose values query has not resolved or failed, when the page renders, then no row is hidden by it and the "not applied yet" note is visible.

## Spec Change Log

### 2026-10-05 — Pass 1 loopback
- **Trigger:** review (high): rounding the actual value for `<`/`>`/`<=`/`>=` as well as `=` makes any threshold finer than the column's display unusable — `price > 0.00001` matches no sub-0.00005 coin, `Price > 0.12344` drops 0.123449, `Vol24h > 1234400` drops 1234450.
- **Amended:** `filters.ts` task and Design Notes: equality alone is tolerant; ordering is raw, combined as a strict trichotomy with the `eq` band. Every I/O matrix row still holds unchanged (0.49996 vs 0.5: `=`,`>=`,`<=` match; `>`,`<` don't). Plus a `Known limit:` for fixed column precision, and two low patches (explicit `fieldDef` guard in `add()`, `afterEach` placement).
- **Known-bad state avoided:** inequality filters silently dropping rows whose raw value passes the typed threshold.
- **KEEP:** `DisplayPrecision`/`formatFixed`/`displayed`; discriminated `FilterCondition`/`FilterField` unions; `RANKING_COLS` entries carrying `precision` with formatters derived from it (byte-identical cells, mirror-test-compatible); shared `TECHNICALS_PRECISION`; the page tests for OBI5 `= 0.5`, Technicals `= 30`, and Technicals pending/errored notes with the `technicalsFilterThenRefetch` helper. Pass-1 diff: scratchpad `dw135-pass1-keep.diff`.

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 1: (high 1)
- patch: 2: (low 2)
- defer: 2: (low 1, medium 1)
- reject: 10: (low 10)
- addressed_findings:
  - `[high]` `[bad_spec]` Inequality operators compared the rounded actual value, breaking thresholds finer than the display; spec amended to tolerant-equality + raw-ordering trichotomy, code reverted for re-derivation.
  - `[low]` `[patch]` `FilterPanel.add()` relied on `canAdd` aliased narrowing; carried into re-derivation as an explicit guard.
  - `[low]` `[patch]` Describe-level `afterEach` declared mid-block in `RankingsPage.test.tsx`; carried into re-derivation.

### 2026-10-05 — Review pass (iteration 1)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (medium 1, low 5)
- defer: 1: (medium 1)
- reject: 13: (low 13)
- addressed_findings:
  - `[medium]` `[patch]` An exact raw match finer than the display (0.49996 vs 0.49996 at 3 decimals) failed `>=`/`<=` (found while verifying the re-derivation); `>=`/`<=` now use raw `>=`/`<=` OR the band, with a pinning test.
  - `[low]` `[patch]` Known limit extended: a sub-0.00005 price also fails `Price > 0`, and every Technicals output shares the 4-decimal band.
  - `[low]` `[patch]` volume24h filter-field comment said `<`/`>` compare raw USD unconditionally; now names the shown-equal band and the $1000 `=` granularity.
  - `[low]` `[patch]` `formatFixed` docstring claimed only the unscaled path behaves as before; now states the scaled path coerces, as `fmtMillions` always did.
  - `[low]` `[patch]` Trichotomy test renamed to scope it to thresholds on the display grid (the code holds "at most one" in general).
  - `[low]` `[patch]` The `= 0` assertion on a sub-display price is commented as pinning today's Known limit.

### 2026-10-05 — Review pass (follow-up, fresh review of done spec)
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 0
- reject: 17: (low 17)
- addressed_findings:
  - none

## Design Notes

Comparing in display units avoids float error on the way back: `1234000 / 1e6` is the correctly rounded quotient, identical to `Number("1.234")`, whereas `Number("1.234") * 1e6` need not equal `1234000`. Rounding goes through `toFixed`, the same call the cell makes, so `=` matches exactly what is on screen. The typed value is not rounded: typing more precision than is shown cannot match `=`.

Only equality is tolerant; ordering stays on the raw value. `eq` is a band around the threshold, and the operators partition every value into exactly one of `<`, `=`, `>` (`>=` = `>` or `=`). Rounding the actual for `<`/`>` too (pass 1) broke thresholds finer than the display: on a coin priced 0.00001234 (shown `0.0000`) `price > 0.00001` matched nothing.

```ts
const eq = displayed(actual, p) === value / (p.scale ?? 1);
// ">": actual > value && !eq    ">=": actual > value || eq
```

## Verification

**Commands:**
- `cd platform/frontend && npx vitest run` -- expected: all pass (baseline 694).
- `cd platform/frontend && npx tsc -b && npx oxlint` -- expected: clean.
- `cd platform && python3 -m pytest -o addopts="" data_api/tests/test_ranking_columns_mirror.py` -- expected: pass.

## Auto Run Result

**Summary:** Screener numeric filters match `=` at the precision the cell displays, `(value / scale).toFixed(decimals)`. That precision is declared once per column and shared by the cell formatter and the filter field. Ordering stays on the raw value: `<` and `>` exclude the band of values that display as equal to the threshold, and `>=` and `<=` include it. Every numeric condition carries its precision by type, so no strict-equality path remains. DW-135's second half (Technicals filters blanking rows while values load or error) was already fixed in code; it now has regression tests. This run was a fresh follow-up review of the already-`done` spec and changed no code.

**Files changed (feature, commit 566dee334f):**
- `platform/frontend/src/pages/filters.ts` -- `DisplayPrecision`, `formatFixed`, `displayed`; union `FilterCondition`; `compare(actual, condition)` with tolerant equality and a `Known limit:`.
- `platform/frontend/src/pages/FilterPanel.tsx` -- union `FilterField` (numeric fields require `precision`); `add()` copies it into the condition.
- `platform/frontend/src/pages/RankingsPage.tsx` -- `RANKING_COLS` entries carry `precision`, formatters derive from it (cells byte-identical); shared `TECHNICALS_PRECISION`.
- `platform/frontend/src/pages/filters.test.ts` -- I/O matrix, finer-than-display ordering, trichotomy and exact-raw-match tests.
- `platform/frontend/src/pages/RankingsPage.test.tsx` -- OBI5 `= 0.5`, Technicals `= 30`, Technicals filter pending/errored keep rows + note.

**Review findings:** this follow-up pass: 0 patches, 0 deferred, 17 rejected. Rejected findings fall into three groups:
- Behaviour the spec mandates: strict `>`/`<` exclude the band shown as equal; `-0.00` reads as 0 per the I/O matrix; an `=` value off the display grid cannot match.
- Residual risks already documented in the triage log and the `Known limit:` comment: non-monotonic band edge, fixed column precision.
- Pre-existing or hypothetical: scaled-string coercion, which `fmtMillions` always did; hex input accepted by `Number()`; unvalidated constant precisions; `"M"` suffix separate from `scale`.

Earlier passes: pass 1 found 1 bad_spec (loopback) and 2 patches; pass 2 applied 6 patches.

**Deferred (not written to the ledger, per the bundle instruction; carried from the prior run):**
- Pre-existing: `RankingsPage` passes the pruned `filters` list to `FilterPanel`, and its `onChange` replaces `allFilters`. So adding or removing any chip while a Technicals column is removed permanently drops that column's hidden condition, contradicting the comment that it "comes back if the same column is re-added".
- Display quirk (low, pre-existing): `fmtSigned` renders -0.001 as `-0.00` but +0.001 as `+0.00`; the filter treats both as 0.

**Verification (this pass, on HEAD 566dee334f):**
- `npx vitest run`: 38 files / 708 tests passed.
- `npx tsc -b`: clean.
- `npx oxlint`: 3 warnings, all pre-existing and outside touched files.
- `data_api/tests/test_ranking_columns_mirror.py`: 1 passed.

**Residual risks:**
- The equality band uses fixed column precision. On sub-0.00005 prices and price-unit Technicals outputs, `Price > 0` excludes rows showing `0.0000`. This is a documented `Known limit:`; the upgrade path is per-instrument precision via `lib/units.ts`.
- Strict thresholds are non-monotonic at the band edge.
- volume24h `=` matches only multiples of $1000, and the panel gives no hint of the precision.
