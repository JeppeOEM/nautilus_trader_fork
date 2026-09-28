---
title: 'Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone'
type: 'chore'
created: '2026-09-28'
status: 'done'
baseline_revision: '782930f5cb41a58dae8f40fa0480c958bbac7e38'
final_revision: 'e710d69cbd4ccb23c9041ee305ef60e03dc636fd'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/27-9-closeout-research-readme-rules-legacy-notebooks-gone.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-27-context.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Epic 27 built six notebooks and an analysis layer, but the research docs are split between a stub `research/README.md` and `research/BACKTESTING.md`. No binding notebook rules exist. The AC sweep `git grep -n "pandas_ta\|talib\|%pip\|custom_dydx_minute_bar" platform/` still hits notebook 06 and two research tests. The epic's `Known limit:`s are not in the DDD spine's Deferred list.

**Approach:**
- Make `research/README.md` the one research page: the moved backtest content, the notebook index, the recipes and the launch instructions. Turn `BACKTESTING.md` into a redirect stub.
- Add NB-01..NB-04 to `platform/CLAUDE.md`, with a one-line pointer in `project-context.md`.
- Clear the sweep's hits and turn the sweep into a permanent guard test, so it cannot regress.
- Update `ARCHITECTURE.md`, `DATA_DICTIONARY.md`, the frontend docs and the spine's Deferred list.

## Boundaries & Constraints

**Always:**
- Every path and command in the new docs exists and runs (the 26.3 principle).
- The backtesting content moves into the README with its meaning unchanged. The only edits are links that now point inside the same file.
- Guard patterns are written so that they never match the AC grep themselves: use character classes, e.g. `pandas[_]ta`.
- Docs, tests and code ship in one commit (MR4).

**Block If:** none. Everything is in the repo.

**Never:**
- Write or revert `sprint-status.yaml`. The orchestrator owns it: marking Epic 27 `done` is its bookkeeping, and 27.8's VPS step stays in `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions".
- Add a dependency, touch `nautilus_trader/`/`crates/`, or put analysis code in a notebook.
- Mention the literal AC tokens (the lowercase `pandas`-underscore-`ta`, `ta`+`lib`, percent-`pip`, and the retired minute-bar directory name) anywhere under `platform/` outside `docs/` and `.planning/`, and that includes the new rule text. NB-03 is worded as "no IPython `pip` magic and no `!pip` shell escape". AC #2's parenthetical describes the rule's content; AC #3's grep is literal, so the wording must satisfy both.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean tree | today's tree after this story | the guard passes; the AC grep prints nothing | — |
| Stray token | a `.py`/`.md` under `platform/` (not `docs/`, `.planning/`) contains a legacy TA-library name, a percent-`pip` or the retired directory name | the guard fails, naming `path:line` | fix the file |
| Stray notebook | a `.ipynb` anywhere under `platform/` except `research/notebooks/` | the guard fails, naming the path | move or delete |
| Install cell | a numbered notebook's `.ipynb` code line starting `%pip`/`!pip`/`%conda`/`!uv` (whitespace allowed) | the guard fails | — |
| Formula in a cell | a numbered notebook's code cell contains an array-math token (`np.`, `.mean(`, `sum(`, `.resample(`, `.rolling(`, `.cumsum(`, `.std(`, `.diff(`, `.pct_change(`) | the guard fails (NB-01) | move it to `research/domain`/`application` |
| Image run | no git binary (collector image) | the walk fallback in `_source_tree` scans the same files | — |

</intent-contract>

## Code Map

- `platform/research/README.md` -- the stub index (27.2), completed here.
- `platform/research/BACKTESTING.md` -- 266 lines to move. Its sections: Run from a notebook, Run an existing backtest, Which existing backtest to copy, Build a strategy, Wire it into a backtest, Test it.
- `platform/research/notebooks/06_candlestick_scanner.py:30` (+`.ipynb`) -- the markdown names a legacy TA library. The Parameters markdown (`HIT_INDEX`) gets the `ipywidgets` `Known limit:`.
- `platform/research/tests/test_notebook_candlestick_scanner.py:98-125` -- `_FORBIDDEN_TOKENS`, `_code_cells` and `test_no_code_cell_holds_array_math_or_ta_lib`. These are superseded by the general guard and deleted.
- `platform/research/tests/test_research_reads.py:75,200` -- a comment naming the retired directory, and a `%pip` fixture line.
- `platform/tests/_source_tree.py` -- shared checkout helpers. `platform/tests/test_legacy_names.py:104-180,264-285` holds the git-grep-or-walk machinery (`_GREP`, `_walked_files`, `_walk_hits`, `_grep_hits`), which moves into `_source_tree`.
- `CLAUDE.md` (repo root):109-113 -- the "Development philosophy" indicator note. It lives here, because `platform/CLAUDE.md` has no such section.
- `platform/CLAUDE.md` -- a new "Research notebooks" section after "Nautilus Usage Patterns". NAUT-03 (`:158`) names `research/BACKTESTING.md`.
- `platform/ARCHITECTURE.md:39` (module map research row), `:86-121` (the data-flow diagram, which has no research), `:288-410` (§2b).
- `platform/docs/DATA_DICTIONARY.md` -- a new §2.12 "Research reads" after §2.11.
- `platform/README.md:411`, `platform/frontend/src/pages/docs/kbData.ts:12,120-135` -- the docs module row and the backtesting KB entry.
- Spine `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md` `## Deferred`.
- Known limits:
  - `frontend/src/pages/ChartPage.tsx:280` (histogram);
  - `research/domain/monte_carlo.py:36` (closed trades);
  - `ipywidgets` has no comment yet, and one is added in 06.

## Tasks & Acceptance

**Execution:**
- [x] `platform/tests/_source_tree.py` + `platform/tests/test_legacy_names.py` -- Move the walk and grep machinery into `_source_tree` as public helpers. They take a regex and the files to skip, and return the text files walked, the `path:line:text` hits and the tracked files. `test_legacy_names` then uses these helpers, and all of its tests still pass. Reason: two guards now need the same scan.
- [x] `platform/tests/test_notebook_rules.py` (new) -- The guard, with one test per matrix row plus positive and negative matcher cases. NB-01 applies to every numbered notebook's code cells, using the percent-format split moved from the 06 test. The module docstring names the rules.
- [x] `platform/research/tests/test_notebook_candlestick_scanner.py` -- Delete the superseded token test and its helpers.
- [x] `platform/research/tests/test_research_reads.py` -- Reword the comment to "the retired minute-bar directory". Change the fixture magic to `%load_ext x`.
- [x] `platform/research/notebooks/06_candlestick_scanner.py` -- Reword line 30 to "No third-party TA library". Add a `Known limit:` for `ipywidgets` beside `HIT_INDEX`: choosing a hit or filter means editing the cell and re-running. The upgrade path is a dependency decision (NFR12 rejected `ipywidgets`) or the web chart for point-and-click browsing. Then run `make notebooks` to sync the pair.
- [x] `platform/research/README.md` -- The full page:
  - the index table: number, purpose, inputs (the stored types), the domain/application calls, and the fixture run time from `--durations`, with the command to re-measure;
  - the recipes: write a notebook, add a metric, add a pattern;
  - local launch;
  - Format and Parameters (kept);
  - the whole of `BACKTESTING.md` below, unchanged.
- [x] `platform/research/BACKTESTING.md` -- A three-line redirect stub. It says the stub is deleted in the first story of the next research epic.
- [x] `CLAUDE.md` (root), `platform/CLAUDE.md`, `_bmad-output/project-context.md` -- The root "Development philosophy" indicator note names `platform/kernel/candle_patterns.py` as the second custom-`Indicator` precedent. `platform/CLAUDE.md` gets a "Research notebooks" section with NB-01..NB-04, and NAUT-03 is re-pointed at `research/README.md` with `[amended 2026-09-28: Story 27.9]`. `project-context.md` gets one short line under Framework-Specific Rules for NB-01/NB-02.
- [x] `platform/ARCHITECTURE.md` -- Changes:
  - the module-map research row reads `research/{domain,application,notebooks,strategies}`;
  - the diagram gains a research branch reading the catalog and candle store, plus `kernel/candle_patterns.py` shared by views, research and bots;
  - §2b points at `README.md` alone.
- [x] `platform/docs/DATA_DICTIONARY.md` -- §2.12 "Research reads": for each notebook, the stored fields read, and each value derived on read with the function that derives it (SIGNAL-01).
- [x] `platform/README.md` + `platform/frontend/src/pages/docs/kbData.ts` -- Link `research/README.md`. Add it to the docs module row and to the backtesting KB entry's refs, and add a notebooks sentence.
- [x] Spine `## Deferred` -- Three entries with upgrade paths and `[amended 2026-09-28: Story 27.9]`:
  - pattern markers on the chart;
  - notebook interactivity without `ipywidgets`;
  - Monte Carlo mark-to-market of open positions.

**Acceptance Criteria:**
- Given the repo, when `git grep -n "pandas_ta\|talib\|%pip\|custom_dydx_minute_bar" platform/ | grep -v "^platform/docs/\|^platform/.planning/"` runs, then it prints nothing. Also, `git ls-files platform | grep '\.ipynb$' | grep -v research/notebooks/` prints nothing.
- Given the README, when each path it names is checked and its commands are run once, then every path exists and every command works. The outputs are recorded in the story's Completion Notes.
- Given `make test`'s suites run on the host, then there are no failures beyond the known baseline, and `tests/test_notebook_rules.py` and `tests/test_legacy_names.py` pass.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 17: (high 0, medium 8, low 9)
- defer: 1: (high 0, medium 0, low 1)
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` The NB-01 token list was too narrow for the rule's "Enforced by" claim. `FORMULA_TOKENS` is now a regex covering numpy/`math`/`statistics` and the reducers (`.max(`, `.min(`, `.corr(`, `.quantile(`, `.ewm(`, `.agg(`, ...). The test docstring carries a `Known limit:` (plain arithmetic passes, with an AST-check upgrade path). NB-01 now says review is the gate and the test is a backstop.
  - `[medium]` `[patch]` The formula test could pass on zero notebooks. It now asserts that the numbered sources exist.
  - `[medium]` `[patch]` The no-git sweep comparison compared two empty sets. It now uses a probe pattern (`NB-0[1-4]`) that has hits today and asserts it is non-empty. A matching comparison was added for the notebook listing.
  - `[medium]` `[patch]` The install check missed `!pip3`, `%mamba`/`micromamba`, `!python -m pip`, `!{sys.executable} -m pip`, and installs inside `%%bash`/`%%sh` cells. All are now caught, with cases for each.
  - `[medium]` `[patch]` A string-valued `.ipynb` cell `source` (valid nbformat) was iterated by character. It is now read by line.
  - `[medium]` `[patch]` The stray-notebook check used a substring test. It now requires the `research/notebooks/` prefix and no subdirectory.
  - `[medium]` `[patch]` The stray-notebook no-git fallback skipped `docs/`/`.planning/`. It now walks every non-ignored tree with `rglob`.
  - `[medium]` `[patch]` The README launch instructions contradicted each other (`uv run` although no Jupyter is in `uv.lock`, and one command lacked `cd platform`). They are now one consistent `cd platform && jupyter lab research/notebooks`, with the reason `uv run` adds nothing.
  - `[low]` `[patch]` `sum(` matched `checksum(`. It now has a word boundary, and `summary(`/`checksum(` are negative cases.
  - `[low]` `[patch]` The deleted 06 test's sanity anchor was restored as `test_code_cells_read_the_real_notebook`.
  - `[low]` `[patch]` `test_research_reads` again covers the pip magic, spelled `\x25pip`, instead of `%load_ext`.
  - `[low]` `[patch]` The sweep also catches the PyPI spelling `pandas-ta`.
  - `[low]` `[patch]` NB-03's "Enforced by" text claimed the token sweep catches every pip magic. It now names what each test covers.
  - `[low]` `[patch]` Removed a stray blank line before the new `platform/CLAUDE.md` section.
  - `[low]` `[patch]` The walk skips `.ipynb_checkpoints/` (git-ignored), so image and host agree.
  - `[low]` `[patch]` `_git` runs with `core.quotePath=false`, so git and the walk spell non-ASCII paths the same way.
  - `[low]` `[patch]` The install check covers every `.ipynb` in `research/notebooks/`, not only the numbered ones.
  - `[low]` `[defer]` The promised deletion of the `BACKTESTING.md` redirect stub is recorded in `deferred-work.md`.
- rejected:
  - `.py` twins are not scanned for installs: the pairing test (NB-02) holds them equal to the scanned `.ipynb`.
  - The ERE-vs-`re` contract is not checked: it is a docstring rule, and the probe comparison exercises both paths.
  - The hard-coded fixture run times will drift: the AC requires them, and the README gives the re-measure command.
  - The research facts appear in four docs: the AC requires each.
  - The module-map brace form: the AC names it literally.
  - `git grep --untracked`: the AC grep is tracked-file semantics.
  - Code before the first `# %%`: in percent format that is the jupytext header.
  - Case variants `TALib`/`TA-Lib`: legitimate history prose ("checked against TA-Lib once") must pass, and the imports are lowercase.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 1, medium 1, low 4)
- defer: 0
- reject: 22
- addressed_findings:
  - `[high]` `[patch]` `test_no_legacy_token_outside_the_history_notes` failed on its own file. A comment and a matcher case spelled the PyPI name `pandas-ta` literally, and the `[-_]` class matches it. Both are now escaped (`\x2d`) or reworded, and the guard is green.
  - `[medium]` `[patch]` `tracked_text_files` and `listed_files` returned None when git ran but failed. Callers then skipped the guard, or silently fell back to the walk. They now assert with git's stderr; None means only "no git can read this checkout".
  - `[low]` `[patch]` `test_notebook_rules` kept its own ignored-tree list, which was missing `dist` and the tool caches. There is now one shared `_source_tree.IGNORED_DIRS`; `_WALK_SKIP_DIRS` is that list plus the history dirs. The no-op `.endswith(".ipynb")` filter is gone.
  - `[low]` `[patch]` NB-01 `FORMULA_TOKENS` gains `.cummax/.cummin/.abs/.clip/.shift/.apply/.round/.nlargest/.nsmallest(`, with found-cases for them. The `Known limit:` now also names builtin `min(`/`max(`.
  - `[low]` `[patch]` DATA_DICTIONARY §2.12 claimed every read was bounded. It now says "every market-data read", since instrument definitions and ledger lines are not windowed.
  - `[low]` `[patch]` NB-03's "Enforced by" text now says "tracked text file", and names the walked text types for the no-git path.

## Design Notes

The guard's token regex is `pandas[_]ta|ta[l]ib|[%]pip|custom_dydx_minute[_]bar`. It matches exactly what the AC grep matches, and its own source text does not match it. The guard therefore needs no self-exclusion, and the literal AC grep stays empty.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests -q` -- expected: pass, apart from the known baseline.
- The AC grep, from the repo root -- expected: empty.
- A throwaway-venv `ruff check`/`ruff format --check` on the changed `.py` files -- expected: clean.
- `cd platform/frontend && npx tsc --noEmit -p tsconfig.app.json` -- expected: clean.


## Auto Run Result

Status: done

**Summary.** A follow-up review pass on the already-done Story 27.9. The pass found one real defect: the new legacy-token guard failed on its own source (the PyPI hyphen spelling), so `make test` was red. It also hardened the shared sweep helpers against a silent skip. No intent gap and no bad spec.

**Files changed in this pass**
- `platform/tests/test_notebook_rules.py`: escaped the self-matching `pandas-ta` spelling; uses the shared `IGNORED_DIRS`; wider NB-01 method tokens with cases; the `Known limit:` wording is updated.
- `platform/tests/_source_tree.py`: the public `IGNORED_DIRS`; a git failure now asserts rather than returning None.
- `platform/CLAUDE.md`: NB-03 "Enforced by" wording (tracked files; walked types without git).
- `platform/docs/DATA_DICTIONARY.md`: §2.12 "every market-data read".

**Review.** Blind Hunter and Edge Case Hunter reviewed. 6 patches applied (high 1, medium 1, low 4), 0 deferred, 22 rejected. Among the rejected:
- case-insensitive tokens: the prior pass already rejected them, because the TA-Lib history prose must pass;
- install-line false positives on `!pip list`: strict by design;
- `run_line_magic`/`subprocess` installs: review is the gate;
- README timings: required by the AC, and the re-measure command is given;
- the module-map brace form and diagram arrows: the spec's literal wording;
- nested `data/`/`.planning/` walk divergence: already caught by `test_walk_reads_every_tracked_text_file`;
- the index-ghost parity case;
- the `.py` twin not being scanned: NB-02 pairing covers it.

**Verification**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. research/tests tests -q`: 788 passed, 3 skipped (the same known skips as before). Before the patch, `test_no_legacy_token_outside_the_history_notes` failed.
- The AC `git grep` (filtered) and the stray-`.ipynb` listing both print nothing.
- `ruff check`/`ruff format --check` 0.15.16 are clean, and `mypy` 1.20.2 is clean, on the two changed test modules.

**Residual risks**
- NB-01's backstop is still a token list, with the AST upgrade path recorded in its `Known limit:`.
- The sweep stays case-sensitive by design.

`sprint-status.yaml` was not touched.
