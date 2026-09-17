# Story 19.2: Consolidate `data_api`'s `CATALOG_PATH` into one shared setting

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a backend developer,
I want one `CATALOG_PATH` source instead of six independently-duplicated defaults,
so that adding a second collector's catalog doesn't require editing multiple files in lockstep.

## Acceptance Criteria

1. **`CATALOG_PATH = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")` — independently redeclared today in `app.py`, `routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py` (each to avoid a circular import from `app.py`, per each file's own comment) — is consolidated to one source of truth.**
2. **The circular-import problem each file's comment documents is not reintroduced** — the consolidation must not make `routes/*.py` import `CATALOG_PATH` from `app.py` if that's what caused the original duplication.
3. **A second collector's catalog output (Bybit/Hyperliquid, Stories 19.3/19.4) is served by the existing routes with zero additional per-route code**, once all collectors write into the shared catalog root this setting now points at.
4. **No behavior change for existing dYdX-only deployments** — same default path, same env var name.

## Tasks / Subtasks

- [ ] Task 1 — Identify why the duplication exists before consolidating (AC: #2)
  - [ ] Read each route file's own comment on this (`routes/candles.py`: "cannot `from data_api.app import CATALOG_PATH`"; `routes/indicators.py`/`routes/snapshots.py`/`routes/indicator_series.py`: same pattern) to confirm the exact import-cycle shape before choosing a fix — don't guess.
  - [ ] Likely fix: extract `CATALOG_PATH` (and any other similarly-duplicated env-derived constant) into a small new module with no dependency on `app.py` or any `routes/*.py` (e.g. `troll/data_api/settings.py`), which both `app.py` and every route module import from — breaks the cycle by introducing a new leaf dependency rather than making the routes depend on `app.py`.

- [ ] Task 2 — Apply the consolidation (AC: #1, #4)
  - [ ] Replace each of the five (or more, if others exist) duplicated declarations with an import from the new settings module. Confirm the app still starts and every existing route still resolves the same default path.

- [ ] Task 3 — Tests
  - [ ] A test (or a startup smoke check) confirming no circular import was reintroduced — the whole `data_api` package must still import cleanly.

## Dev Notes

- **This is a small, mechanical refactor with a real historical reason for its current (duplicated) shape** — don't "fix" it by making every route import from `app.py`, which is exactly the cycle the original authors avoided. Read the actual import graph before changing it.
- **Sequencing:** this story should land before or alongside Stories 19.3/19.4 (new collectors) — those stories' catalog output only becomes queryable through the existing routes once this consolidation (or at least a correct multi-collector-capable `CATALOG_PATH`) is in place.

### Project Structure Notes

- New: a small settings module (e.g. `troll/data_api/settings.py`) if that's the chosen fix.
- Modified: `troll/data_api/app.py`, `routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py` (and any other route module with the same duplicated constant).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 19, Story 19.2] — this story's origin (FR62).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#Part D, D3] — the consolidation rationale ("simpler than inventing a venue→path registry, since the catalog already partitions correctly by instrument_id").
- [Source: troll/data_api/app.py:53, routes/candles.py:25-26,39, routes/snapshots.py:27-29,52, routes/indicators.py:36,58, routes/indicator_series.py:39,41,56] — grepped this session; the exact duplicated declarations and each file's own circular-import comment.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
