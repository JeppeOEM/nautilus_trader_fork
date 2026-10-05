---
title: 'DW-103: swap httpx for httpx2 as the TestClient transport'
type: 'chore'
created: '2026-10-05'
status: 'done'
final_revision: 'f12c9d833c'
baseline_revision: 'a3a4ab5ca335e9ae30dc87e6da285839c6e267dc'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** Every `import fastapi.testclient` / `starlette.testclient` in the platform image emits `StarletteDeprecationWarning: Using httpx with starlette.testclient is deprecated; install httpx2 instead.` (DW-103). starlette 1.6.0 (pulled in by `fastapi==0.141.1`) tries `import httpx2` first and only warns when it falls back to `httpx`; `platform/requirements.txt` pins `httpx==0.28.1`, so the fallback always fires.

**Approach:** Replace the `httpx==0.28.1` pin in `platform/requirements.txt` with an exact `httpx2==2.13.1` pin (pydantic-maintained successor, starlette's own declared test-client dep: its `full` extra requires `httpx2>=2.0.0`), update the comment, and prove the data_api tests pass with the warning gone and no new warnings.

## Boundaries & Constraints

**Always:** Exact pin (matches the file's pinning style). Keep TEST-04: the warning disappears because its cause is removed, not because it is filtered. Verify against the real starlette 1.6.0 / fastapi 0.141.1 versions.

**Block If:** httpx2 2.13.1 breaks any data_api test or `TestClient` behaviour in a way that needs test/app code changes beyond the pin swap.

**Never:** No `filterwarnings`/`-W ignore` suppression. Never touch the top-level `pyproject.toml`/`uv.lock` (`httpx` there is nautilus_trader's own dev dependency, unrelated to the platform image). No changes to `nautilus_trader/` or `crates/`. Do not edit the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| httpx2 installed | `python3 -W error::DeprecationWarning -c "import starlette.testclient"` | imports silently, `starlette.testclient.httpx.__name__ == "httpx2"` | No error expected |
| httpx also present (e.g. another dep pulls it) | both installed | starlette still picks httpx2 (tried first), no warning | No error expected |

</intent-contract>

## Code Map

- `platform/requirements.txt` -- pip requirements shared by collector/data_api/bots images; holds the `httpx==0.28.1` TestClient pin and its comment.
- `platform/data_api/tests/*.py`, `platform/verification/tests/test_candles.py`, `platform/verification/tests/test_ssot_trace.py`, `platform/scripts/bench_candles.py` -- `fastapi.testclient.TestClient` users; none import `httpx` directly, so no code change expected.
- starlette 1.6.0 `starlette/testclient.py:32-50` -- `try: import httpx2 as httpx` / fallback to `httpx` with the deprecation warning.

## Tasks & Acceptance

**Execution:**
- [x] `platform/requirements.txt` -- replace `httpx==0.28.1` with `httpx2==2.13.1`; rewrite the comment to say it is starlette.testclient's transport, that starlette 1.6.0 prefers httpx2 and warns on the httpx fallback (DW-103), and that it is distinct from the top-level pyproject's `httpx` -- removes the warning's cause.

**Acceptance Criteria:**
- Given an environment with requirements.txt's pins (httpx2 2.13.1, starlette 1.6.0), when `import starlette.testclient` runs with `-W error::DeprecationWarning`, then it succeeds and the module's `httpx` alias is `httpx2`.
- Given that environment, when `python3 -m pytest data_api/tests verification/tests -q -W error::DeprecationWarning` runs from `platform/`, then all tests pass and no `StarletteDeprecationWarning` appears.
- Given the full `make test` suite selection, when run with httpx2 installed, then the result is no worse than the httpx baseline (same pass/fail set, no new warnings).

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2 (high 0, medium 1, low 1)
- defer: 3 (high 0, medium 2, low 1)
- reject: 9
- addressed_findings:
  - `[medium]` `[patch]` starlette floated (fastapi 0.141.1 only floors `starlette>=0.46.0`; the 3-day-old data_api image had resolved 1.7.0) -- pinned `starlette==1.7.0` (the version images already ship, verified httpx2-first) with a paired-version comment beside `httpx2==2.13.1`.
  - `[low]` `[patch]` comment claimed httpx2 is starlette's declared test-client dep, but its `full` extra lists both httpx2 and httpx -- reworded to cite the testclient's httpx2-first import instead.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 1, low 2)
- defer: 1 (high 0, medium 1, low 0)
- reject: 10
- addressed_findings:
  - `[medium]` `[patch]` the bump rule said "with StarletteDeprecationWarning escalated to an error" but gave no filter; the obvious `-W error::DeprecationWarning` never matches (the class subclasses `UserWarning`) and the repo-root pytest config ignores `UserWarning` -- the comment now names `-W error::starlette.exceptions.StarletteDeprecationWarning` and says why.
  - `[low]` `[patch]` comment called 1.7.0 "the release whose testclient prefers httpx2", but 1.6.0 does too -- reworded: 1.7.0 is the release the images already resolved, verified httpx2-first.
  - `[low]` `[patch]` comment block was two run-together paragraphs (second starting lowercase) and the bump rule omitted fastapi, which is what drives starlette -- merged into one block; rule now bumps fastapi, starlette and httpx2 together.

## Verification

`StarletteDeprecationWarning` subclasses `UserWarning` (not `DeprecationWarning`), and the repo-root `pyproject.toml` pytest config ignores `UserWarning`/`DeprecationWarning`, so checks must escalate the starlette class explicitly.

**Commands:**
- `python3 -m venv --system-site-packages <scratch>/venv && <scratch>/venv/bin/pip install httpx2==2.13.1` -- expected: installs httpx2 2.13.1, httpcore2 2.13.1, truststore 0.10.4 (isolated from the user's global env).
- `<scratch>/venv/bin/python -W error -c "import starlette.testclient as t; assert t.httpx.__name__ == 'httpx2'"` -- expected: exit 0 (system `python3` raises the StarletteDeprecationWarning).
- `cd platform && <scratch>/venv/bin/python -m pytest data_api/tests verification/tests -q -W error::starlette.exceptions.StarletteDeprecationWarning` -- expected: no collection errors (system `python3` errors on 34 modules); same pass/fail set as the httpx baseline.
- `cd platform && <scratch>/venv/bin/python -m pytest <make test path list> -q -W default` vs system `python3` -- expected: identical outcome, StarletteDeprecationWarning only in the baseline.

## Auto Run Result

**Summary:** Follow-up review of the DW-103 swap (`platform/requirements.txt` pins `httpx2==2.13.1` + `starlette==1.7.0` in place of `httpx==0.28.1`). The pins stand unchanged; the pin comment was corrected so the re-verify rule actually works: it names the exact escalation filter, gives the true reason for 1.7.0, and bumps fastapi with the pair.

**Files changed (this pass):**
- `platform/requirements.txt` -- comment-only rewrite of the httpx2/starlette pin block.
- `_bmad-output/implementation-artifacts/spec-dw-103-httpx2-test-client-swap.md` -- triage log + this result.

**Review findings:** 3 patches applied (1 medium, 2 low, all in the comment); 1 deferred; 10 rejected.
- Deferred (not written to the ledger per the spec's Never boundary; for the orchestrator): `[medium]` repo-root `pyproject.toml` pytest `filterwarnings = ["ignore::UserWarning", "ignore::DeprecationWarning"]` applies to host `platform/` runs and hides StarletteDeprecationWarning plus other warnings, contrary to TEST-04's visibility intent (pre-existing, reported in the first pass too).
- Rejected: host dev env lacks httpx2 (known residual risk, below); test-only deps in prod images and httpx2's transitive deps/truststore (pre-existing pattern; in-process ASGI client, no TLS); anyio floor (`uv.lock` anyio 4.13.0 already meets httpx2's `>=4.10`); dev-group httpx coexistence (the base image's final stage installs only runtime deps, and starlette tries httpx2 first anyway); exact-pin style and starlette's `full`-extra httpx cap (no consequence); fastapi 0.141.1 + starlette 1.7.0 pairing (the first pass ran data_api's 273 tests in the real image on 1.7.0); no in-diff proof / DW-id-only traceability (covered by the spec).

**Verification:** comment-only change, so pins and resolution are unchanged from `bd55582855`'s verified state (real image: data_api 273 passed, 0 warnings). This pass: every non-comment line of `platform/requirements.txt` parses as a `packaging` Requirement; `starlette.exceptions.StarletteDeprecationWarning.__mro__` confirms the `UserWarning` base the comment now cites; the edge-case reviewer confirmed from the starlette 1.7.0 wheel that the testclient tries httpx2 first.

**Follow-up review recommended:** false -- three localized comment fixes, no behaviour change.

**Residual risk:** the host dev env (system python3: starlette 1.6.0, httpx 0.28.1, no httpx2) still falls back to httpx, silently because of the root filter, until `pip install -r platform/requirements.txt` is run there. The images are fixed on their next rebuild.
