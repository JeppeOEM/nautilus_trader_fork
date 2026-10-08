---
title: 'DW bundle: measure_lag reads each venue plan from its env var (DW-240)'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
baseline_revision: '5e0a5f76d7c00c19a76cea51a1b8cb06f81ad9b5'
final_revision: '7293a65aadb63e9796348099e0e1c4242771180b'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `platform/archive/tools/measure_lag.py`'s `_default_instruments` resolves dYdX's plan from `DYDX_PLAN_PATH`, but for Bybit and Hyperliquid it always reads the in-tree `capture/venues/<venue>/config.toml` and ignores `BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`, which compose sets (`docker-compose.yml:140,177`). Inside a container, or with a host-edited plan, bare `--venue bybit` measures a different instrument set than the collector collects. The `FileNotFoundError` hint names an env var only for dYdX.

**Approach:** One venue → env-var table (`bybit`: `BYBIT_COLLECTOR_CONFIG`, `hyperliquid`: `HYPERLIQUID_COLLECTOR_CONFIG`, `dydx`: `DYDX_PLAN_PATH`) and one plan-path resolver: the env var's value when set and non-empty, else the venue's default (in-tree `config.toml` for Bybit/Hyperliquid, the frozen `/app/dydx_collector/config.toml` for dYdX). The missing-file hint names that venue's env var for every venue.

## Boundaries & Constraints

**Always:** Keep archive's no-static-capture-import rule (resolve the path in `measure_lag.py`, do not import `capture.venues.*.config`, whose `CONFIG_PATH` is frozen at import). Real Nautilus types in tests, pytest functions `-> None`, ruff line length 100, single-line imports. Update the module/function docstrings and the `--instrument` help text so none claims the committed config is always read.

**Block If:** None expected.

**Never:** Do not edit `_bmad-output/implementation-artifacts/deferred-work.md`. Do not modify `nautilus_trader/` or `crates/`. Do not change the collectors' own `CONFIG_PATH` resolution.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bybit env set | `BYBIT_COLLECTOR_CONFIG=<tmp plan>` listing `["X-LINEAR.BYBIT"]` | `_default_instruments("bybit") == ["X-LINEAR.BYBIT"]` | none |
| Hyperliquid env set | `HYPERLIQUID_COLLECTOR_CONFIG=<tmp plan>` | that plan's ids | none |
| Env unset or empty | var absent or `""` | the committed in-tree `config.toml` is read (non-empty list) | none |
| Env names a missing file | `BYBIT_COLLECTOR_CONFIG=<absent>` | `main(["--venue","bybit",...])` exits 2, message names the path and `BYBIT_COLLECTOR_CONFIG` | `parser.error` |
| dYdX (unchanged) | `DYDX_PLAN_PATH` as before | as before; hint names `DYDX_PLAN_PATH` | `parser.error` |

</intent-contract>

## Code Map

- `platform/archive/tools/measure_lag.py` -- `_default_instruments` (path resolution), `main` (FileNotFoundError hint, `--instrument` help), module docstring.
- `platform/archive/tests/test_measure_lag.py` -- existing dYdX env tests to mirror for Bybit/Hyperliquid.
- `platform/verification/recorder.py:67-79` -- precedent `CONFIG_ENV` + `config_path` for the same two env vars.
- `platform/capture/venues/{bybit,hyperliquid,dydx}/config.py` -- the collectors' `CONFIG_PATH` the tool must match.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/tools/measure_lag.py` -- add the venue → env-var mapping and a `_plan_path(venue)` resolver; `_default_instruments` reads through it; `main`'s FileNotFoundError hint becomes `set <ENV_VAR> or pass --instrument` for all venues; refresh docstrings and the `--instrument` help -- tool reads the plan the collector reads.
- [x] `platform/archive/tests/test_measure_lag.py` -- parametrized tests over Bybit/Hyperliquid: env plan honoured; empty env falls back to the committed config; env naming a missing file exits 2 with the env var in stderr -- covers the I/O matrix.

**Acceptance Criteria:**
- Given `BYBIT_COLLECTOR_CONFIG` or `HYPERLIQUID_COLLECTOR_CONFIG` set to a plan, when `measure_lag --venue <v>` runs without `--instrument`, then it subscribes that plan's ids, not the in-tree file's.
- Given no env var, when the tool runs from any cwd, then the committed in-tree config is used as before.

## Design Notes

Empty env var is treated as unset (`os.environ.get(VAR) or default`), matching the existing dYdX line in this file; the collectors' `get(VAR, default)` would turn `""` into `Path("")`, which is no plan anyone means.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests/test_measure_lag.py -q` -- expected: all pass
- `cd platform && ruff check archive/tools/measure_lag.py archive/tests/test_measure_lag.py && ruff format --check archive/tools/measure_lag.py archive/tests/test_measure_lag.py && mypy archive/tools/measure_lag.py` -- expected: clean

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 0, medium 2, low 2)
- defer: 0
- reject: 7 (high 0, medium 0, low 7)
- addressed_findings:
  - `[medium]` `[patch]` DEPLOY_CHECKLIST §5.3 ran the 3 h lag measurement as `docker compose run <collector>`, inheriting the collector's `mem_limit` (362m/248m, sized for the collector, not a recorder holding every lag) and its capture `cpu_shares` beside the live collector; now `docker run --network host` of the collector image with the live plan mounted read-only and its env var set; module docstring matches.
  - `[medium]` `[patch]` `PLAN_ENV` and `_DYDX_DEFAULT_PLAN` had no drift guard against compose and the collectors' `CONFIG_PATH`; added `platform/tests/test_compose_plan_env.py` (collector services' env var + rw mount, each venue `config.py` reads the same var, `archive`'s `DYDX_PLAN_PATH` and dYdX's default equal `_DYDX_DEFAULT_PLAN`).
  - `[low]` `[patch]` Module docstring said all three vars are set "as compose sets them" on the venue's service; `DYDX_PLAN_PATH` is set on `archive`, not the dYdX collector; reworded.
  - `[low]` `[patch]` `--instrument` help listed the env vars without their venue; now `bybit: BYBIT_COLLECTOR_CONFIG, ...`.

## Auto Run Result

- **Summary:** follow-up review of DW-240 (`archive.tools.measure_lag` reads each venue's plan from its collector env var, `PLAN_ENV`). The change itself held; the review fixed how the operator runs it and pinned the env-var mapping against compose and the collectors.
- **Files (this pass):**
  - `platform/docs/DEPLOY_CHECKLIST.md` -- §5.3 lag run as `docker run --network host` with the live plan mounted `:ro` and its env var set, not `docker compose run` (collector `mem_limit`/`cpu_shares`).
  - `platform/archive/tools/measure_lag.py` -- docstring (run recipe, dYdX's var lives on `archive`), venue-paired `--instrument` help.
  - `platform/tests/test_compose_plan_env.py` -- new drift guard: `PLAN_ENV`/`_DYDX_DEFAULT_PLAN` vs `docker-compose.yml` and `capture/venues/*/config.py`.
- **Review:** 4 patches applied, 0 deferred, 7 rejected (error-ledger leak: false, the tool never calls `error_ledger.start`; empty-env divergence: spec Design Notes decision, documented `Known limit:`; scalar/duplicate `instruments`, mid-save TOML read: pre-existing, same file as before; FileNotFound wording; extra `main()` coverage).
- **Follow-up review recommended:** false -- doc, help text and one additive test; no behaviour change.
- **Verification:** `python3 -m pytest archive/tests/test_measure_lag.py tests/test_compose_plan_env.py tests/test_boundaries.py tests/test_legacy_names.py tests/test_notebook_rules.py -q` → 222 passed; also `tests/test_compose_verify.py tests/test_compose_cpu_budget.py` pass; `ruff check` / `ruff format --check` clean; `mypy` on `measure_lag.py` and the new test → no issues; `--help` shows the paired vars; image `platform-bybit_collector` confirmed present locally.
- **Residual risk:** the 3 h live lag run remains an owed operator step (§5.3); a relative env-var path resolves against the tool's cwd; `LagRecorder` still holds every lag in memory (unbounded over `--seconds`), now without a cgroup cap.
