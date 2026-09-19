# Story 19.6: CEX/DEX registry

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want to know whether a venue is a CEX or a DEX,
so that I can filter or reason about counterparty/custody risk differences.

## Acceptance Criteria

1. **A small, self-maintained registry — `troll/common/venues.py` — maps each known venue to its kind**: `{"DYDX": {"kind": "dex"}, "HYPERLIQUID": {"kind": "dex"}, "BYBIT": {"kind": "cex"}}`. A plain dict, not a class hierarchy or plugin system (DESIGN-01).
2. **No Nautilus-native mechanism is used for this** — confirmed `Venue.is_dex()` (`crates/model/src/defi`) only fires on a `"<Chain>:<DexType>"`-formatted venue string behind the `defi` cargo feature, and none of dYdX's, Bybit's, or Hyperliquid's adapter constants use that format (all three are flat strings) — so it would never return `true` for any of this project's actual venues, DEX or not.
3. **The registry powers a CEX/DEX label or filter option in the screener**, confirmed against all three real venues once Stories 19.3-19.5 exist.

## Tasks / Subtasks

- [x] Task 1 — Registry (AC: #1, #2)
  - [x] Create `troll/common/venues.py` with the plain dict above. No dependency on `crates/model/src/defi` or any Nautilus `is_dex()` mechanism.
- [x] Task 2 — Surface in the screener (AC: #3)
  - [x] Expose `kind` alongside `venue` wherever `venue` is already surfaced (Story 19.1's response shapes, Story 19.5's Rankings column) — either a small badge/label next to the venue column, or a filterable field (`kind = dex`), reusing Story 17.6's filter mechanism.
- [x] Task 3 — Tests
  - [x] A test confirming the registry returns the correct kind for all three known venues, and a defined (not crashing) behavior for an unknown venue (e.g. `None`/`"unknown"`, not a `KeyError`).

## Dev Notes

- **This is intentionally the smallest possible implementation** — a static dict, maintained by hand as new venues are added (Epic 19 adds exactly two; if a fourth venue is ever added later, this file gets one more entry). No abstraction beyond that is justified yet.
- **Do not attempt to derive `kind` from Nautilus's `Venue.is_dex()`** — confirmed it structurally cannot distinguish any of this project's three real venues correctly (see AC #2's reasoning), so relying on it would produce a silently wrong answer for every venue, not just an unsupported one.

### Project Structure Notes

- New: `troll/common/venues.py`.
- Modified: wherever `venue` is surfaced (Story 19.1's response shapes; `RankingsPage.tsx`, Story 19.5).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.6] — this story's origin (FR66).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D6] — the registry design and the `Venue.is_dex()` non-applicability finding.
- [Source: crates/model/src/defi] — confirmed (via prior session research) `is_dex()`'s `Chain:DexType` format requirement and the `defi` cargo feature gate.
- [Source: _bmad-output/implementation-artifacts/19-1-explicit-venue-field-in-the-data-model.md, 19-5-rankings-screener-venue-column-and-filter.md] — the `venue` field and column this story's `kind` label attaches to.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- `troll/common/venues.py`: plain `VENUE_KINDS` dict + `venue_kind()` (unknown venue → `"unknown"`, never raises). No Nautilus `is_dex()`.
- Surfaced on rankings only: `ranking_engine._current_ranks()` rows gain `venue_kind`; RankingsPage gets a Kind column and a `Kind (cex/dex)` text filter (reusing 19.5's text-filter support). Did not add `kind` to the candles/snapshots response shapes — nothing consumes it there (YAGNI); one line each if wanted.
- `collector.dockerfile` now COPYs `troll/common` (ranking_engine imports it). Full troll suite in the image with a throwaway Redis: 701 passed (27 pre-existing Pandas4Warning/deprecation warnings from ml_signals, unrelated to this epic).

### File List

- troll/common/{__init__,venues}.py, common/tests/{__init__,test_venues}.py (new)
- troll/ranking_engine/engine.py, tests/test_engine.py; troll/frontend/src/pages/{RankingsPage.tsx,RankingsPage.test.tsx}
- troll/collector.dockerfile, troll/Makefile
