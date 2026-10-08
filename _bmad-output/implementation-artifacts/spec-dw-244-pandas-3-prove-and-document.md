---
title: 'DW-244: prove pandas 3.x on the read/backtest path and document the override'
type: 'chore'
created: '2026-10-08'
status: 'done'
baseline_revision: '997914795d746c5fab60f5ce37dee149734776c3'
final_revision: '1587c02e1e2e85c2a8c6f5935ee5aede62848c00'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `platform/requirements.txt` pins `pandas==3.0.4` over nautilus_trader 1.229.0's `pandas>=2.3.3,<3.0.0` (`pyproject.toml:31`). As a result `pip check` fails in every image, host runs use pandas 2.3.3, and nothing proves or holds pandas 3 on the catalog read and backtest path. The operator decided on 2026-10-05 to prove 3.x and keep it (DW-244).

**Approach:** Investigation evidence from 2026-10-08:
- The full `make test` suite in the rebuilt collector image ran on pandas 3.0.4 and pyarrow 25.0.1, and so did `bots/tests` in the live-paper image beside a throwaway redis.
- Catalog reads, BacktestNode runs, the data_api/views readers and the bots paths all pass.
- The only pandas-3 signal is two upstream `Pandas4Warning`s raised inside compiled nautilus_trader:
  - `pd.Timestamp.utcnow()` at `backtest/engine.pyx:1418`, `:1601` and `backtest/node.py:347`;
  - `floor(freq="d")` at `data/aggregation.pyx:1626`, `:1634`, `:1646`, `:1786` and `data/engine.pyx:2041`.
- The notebook harness's `simplefilter("error")` turns those warnings into failures: 04, 05 and 08, the gallery tests and the dependent notebook fixtures.
- Fix: ignore exactly those two messages and hold everything else.
- Make the image test runs fail on any *new* `Pandas4Warning`.
- Guard the pin, the single sanctioned `pip check` conflict and the absence of those two calls in platform code.
- Document the override beside the pin and close the spine's Deferred entry.

## Boundaries & Constraints

**Always:**
- Suppress warnings by exact message, never by blanket category, and record each upstream site by file:line (TEST-04).
- Keep pandas at 3.0.4.
- Host runs (pandas 2.3.3) must stay green, so the harness filter uses category `DeprecationWarning`, the base class of `Pandas4Warning`, and does not import the 3.x-only name.

**Block If:** none known.

**Never:**
- Modify `nautilus_trader/`, `crates/` or `pyproject.toml`.
- Edit the deferred-work ledger.
- Pin pandas 2.3.3.
- Fix the unrelated pre-existing image-run failures:
  - frontend-mirror tests that read `/app/frontend`;
  - data_api tests that need redis at 127.0.0.1:6379;
  - `test-live-paper`'s `tests/` importing `capture`;
  - `bots/tests/test_config.py` reading the capture config.

  Report these in the result instead.
- Touch the pyarrow-drift Deferred entry beyond its cross-reference wording.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Image run, pin honoured | collector or live-paper image | the version and pip-check guard passes: installed pandas == pin, and `pip check` prints exactly the one nautilus/pandas line | none |
| Image run, extra conflict | an image whose `pip check` prints a second line | the guard fails and names the line | test failure |
| Host run | checkout interpreter, pandas 2.3.3 | runtime guard skipped with a reason naming both versions and `make test`; static guards run | skip |
| New pandas-4 deprecation in platform code | `make test` | the image run fails on `Pandas4Warning` | test failure |
| Upstream `utcnow` / `'d'` warning | backtest or notebook under pandas 3 | ignored by exact message | none |
| Platform code calls `.utcnow(` or passes a `"d"` freq | a static scan of `platform/**/*.py` | the guard fails with file:line | test failure |

</intent-contract>

## Code Map

- `platform/requirements.txt` -- the pin; the override comment goes beside it.
- `platform/Makefile` -- the `test` and `test-live-paper` targets, the image test runs.
- `platform/research/tests/test_notebooks.py:232-243` -- `_run`, the notebook harness that turns warnings into errors.
- `platform/bots/tests/conftest.py:24-36` -- the existing record of the two upstream warnings, partly stale (cites only `data/engine.pyx:2041`).
- `platform/tests/_source_tree.py` -- `PLATFORM_DIR`: the checkout, or `/src/platform` in an image.
- `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md:400,522-523` -- the pandas row, the Deferred entry and the pyarrow cross-reference.

## Tasks & Acceptance

**Execution:**
- [x] `platform/research/tests/test_notebooks.py` -- after `simplefilter("error")` in `_run`, ignore the two upstream messages through a module constant `UPSTREAM_PANDAS4_DEPRECATIONS`, category `DeprecationWarning`, and comment each site's file:line. Update the module docstring line that describes the harness. -- Unblocks notebooks 04, 05 and 08 under pandas 3 without hiding other warnings.
- [x] `platform/Makefile` -- define a `PANDAS4_WARNINGS` variable: `-W error::pandas.errors.Pandas4Warning` followed by `-W "ignore:<msg>:pandas.errors.Pandas4Warning"` for each upstream message. Comment why, and point to the harness constant. Add the variable to the `test` and `test-live-paper` pytest commands. -- The image run then proves pandas 3 on every later change.
- [x] `platform/tests/test_pandas_pin.py` (new) -- add three tests:
  - static: `requirements.txt` pins `pandas==3.x`, while `pyproject.toml` still declares `"pandas>=2.3.3,<3.0.0"`. When upstream lifts the cap, the guard fails so the override can be revisited.
  - static: no `platform/` Python source, outside `node_modules`, calls `.utcnow(` or passes the `"d"` alias to floor, ceil, round, resample, date_range, asfreq, to_offset or `freq=`. Then the message filters can only ever hide upstream calls.
  - image only, detected when `Path(__file__).resolve().parents[1] != PLATFORM_DIR` (otherwise a skip with a reason): installed `pandas.__version__` equals the pin, and the `python3 -m pip check` output equals exactly the sanctioned line.
- [x] `platform/requirements.txt` -- add the comment above the pin:
  - why: operator decision 2026-10-05, DW-244;
  - what was proven, on which images, on 2026-10-08;
  - the two upstream warnings and how they are handled;
  - the `pip check` conflict held by the guard;
  - the host runs on 2.3.3;
  - the upgrade path (drop the override note when nautilus lifts its cap);
  - the revert path (pin `2.3.3`, remove `PANDAS4_WARNINGS`);
  - `Known limit:` pandas 4 removes both upstream calls.
- [x] `platform/bots/tests/conftest.py` -- correct the upstream record: list every site, note that the warnings appear only under pandas 3, and point to the Makefile and the harness.
- [x] `ARCHITECTURE-SPINE.md` -- reword the pandas row as a deliberate, proven override. Strike the Deferred entry as `~~**pandas major-version split.**~~ Resolved 2026-10-08 (DW-244): ...`. Adjust the pyarrow entry's "together with the pandas item above".

- [x] `platform/tests/test_images.py` -- `_pytest_paths` expands whole-token `$(NAME)` Makefile variables (an undefined one fails) -- found in implementation: the Makefile parser read `$(PANDAS4_WARNINGS)` as a test path.
- [x] `platform/docker-compose.test.yml` (new) + `platform/Makefile` `test` -- the run's own measured `mem_limit` (3127m = 2084.4 MiB cgroup peak x 1.5) -- found in implementation: with the notebook backtests completing, the suite outgrew the `collector` service's 1690m runtime limit and `make test` died `Error 137` at 17%.

**Acceptance Criteria:**
- Given the rebuilt collector image, when the `make test` command runs with `PANDAS4_WARNINGS`, then no failure or error names pandas, and every remaining failure belongs to the pre-existing environmental set.
- Given the host interpreter (pandas 2.3.3), when `python3 -m pytest platform/tests/test_pandas_pin.py platform/research/tests/test_notebooks.py` runs, then it passes, with the runtime guard skipped for a stated reason.
- Given `ruff`, `ruff format --check` and `mypy` on the changed Python files, then all are clean.

## Spec Change Log

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11 (high 0, medium 3, low 8)
- defer: 0
- reject: 6 (Makefile parser `=`/`?=`/nested/embedded variables, which already fail loudly as undefined; compiled-source line numbers repeated across docs, which TEST-04 requires; module-scoped -W, impossible because pandas attributes the warning to the Python caller, now explained in the Makefile; a single-box memory measurement, already a `Known limit:`; the conftest comment carrying no filter, which is its documented record role; re-reading the Makefile per call)
- addressed_findings:
  - `[medium]` `[patch]` The Makefile and harness message lists were synced by comment only. Added `test_the_makefile_and_the_notebook_harness_ignore_the_same_messages`, which also checks the messages are colon-free.
  - `[medium]` `[patch]` Nothing held the image runs to `$(PANDAS4_WARNINGS)` or `make test` to `docker-compose.test.yml`. Added `test_both_image_test_runs_pass_the_pandas4_filters`.
  - `[medium]` `[patch]` The documented revert path would leave `test_pandas_pin.py` failing on a 2.x pin. The revert path now deletes it.
  - `[low]` `[patch]` The sanctioned `pip check` line hard-coded the nautilus version. It is now read from `importlib.metadata`, and the image test also asserts the installed wheel's own `pandas (>=2.3.3,<3.0.0)` cap.
  - `[low]` `[patch]` Host/image detection relied on `PLATFORM_DIR`, so a host run with `PLATFORM_SOURCE_DIR` pointing at another checkout was misclassified. It now checks for `docker-compose.yml` beside the running tests.
  - `[low]` `[patch]` The static scan missed `.utcnow` references and the `rolling`/`to_period`/`*_range`/`Period*` aliases, and checked only the first positional argument. All are covered now; a planted file verified it.
  - `[low]` `[patch]` The scan's docstring overclaimed. Added a `Known limit:` for variable, f-string and third-party aliases.
  - `[low]` `[patch]` The harness filtered the broader `DeprecationWarning`. It now uses `Pandas4Warning` where it exists and falls back to the base class on pandas 2.
  - `[low]` `[patch]` -W does not reach spawned interpreters. Recorded as a `Known limit:` with an upgrade path in the Makefile.
  - `[low]` `[patch]` The proof claim did not say how warnings were surfaced. The requirements comment now states the `-W always` run and that no FutureWarning or other pandas deprecation appeared.
  - `[low]` `[patch]` The spine's pyarrow entry now says the pandas override is independent of the lock fix, and that moving to pyarrow 24 re-runs `make test`.

### 2026-10-08 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 0, medium 1, low 8)
- defer: 0
- reject: 10 (message-only ignores reaching third-party calls, already a `Known limit:`; the one-off `-W always` proof left unrecorded, and FutureWarning/silent pandas-3 behaviour changes not held going forward, both beyond the decided scope; pyarrow drift, already the open spine Deferred entry; hand-typed cap spellings that would fail loudly on a pip change; Makefile parser `=`/`?=`/nested variables, rejected in the first pass; -W missing from spawned interpreters, already a `Known limit:`; `.pyx` line lists repeated, as TEST-04 requires; the host's `DeprecationWarning` fallback, speculative)
- addressed_findings:
  - `[medium]` `[patch]` Nothing guarded `-W error::pandas.errors.Pandas4Warning`. Deleting it or moving it after the ignores, or adding another ignore form such as `ignore::DeprecationWarning`, passed every test. `_pandas4_filters()` now parses `PANDAS4_WARNINGS` with `shlex`: it must be `-W` pairs only, the error must come first, every later filter must be exactly `ignore:<msg>:pandas.errors.Pandas4Warning`, and no message may repeat.
  - `[low]` `[patch]` The recipe check was a plain substring check. It now requires `python3 -m pytest $(PANDAS4_WARNINGS) `, so compose cannot read the filters as its own options.
  - `[low]` `[patch]` Only a colon was rejected in a message. `"`, `$`, backtick and `\` are now rejected too, because make or the shell rewrites them.
  - `[low]` `[patch]` The day-alias scan missed `window=` and `snap(...)`. Both are covered now; a planted file verified it.
  - `[low]` `[patch]` A second pandas requirement line, such as an extras, marker or `>=` form, passed unseen. It now fails the pin guard.
  - `[low]` `[patch]` In an image, a run of the mounted checkout counted as a host run and skipped the runtime guard. `IN_IMAGE` now also checks for `/.dockerenv`.
  - `[low]` `[patch]` The upstream-cap check was a substring match over all of `pyproject.toml`. It now reads `[project].dependencies` with `tomllib`.
  - `[low]` `[patch]` `docker-compose.test.yml` claimed to protect the live stack without qualification. It now has a `Known limit:` about the 3.7 GB VPS.
  - `[low]` `[patch]` `requirements.txt` said "every image" while the data_api image runs no test. It now names which images hold the guard, with a `Known limit:` for data_api.

## Verification

**Commands:**
- `docker compose -f platform/docker-compose.yml build collector` and then the Makefile `test` command (with a throwaway redis it is optional) -- expected: only the environmental failures listed in Never.
- `python3 -m pytest platform/tests/test_pandas_pin.py platform/research/tests/test_notebooks.py -q` on the host -- expected: pass, with one skip.
- `bots/tests` in the rebuilt live-paper image, beside a throwaway redis and with `PANDAS4_WARNINGS` -- expected: only `test_the_verify_parity_fleet_is_one_paper_dummy_bot_per_verify_instrument` fails (environmental).


## Auto Run Result

Status: done

**Summary.** This was a follow-up review of the DW-244 change, the pandas 3.0.4 override proven on the collector and live-paper images and documented. See the first pass's triage entry and commit `9f3d260c9c` for that change. This pass hardened the guards that hold it, so a future edit can no longer quietly drop the `Pandas4Warning` error filter or slip a platform call past the message-only ignores.

**Files changed (this pass)**
- `platform/tests/test_pandas_pin.py` -- now parses `PANDAS4_WARNINGS` and checks it: the error filter first, exact ignore forms, messages safe for make and the shell. It also checks that the filters come straight after `python3 -m pytest`, scans for `window=`/`snap`, rejects a second pandas requirement line, reads the cap from `[project].dependencies`, and detects the image through `/.dockerenv`.
- `platform/docker-compose.test.yml` -- `Known limit:`: the 3127m bound protects neighbours only where that much memory is free, so not on the 3.7 GB VPS.
- `platform/requirements.txt` -- the `pip check` note names the images that hold the guard, with a `Known limit:` for data_api, which runs no test.

**Review:** 9 patches applied (1 medium, 8 low), 0 deferred, 10 rejected (see the follow-up triage entry). No follow-up review is recommended: these are localized test-guard and comment fixes, with no change to runtime or data paths.

**Verification**
- Host, pandas 2.3.3: `tests/test_pandas_pin.py`, `tests/test_images.py` and `research/tests/test_notebooks.py` gave 72 passed, 3 skipped. The runtime pin guard is among the skips, with its stated reason.
- Mutation checks against temporary copies of the files:
  - removing the `error::` filter: caught;
  - adding `-W ignore::DeprecationWarning`: caught;
  - moving the filters off pytest's arguments: caught;
  - adding `pandas[pyarrow]>=2.2`: caught;
  - planting `rolling(window="7d")` and `snap("d")`: both reported.
- Inside `platform-collector:latest` and `platform-live-paper:latest`, both built today, with the edited test mounted over `/app/tests/test_pandas_pin.py` and `-W error::pandas.errors.Pandas4Warning`: 5 passed in each. The runtime guard ran: pandas 3.0.4, with the one sanctioned `pip check` line.
- `ruff check`, `ruff format --check` and `mypy` are clean on `test_pandas_pin.py`.
- The full `make test` was not re-run in this pass. The only changes are to this guard file and comments, and the guard ran in both images.

**Residual risks**
- Pandas 4 stays blocked until nautilus_trader stops calling `Timestamp.utcnow` and the `'d'` alias (a `Known limit:`).
- The proof ran on pyarrow 25.0.1, which resolves outside the lock. The open spine Deferred entry covers it.
- The data_api image is held only by installing the same requirements over the same base (a `Known limit:`).
- A run of the mounted checkout inside a container (`-w /src/platform`) does not start. It picks up the repo root's pytest config, which expects plugins the image lacks. That run shape is not used by any target.

**Out-of-scope findings carried from the first pass** (pre-existing, not recorded in the ledger):
1. 26 frontend- and docs-mirror tests fail in the collector image (`/app/frontend`, `/app/docs`).
2. `make test-live-paper` errors at collection: `tests/` imports `capture`, and `bots/tests` need a redis on 127.0.0.1:6379.
3. `bots/tests/test_config.py::test_the_verify_parity_fleet_is_one_paper_dummy_bot_per_verify_instrument` reads a capture config that is absent from the live-paper image.
4. `make test` inherits the `collector` service's read-write data volumes.
