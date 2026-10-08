---
title: 'DW-231/232/256: collection-control plan integrity (listed-id start check, plan-file concurrency)'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
baseline_revision: 'b480ab0661'
final_revision: 'da3d3b5836'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** `ControlService._command` accepts a `start` for any id carrying the plan's venue suffix, so a typo or an id the venue does not list (or cannot subscribe) is saved to the plan file, holds a slot and stays `pending` forever (DW-232, DW-256). And `TomlPlanStore.save` overwrites the plan keys without checking the file still holds the plan the service runs, so a command landing within the reload interval of a hand edit silently loses that edit (DW-231).

**Approach:** Expose capture's listed-id set through the `Capture` port (`CaptureStatus.listed`, filled from `CaptureService._listed`) and refuse a `start` for an id outside it at command time, as a `PlanRejected` next to the existing `has_venue` check. Give `PlanStore.save` an `expected` plan: the store refuses (`PlanFileChanged`) when the file's current plan is not `expected`; `ControlService` ledgers that refusal and replies through the status aggregate's `last_refusal`.

## Boundaries & Constraints

**Always:** the listed check applies to `start` only (`stop`/`unpin` of an unlisted id must still work, so a typo already in a plan can be removed); `listed is None` (capture has not fetched the venue's instruments yet) refuses a `start` with a reason saying so -- fail closed; the concurrency check reads the file once per save (no separate load then save); a refused save writes nothing and applies nothing; refusals are ledgered (`collector.control`) and recorded/published via `StatusPublisher.record_refusal` + `_publish`.

**Block If:** the fix would require changing the published `collector:control`/`collector:status` byte formats beyond the existing `last_refusal` field.

**Never:** no hand-edit validation against `listed` on `reload` (capture already ledgers an unlisted planned id; out of scope); no change to `capture/application/capture_service.py`'s `_subscribe_added` guard; no edits to the deferred-work ledger; no `nautilus_trader/`/`crates/` edits.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Listed start | `start X.DYDX`, X in `listed` | saved, applied, published (unchanged behavior) | none |
| Unlisted start | `start BTC-USD-PERP` typo w/ suffix, or `BTCUSD-INVERSE.BYBIT` not in `listed` | `PlanRejected` "not listed on the venue"; nothing saved/applied; `last_refusal` published | WARNING log (existing path) |
| Before fetch | `start` while `listed is None` | `PlanRejected` naming that the venue's markets are not known yet | as above |
| Stop unlisted | `stop`/`unpin` of an unlisted id in the plan | accepted as today | none |
| Hand edit pending | file's plan != service's plan, then a command | `PlanFileChanged`; file untouched; nothing applied; ledgered `collector.control`; `last_refusal` published with the reason | next reload tick adopts the edit; operator retries |
| Non-plan edit | file's thresholds/comments changed, plan equal | save proceeds, non-plan keys kept (comment loss stays the documented `Known limit:`) | none |
| Unparseable file | hand edit breaks the file | `PlanFileChanged` (a `ValueError`) whose message carries the parse error; file untouched | ledgered + replied as above |

</intent-contract>

## Code Map

- `platform/capture/application/ports.py` -- `CaptureStatus` dataclass (add `listed: frozenset[str] | None = None`, documented).
- `platform/capture/application/capture_service.py:2734` -- `capture_status()`; pass `listed=self._listed` (immutable frozenset, no copy).
- `platform/collection_control/application/ports.py` -- `Capture`/`PlanStore` protocols; add `PlanFileChanged(ValueError)`, change `save(plan, *, expected)`.
- `platform/collection_control/application/control.py` -- `_command` (listed check), `_commit` (pass `expected=self._plan`, handle `PlanFileChanged`), module docstring.
- `platform/collection_control/infrastructure/plan_store.py` -- `TomlPlanStore.save`: parse the read file, compare with `expected`, then merge; docstring.
- `platform/collection_control/tests/test_control.py`, `test_plan_store.py` -- fakes + new tests.
- `platform/capture/venues/{bybit,hyperliquid}/tests/test_control_wiring.py` -- set the capture's listed set before a command (they run no `run()`).

## Tasks & Acceptance

**Execution:**
- [x] `platform/capture/application/ports.py` -- add `listed` to `CaptureStatus` with docstring (None before `run()` fetched instruments) -- the port's read of the venue's listing.
- [x] `platform/capture/application/capture_service.py` -- fill `listed` in `capture_status()`.
- [x] `platform/collection_control/application/ports.py` -- `PlanFileChanged`; `PlanStore.save(plan, *, expected)` invariant text.
- [x] `platform/collection_control/infrastructure/plan_store.py` -- optimistic-concurrency check in `save`; unparseable file raises `PlanFileChanged` chaining the error.
- [x] `platform/collection_control/application/control.py` -- `start` listed check (helper keeps `_command` under complexity 10); `_commit` passes `expected`, catches `PlanFileChanged` -> ledger + `record_refusal` + `_publish`; docstrings.
- [x] tests -- fakes take `expected`/`listed`; new tests per matrix rows (control: unlisted start, before-fetch start, stop of unlisted, hand-edit refusal ledgered+published+not applied; store: changed plan refused & file untouched, non-plan edit saved, unparseable refused as `PlanFileChanged`); wiring tests set `_listed`; a capture test that `capture_status().listed` mirrors `_listed`.

**Acceptance Criteria:**
- Given a venue's capture has listed its markets, when a `start` names an id outside them, then the plan file is unchanged, capture's `apply` is not called, and the published aggregate's `last_refusal` names the id and "not listed".
- Given the plan file was hand-edited to a different plan since the service last loaded or saved it, when any plan-changing command arrives, then the file keeps the hand edit byte-for-byte, the command is not applied, `collector.control` is ledgered, and `last_refusal` carries the reason.
- Given a refused save, when the next reload tick runs, then the hand-edited plan is adopted (existing reload path) and a retried command succeeds.

## Spec Change Log

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 2, low 2)
- defer: 2: (high 0, medium 1, low 1)
- reject: 8: (high 0, medium 1, low 7)
- addressed_findings:
  - `[medium]` `[patch]` `PlanFileChanged` text promised "the next reload adopts the edit", false while a reload refuses the edit over lingering subscriptions (every command is then refused until they end); the messages now say "retry once the reload has adopted the edit" and the store docstring names the refuse-every-command state and its two ledger sites.
  - `[medium]` `[patch]` A save failing for any other reason (plain `ValueError`, `OSError`) was ledgered but never answered; `_commit` now also publishes it as `last_refusal` ("plan save failed: ..."), test updated.
  - `[low]` `[patch]` `_refuse_unlisted` gained a `Known limit:` (the listing is capture's one fetch at `run()`; newly listed refused / delisted passes until restart; upgrade path named).
  - `[low]` `[patch]` Docs/docstrings claimed "a typo never holds a slot"; narrowed to typos sent as commands (hand edits are not checked against the listing).

### 2026-10-08 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 0, low 4)
- defer: 0
- reject: 13: (high 0, medium 3, low 10)
- addressed_findings:
  - `[low]` `[patch]` A `start` of an id the plan already collects but the listing lacks was refused "not listed" instead of "already collected"; `_command` skips the listing check for a collected id (new test).
  - `[low]` `[patch]` A save failing with an exception whose text is empty published "plan save failed: " with no cause; the reason now carries the exception type.
  - `[low]` `[patch]` `DATABASE_SETUP.md` said "a threshold-only edit is kept", but dYdX's `liquidity_min_oi_usd`/`non_config_retain_hours` are plan keys and do refuse until adopted; doc and `TomlPlanStore` docstring now say "non-plan keys" and name them.
  - `[low]` `[patch]` `TomlPlanStore` docstring named only lingering subscriptions as a refused reload; it now also names a plan the domain rejects.

## Design Notes

The concurrency check compares the file's **plan** with the service's current plan rather than a content hash recorded at load/save. Same intent, tighter fit: (a) the save already re-reads and preserves every non-plan key, so only a plan-key edit can be lost -- a hash would also refuse harmless threshold-only edits; (b) when `reload` reads a hand edit but refuses to adopt it (lingering subscriptions over the cap), a load-time hash would mark the edit as "seen" and the next save would overwrite it, whereas comparing with the plan the service actually holds keeps refusing until the edit is adopted; (c) no baseline to initialize for a store built after the composition root already loaded the plan. Comment loss on save remains the existing documented `Known limit:`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest collection_control capture/venues/bybit/tests/test_control_wiring.py capture/venues/hyperliquid/tests/test_control_wiring.py capture/venues/dydx/tests/test_control_wiring.py capture/application -q` -- expected: all pass, no new warnings.
- `cd platform && python3 -m pytest tests/test_boundaries.py -q` -- expected: pass.
- `ruff check` / `ruff format --check` / `mypy` on changed files -- expected: clean.

## Auto Run Result

Status: done

**Summary:** Follow-up review of the DW-231/232/256 change (commit fe9db0bde3). A `start` for an id outside capture's fetched listing, or before the fetch, is refused at command time as a `PlanRejected` (DW-232, DW-256). `PlanStore.save(plan, *, expected)` refuses with `PlanFileChanged` when the file's plan is not the one the service runs (DW-231). This pass applied 4 low-severity patches.

**Files changed (this pass):**
- `platform/collection_control/application/control.py` -- listing check skipped for an already-collected id; save-failure reason carries the exception type.
- `platform/collection_control/infrastructure/plan_store.py` -- docstring: which keys are plan keys; refused-reload causes.
- `platform/docs/DATABASE_SETUP.md` -- non-plan-key wording.
- `platform/collection_control/tests/test_control.py` -- new already-collected test; updated reason assertion.

**Review:** 4 patches applied, 0 deferred, 13 rejected. Rejected: documented `Known limit:`s (delisted after startup, non-atomic in-place write, check-then-write window); intended ledgering per the intent contract; `PlanFileChanged` covering a parse failure (specified); the startup "markets not known yet" window, which production cannot reach because the control loops start only after `_run` sets `_listed`; test-fake style; and `pin_top_liquid` skipping the listing (dYdX-only, already handed to the orchestrator by the previous pass).

**Verification:** `python3 -m pytest -o addopts="" collection_control capture/venues/{bybit,hyperliquid,dydx}/tests/test_control_wiring.py capture/tests/test_collector.py tests/test_boundaries.py` -- 364 passed, no warnings. `uvx ruff check` and `ruff format --check` on `collection_control` are clean. `uvx mypy` on the two changed source files is clean.

**Residual risks:** while a reload refuses a hand edit, every command is refused until the edit is adopted or reverted. This is intended and ledgered at two sites.

