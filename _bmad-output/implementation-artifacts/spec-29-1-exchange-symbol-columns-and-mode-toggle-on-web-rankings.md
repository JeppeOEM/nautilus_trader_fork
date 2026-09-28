---
title: 'Story 29.1: Exchange and Symbol on the web rankings, sortable and filterable'
type: 'feature'
created: '2026-09-28'
status: 'done'
baseline_revision: 'b931190458a137392f837cad70002a3955ef6075'
final_revision: '311c881fb468118fd07352b62ace102474d81dd8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-29-context.md'
warnings: [oversized]
---

<intent-contract>

## Intent

**Problem:** The rankings are already multi-venue, but the exchange appears only as the id's suffix and there is no base-symbol field. As a result, `BTC` on Bybit and `BTC` on Hyperliquid cannot be lined up, sorted together or filtered as one coin.

**Approach:** Add one kernel helper, `kernel.venues.base_symbol`, which derives the base from the id (SIGNAL-01). Publish it as a new `symbol` field on every `rankings:live` rank entry, next to `venue` (added field only, per 25.2's wire rule). Render `Symbol` and `Exchange` (`venue` plus a `market` tag) as pinned, sortable, `=`-filterable columns between Rank and Instrument on the web page. The ranking-mode toggle named in the slug already shipped on the web in Story 25.1a, so this story only keeps it working.

## Boundaries & Constraints

**Always:**
- `base_symbol` is pure. It raises `MalformedInstrumentId` only for an id with no `.VENUE` suffix, and it never guesses:
  - Dashed venues (dYdX, Hyperliquid) take the symbol's first `-` segment.
  - Bybit takes the head before the first `-`, minus the first matching suffix from the named constant `BYBIT_QUOTES = ("USDT", "USDC", "PERP", "USD")`, and only if a non-empty base remains. Otherwise the head is returned whole.
  - An unknown venue takes the first `-` segment.
- A `Known limit:` comment names the ceiling (a base that itself ends in a quote name, or an unlisted quote) and the upgrade path (the venue's instrument definition, which the catalog stores: its `base_currency`).
- The rank entry's existing fields stay byte-identical and in the same order, and `symbol` is inserted right after `venue`. `metrics.db` stores no symbol (never stored twice).
- Default row order stays the message order, which is the true rank. A Symbol or Exchange sort is an explicit viewer choice: clicking a header cycles asc, then desc, then back to rank order. Ties always break by ascending rank. A row missing the field sorts last.
- Rank shown in a row is always its message rank, even when the table is sorted or filtered.

**Block If:**
- Adding `symbol` would require changing or removing an existing rank-entry field or its bytes.

**Never:**
- No `symbol` computed in TS or `data_api`. The page reads the published field only.
- No change to `RANKING_COLS` metric columns or the mirror test: Symbol and Exchange are pinned identity columns like Rank and Instrument.
- No changes to venue chips, the ranking-mode control, the TUI, `nautilus_trader/` or `crates/`.
- Do not write `sprint-status.yaml`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| dYdX perp | `BTC-USD-PERP.DYDX` | `BTC` | — |
| Bybit linear/spot | `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT` | `BTC` | — |
| Bybit other quotes | `ETHUSDC-SPOT.BYBIT`, `BTCUSD-INVERSE.BYBIT`, `BTCPERP-LINEAR.BYBIT` | `ETH`, `BTC`, `BTC` | — |
| Numeric prefix | `1000PEPEUSDT-LINEAR.BYBIT` | `1000PEPE` | — |
| Dated future | `BTCUSDT-25SEP26-LINEAR.BYBIT` | `BTC` | — |
| Unlisted quote | `ETHBTC-SPOT.BYBIT` | `ETHBTC` (whole head, no guess) | — |
| Hyperliquid | `SOL-USD-PERP.HYPERLIQUID`, `HYPE-USDC-SPOT.HYPERLIQUID`, `km:US500-USD-PERP.HYPERLIQUID` | `SOL`, `HYPE`, `km:US500` | — |
| Malformed | `BTCUSDT` (no venue) | — | raises `MalformedInstrumentId` |
| Older producer | rank entry without `symbol` | Symbol cell `—`, sorts last | — |

</intent-contract>

## Code Map

- `platform/kernel/venues.py` -- the only `InstrumentId` parser. It gains `BYBIT_QUOTES` and `base_symbol`.
- `platform/kernel/tests/test_venues.py` -- the id-shape tables.
- `platform/ranking/domain/board.py:435-441` -- `RankingBoard._rank_row` builds the rank entry (`venue`, `venue_kind`, `market`).
- `platform/ranking/tests/test_replay.py` + `fixtures/replay_burst.json` -- the 25.2 byte-replay (the sha256 of every published message).
- `platform/ranking/tests/test_board.py:100` -- the rank-entry identity-field assertion.
- `platform/frontend/src/pages/RankingsPage.tsx` -- the table. Pinned Rank/Instrument are rendered on both tabs, and the Performance tab also renders Venue/Kind/Market columns. It holds `filterFields` and the empty-state `colSpan={2}`.
- `platform/frontend/src/pages/filters.ts` -- `applyFilters` (order-preserving) and text `=` match.
- `platform/frontend/src/pages/RankingsPage.test.tsx` -- page tests. Tests around line 407+ assert the `Venue`/`Market` columnheaders.
- `platform/frontend/src/index.css:81-135` -- `.rankings-table` styles (auto layout).
- `platform/views/ranking_columns.py` -- `RANKING_COLS` docstring (the pinned columns are not part of it).
- `platform/docs/DATA_DICTIONARY.md` §3.3, §2.10; `platform/ARCHITECTURE.md:35` (the kernel helper list).

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/venues.py` -- Add `BYBIT_QUOTES` and `base_symbol(instrument_id) -> str` per Always. The docstring carries the `Known limit:`. The module docstring mentions it. -- One kernel parser (AD-D3, SIGNAL-01).
- [x] `platform/kernel/tests/test_venues.py` -- Add a parametrized table covering every I/O-matrix row plus the malformed raise. -- TEST-01.
- [x] `platform/ranking/domain/board.py` -- Add `"symbol": base_symbol(iid)` right after `"venue"` in `_rank_row`. -- Added wire field.
- [x] `platform/ranking/tests/test_replay.py` -- Extend the test in three ways. Every published rank carries `symbol == base_symbol(instrument_id)`, placed right after `venue`. With `symbol` removed from each rank and the message re-serialized with `json.dumps`, every message hashes to the recorded `publish_sha256`, and the final message equals `final_message`. Leave the fixture unchanged. -- This proves the existing bytes are identical.
- [x] `platform/ranking/tests/test_board.py` -- Assert `symbol` alongside venue/kind/market.
- [x] `platform/frontend/src/pages/RankingsPage.tsx` -- Make these changes:
  - Add pinned `Symbol` and `Exchange` headers and cells between Rank and Instrument on both tabs. The Exchange cell shows `venue`, with `market` as a small `.rankings-market-tag` (`BYBIT · spot`).
  - Remove the now-duplicate Performance-tab `Venue` and `Market` columns. Keep `Kind`.
  - Add sort state with header buttons (`aria-sort` on the `th`) per Always. Sort after the chips and filters, keeping each row's message rank.
  - Add a `{key:"symbol", label:"Symbol", text:true}` filter field. Relabel the `venue` field to `Exchange (venue)`, keeping the key.
  - Set the empty-state `colSpan` to 4.
  - Narrow the instrument id with a truncating span (`.rankings-instrument`, `title` = the full id).
  - Update the comments that say rows are never re-sorted.
- [x] `platform/frontend/src/pages/filters.ts` -- Change the doc comment only, to say that order-preserving holds and sorting is the page's explicit viewer choice.
- [x] `platform/frontend/src/index.css` -- Add `.rankings-market-tag` (small, dim) and `.rankings-instrument` (inline-block, max-width, ellipsis).
- [x] `platform/frontend/src/pages/RankingsPage.test.tsx` -- Update the Venue/Market column tests to the Exchange column. Add tests for:
  - Same-symbol rows on different exchanges sort adjacent, with ties by rank.
  - Desc and back-to-rank cycling.
  - An Exchange (venue) filter hides the other venue's rows.
  - `symbol = BTC` shows both BTC rows and hides the others.
  - The market tag renders.
  - A missing symbol renders `—` and sorts last.
- [x] `platform/views/ranking_columns.py`, `platform/docs/DATA_DICTIONARY.md`, `platform/ARCHITECTURE.md` -- Document that Symbol and Exchange are pinned identity columns outside `RANKING_COLS`. List the rank entry's identity fields (`instrument_id`, `venue`, `symbol`, `venue_kind`, `market`, `rank`) in §3.3. Add `base_symbol` to the kernel list. -- MR4.

**Acceptance Criteria:**
- Given a `rankings:live` message with `BTCUSDT-LINEAR.BYBIT` (rank 1), `ETH-USD-PERP.DYDX` (rank 2) and `BTC-USD-PERP.HYPERLIQUID` (rank 3), when the viewer clicks the Symbol header, then the two BTC rows are adjacent (Bybit then Hyperliquid, by rank) and every row still shows its message rank.
- Given the 25.2 replay, when it runs after this story, then only `symbol` differs from the recorded bytes.
- Given the Technicals tab, when it renders, then Rank, Symbol, Exchange and Instrument are still the leading columns.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 10: (high 0, medium 1, low 9)
- defer: 1: (high 0, medium 1, low 0)
- reject: 7: (high 0, medium 0, low 7)
- addressed_findings:
  - `[medium]` `[patch]` The replay test checked `symbol` against `base_symbol` itself, so the check was circular. It now checks against an explicit `EXPECTED_SYMBOLS` table for the six replay instruments.
  - `[low]` `[patch]` `base_symbol` returned `""` for an empty first `-` segment (`-LINEAR.BYBIT`). It now returns the symbol whole, which is tested and documented in the `Known limit:`.
  - `[low]` `[patch]` The `Known limit:` now also names the multiplier-named base (`1000PEPE` vs `kPEPE`), matching `asset_key`.
  - `[low]` `[patch]` Replaced the change-detector test on the `BYBIT_QUOTES` tuple with behaviour tests (the ETH heads for USDT, USDC and USD). Added a test pinning the named ceiling (`ETHBUSD` → `ETHB`).
  - `[low]` `[patch]` Symbol/Exchange sorting used code-point order while the `=` filter ignores case. It now compares case-insensitively, with exact case as a locale-independent tie-break; tested with `km:US500`.
  - `[low]` `[patch]` An Exchange sort now groups a venue's markets (perp, then spot) before the rank tie-break; tested.
  - `[low]` `[patch]` The Exchange cell rendered `— · spot` when `venue` was missing. The tag now needs a venue; tested.
  - `[low]` `[patch]` An empty-string `symbol` rendered blank and sorted first. `textField` now treats `""` as missing; tested.
  - `[low]` `[patch]` The hard-coded `colSpan={4}` is now `PINNED_COLUMN_COUNT`.
- deferred: `ranking/tests/test_metrics_store.py`'s wall-clock flake, which surfaced in the full-suite run (see deferred-work.md).
- rejected: the two Bybit tables disagreeing (by design: `asset_key` is a strict matcher, `base_symbol` a display grouping); unknown-venue raw heads (spec-mandated); rank-driven reorder inside a sorted group (spec: ties by the current rank); the replay's `json.dumps` coupling (it would fail loudly); Kind not folded into the Exchange cell (the spec keeps Kind); the malformed-message assertion; `PERP` on a `-SPOT` head (not a real Bybit shape).

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 1, low 4)
- defer: 0
- reject: 15: (high 0, medium 0, low 15)
- addressed_findings:
  - `[medium]` `[patch]` The replay compared messages only after `_without_symbol` re-serialized them, so a change to the engine's own serialization (separators, escaping) would have passed. Each raw message is now asserted to equal `json.dumps` of its own decoding, which pins its bytes.
  - `[low]` `[patch]` The `base_symbol` docstring said "never guesses", which its own `Known limit:` (`ETHBUSD` -> `ETHB`) contradicted. It now says an unlisted quote is never guessed at.
  - `[low]` `[patch]` The `Known limit:` now also names Hyperliquid HIP-3 dex-prefixed bases (`xyz:BTC`), which never line up with `BTC`.
  - `[low]` `[patch]` `onSort` read `sort` from the render closure. It now uses a functional `setSort` updater.
  - `[low]` `[patch]` DATA_DICTIONARY §2.10 now states the Exchange sort's secondary key (market, `perp` before `spot` ascending).
- rejected: two Bybit quote tables (by design, as in the first pass); `BYBIT_QUOTES` public (spec-named); `-LINEAR` returned whole (first-pass decision, spec raises only without `.VENUE`); Hyperliquid spot id shapes (not collected); REST seed missing `symbol` (checked: `/api/rankings` returns the bus's `ranks` verbatim); market hidden without venue (first-pass decision); Kind column (spec keeps it); hand-kept `PINNED_COLUMN_COUNT`; filter-label migration (checked: filters are not persisted, only venue chips by key); per-publish parse cost (a string split per row); DEPLOY_CHECKLIST `this story's` (the file's existing convention); instrument truncation on touch; test-helper DOM coupling; dated inverse ids (capture refuses INVERSE); an `unknown` market tag (honest display of the published value).

## Design Notes

- **Market tag:** the AC's `BYBIT · linear` example conflicts with its own normative text, which says "with `market` shown". `market` is `market_kind` (`perp`/`spot`), so the tag reads `BYBIT · perp` / `BYBIT · spot`. Deriving `linear` would need either a second id parse in TS (forbidden by SIGNAL-01) or another wire field outside this AC. The perp/spot distinction is what the operator lines up.
- **Removing the Venue and Market columns:** the Exchange cell now carries exactly those two values, so keeping them would show the same datum twice. Their filter fields stay.
- **Replay test:** strip-and-rehash instead of re-recording the fixture. Re-recording would prove nothing about the existing bytes.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests ranking/tests views/tests data_api/tests/test_rankings.py data_api/tests/test_ranking_columns_mirror.py -q` -- expected: all pass, with no new warnings.
- `cd platform/frontend && npx vitest run src/pages` -- expected: all pass.
- `cd platform/frontend && npx tsc -b && npx oxlint` -- expected: clean.
- `ruff check` + `ruff format --check` + `mypy` on the changed Python files -- expected: clean.


## Auto Run Result

Status: done

**Summary:** This was a follow-up review of the shipped Story 29.1, covering `base_symbol`, the `symbol` rank-entry field, and the web Symbol and Exchange columns with their sorting and filtering. It found no intent gap and no bad-spec finding. It applied 5 patches, which tighten the replay test, the documentation and the sort-state handling. There were no behaviour changes on the wire.

**Files changed (this pass):**
- `platform/ranking/tests/test_replay.py`: asserts each raw published message equals `json.dumps` of its own decoding, so the strip-and-rehash cannot hide a serialization change.
- `platform/kernel/venues.py`: `base_symbol`'s docstring wording, and a HIP-3 dex-prefix line in its `Known limit:`.
- `platform/frontend/src/pages/RankingsPage.tsx`: `onSort` uses a functional `setSort` updater.
- `platform/docs/DATA_DICTIONARY.md`: §2.10 states the Exchange sort's secondary key.

**Review:** 5 patches (1 medium, 4 low), 0 deferred, 15 rejected. See the Review Triage Log.

**Follow-up review recommended:** false. The fixes are few, localized and low-consequence.

**Verification:**
- `python3 -m pytest kernel/tests ranking/tests views/tests data_api/tests/test_rankings.py data_api/tests/test_ranking_columns_mirror.py -q`: 513 passed, 1 failed. The failure is `test_rankings_live_message_reflected_by_rest_and_ws_relay`, which needs a local Redis on 127.0.0.1:6379 (connection refused). It is one of the known Redis-dependent failures.
- `npx vitest run src/pages`: 129 passed. `npx tsc -b` is clean. `npx oxlint src/pages/RankingsPage.tsx` is clean.
- `ruff check`, `ruff format --check` and `mypy --disallow-incomplete-defs` on the changed Python files are clean.

**Residual risks:**
- `base_symbol` splits by suffix, so it has the ceiling its `Known limit:` names: `ETHBUSD` reads `ETHB`, and multiplier and HIP-3 dex-prefixed bases do not line up. The upgrade path is the instrument definition's `base_currency`.
- The market tag reads `perp`/`spot` rather than the AC example's `linear`, as the Design Notes explain.
