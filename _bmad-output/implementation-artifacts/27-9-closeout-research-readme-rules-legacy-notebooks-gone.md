# Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> Research epic (Epic 27). Depends on Stories 27.1–27.8. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 27.9".

## Story

As the platform owner,
I want the research context documented as one page with a notebook index and a recipe, the rules for notebooks in `platform/CLAUDE.md`, and no legacy notebook or `pip install` cell left anywhere,
so that the next notebook is written the platform's way by default and the docs describe the code that runs.

## Acceptance Criteria

1. **Given** the six notebooks and the analysis layer from 27.1–27.8
**When** the story ships
**Then** `research/README.md` replaces `research/BACKTESTING.md` (the backtesting content moves in unchanged, with a redirect stub for one release) and holds: the notebook index (number, purpose, inputs, the domain functions it calls, run time on the fixture), the "write a notebook" recipe (pair with jupytext, parameters cell, one section per question, analysis logic goes in `research/domain`, reads go through `MarketFrames`, run `make notebooks` and `make test`), the "add a metric" recipe (add to `performance_metrics` or `research/domain`, then the notebook, never the reverse), and the local launch instructions; `platform/README.md` and the frontend docs page link to it

2. **Given** `platform/CLAUDE.md`
**When** the story ships
**Then** it gains a "Research notebooks" section with `NB-01` (a notebook holds no analysis logic: every computation is a `research/domain`, `kernel` or `performance_metrics` call; a formula in a cell is a review failure), `NB-02` (every notebook is jupytext-paired, output-stripped, parameterised by the environment variables of 27.2, and executed by `make test` against the fixture catalog), `NB-03` (no `%pip`/`!pip` cell, no dependency outside `uv.lock`; a notebook that needs a library files a dependency decision first) and `NB-04` (every catalog read in a notebook is bounded by `START`/`END`; MEM-01 restated for notebooks), and its "Development philosophy" indicator note points at `kernel/candle_patterns.py` as the second custom-`Indicator` precedent

3. **Given** the repository
**When** the story ships
**Then** `git grep -n "pandas_ta\|talib\|%pip\|custom_dydx_minute_bar" platform/` returns nothing outside `docs/` history notes and `.planning/`, no `.ipynb` exists under `platform/` outside `research/notebooks/`, `ARCHITECTURE.md`'s module map and diagram show `research/{domain,application,notebooks,strategies}` and `kernel/candle_patterns.py`, `docs/DATA_DICTIONARY.md` gains a §"Research reads" listing which stored fields each notebook reads and which derived values it computes on read (SIGNAL-01), `sprint-status.yaml` marks Epic 27 `done` once 27.8's operator action is confirmed, and this epic's `Known limit:` comments (histogram-not-marker pattern display, no `ipywidgets` interactivity, Monte Carlo on closed trades only) are listed in the DDD spine's Deferred section with their upgrade paths

## Tasks / Subtasks

- [ ] Task 1 — `research/README.md` (AC: #1)
  - [ ] Move `research/BACKTESTING.md` content in (sections: Run an existing backtest, Which backtest to copy, Build a strategy, Run from a notebook — updated for `BacktestRunner` and `backtest_candle_pattern.py`); leave `BACKTESTING.md` as a three-line redirect with `REMOVE_AFTER = "27-9-…"` semantics in prose (delete in the next epic's first story).
  - [ ] Notebook index table: `01_catalog_inspection` … `06_candlestick_scanner` with purpose, inputs (stored types read), domain/application calls, fixture run time (from the smoke test's recorded durations; add a `--durations` note).
  - [ ] Recipes: "write a notebook", "add a metric", "add a pattern" (kernel + tests + catalog entry, one paragraph), local launch (`uv run jupyter lab research/notebooks`, env vars, `make notebooks`).
  - [ ] Links from `platform/README.md` (Research section) and the frontend docs page (`frontend/src/pages/docs/data.ts` module list, the same place other modules are described).
- [ ] Task 2 — rules (AC: #2)
  - [ ] `platform/CLAUDE.md`: "Research notebooks" section, NB-01..NB-04 verbatim from AC #2; "Development philosophy" indicator note gains `kernel/candle_patterns.py` as the second custom-`Indicator` precedent; NAUT-03 already cites `research.strategies` (24.4) — verify.
  - [ ] `_bmad-output/project-context.md`: one line under Framework-Specific Rules for NB-01/NB-02 (agents read that file first).
- [ ] Task 3 — repository sweep and closeout (AC: #3)
  - [ ] `git grep` sweep per AC #3; delete any stray `.ipynb` outside `research/notebooks/` (the three legacy notebooks were deleted by 27.2/27.5/27.7 — verify, and check `.claude/worktrees/` is not in scope).
  - [ ] `ARCHITECTURE.md` module map + diagram; `docs/DATA_DICTIONARY.md` §"Research reads" (per notebook: stored fields read, derived-on-read values and the kernel function that derives them).
  - [ ] DDD spine Deferred: three entries with upgrade paths (markers on the chart; notebook interactivity without `ipywidgets`; Monte Carlo mark-to-market of open positions).
  - [ ] `sprint-status.yaml`: Epic 27 `done` only after 27.8's `awaiting-operator` is confirmed; otherwise this story records the pending operator action and leaves the epic `in-progress`.

## Dev Notes

- **Docs describe the code that runs** (the 26.3 principle applied to research): every path in the README must exist; run the README's commands once and record the output in Completion Notes.
- **`BACKTESTING.md` redirect:** a moved doc keeps a stub for one release so external links and memory notes still resolve; the stub says where the content went and when the stub goes.
- **project-context.md** is the file agents load first (bmad `persistent_facts`); a rule that only lives in `platform/CLAUDE.md` is read later. Keep the line short.
- **Project rules:** READ-02 (comments say why), MR4 (docs in the same commit), TEST-04 (the sweep is a test-like guard; if a stray `%pip` survives, the story is not done).
- **Working directory:** `platform/`.

### Project Structure Notes

- `research/README.md` (new), `research/BACKTESTING.md` (stub), `platform/README.md`, `platform/CLAUDE.md`, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, `frontend/src/pages/docs/data.ts`, `_bmad-output/project-context.md`, DDD spine Deferred, `sprint-status.yaml`.

### References

- Epic text: "Story 27.9"; FR79
- Stories 27.1–27.8 File Lists (the index is built from them)
- `platform/CLAUDE.md` "Development philosophy" (indicator note), NAUT-03; `_bmad-output/project-context.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
