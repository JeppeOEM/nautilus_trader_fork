# Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 25). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 25.4".

## Story

As the collector operator,
I want a venue's collected instruments to be one plan aggregate with a cap and USD-classified pins, applied by capture with an explicit report of what actually subscribed,
So that the TUI never shows an instrument as collected that the feed never applied, and control can never reach the gate or delete catalog files.

## Acceptance Criteria

1. **Given** `DydxCollector`'s control plane (`dydx_collector/collector.py:369-625`: `_subscribe`/`_unsubscribe`, `_apply_config`, `_status_loop`, `_publish_status`, `_reload_config_loop`, `_apply_and_persist`, `_handle_control_message`, `_publish_removed`, `_pin_top_liquid`, `_control_loop`), `dydx_collector/config.py` and `classify_liquidity`
**When** the story ships
**Then** `platform/collection_control/` holds `domain/` (`CollectionPlan(venue)` with `instruments`, `exclude`, pins and `cap` = 30 for dYdX; invariants `exclude ∩ collected = ∅`, `|collected| ≤ cap`, pins admitted only by `classify_liquidity` on USD volume; `LiquidityTier`; commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` returning a plan diff), `application/` (`ControlService` for `collector:control`, `StatusPublisher` for `collector:status`, the reload loop) and `infrastructure/` (`CollectionPlanStore` over `platform/data/dydx_config.toml`, full rewrite, comment loss as a `Known limit:`; redis); the dYdX entrypoint starts these as `extra_loops` and `DydxCollector` keeps only its book hooks

2. **Given** AD-D17's applied-set rule
**When** a plan diff is applied
**Then** `Collector.apply(plan_diff)` (the legacy capture class, one new method) returns `Applied(subscribed, unsubscribed, failed)`; the sampler iterates `applied ∩ plan`; a `failed` instrument is `pending` on `collector:status`, ledgered `collector.subscribe_failed` once per attempt and retried by capture; a `LiveBook`/book state exists only for a subscribed instrument and is cleared on `unsubscribed`; an unsolicited message for a non-applied instrument is counted at `collector.unplanned_message`, not booked; tests cover a subscribe that fails on the wire and an unsubscribe that fails

3. **Given** one `config.toml` with two schema owners today
**When** the story ships
**Then** `collector_core/config.py` is the one loader, returning `(CoreConfig, CollectionPlan)` for every venue (Bybit/Hyperliquid plans are static tuples applied once through the same `apply`), control validates through it before `CollectionPlanStore.save`, the key set is frozen, and `bot_tui`'s collector pane reads an unchanged `collector:status` payload (replay test)

4. **Given** MR2 and MR4
**When** the story is merged
**Then** the moved `dydx_collector` modules are pure re-export shims with `REMOVE_AFTER = "26-2-..."`, the collector image `COPY`s `collection_control`, the Makefile test lists include `collection_control/tests`, and `platform/CLAUDE.md`'s "Adding a venue" steps 2–4 and `ARCHITECTURE.md` describe control as a separate context

## Tasks / Subtasks

- [ ] Task 1 — `platform/collection_control/` (AC: #1)
  - [ ] `domain/plan.py`: `CollectionPlan(venue, instruments: tuple[InstrumentEntry,...], exclude: frozenset, cap: int)` with `add/remove/pin/unpin/exclude/reload -> PlanDiff` and invariants asserted in `__post_init__`; `domain/liquidity.py`: `LiquidityTier`, `classify_liquidity` (from `dydx_collector/open_interest.py:40`). `application/control.py` (`ControlService.handle(action, iid)` from `_handle_control_message`, `_pin_top_liquid`), `application/status.py` (`StatusPublisher` from `_status_loop`/`_publish_status`/`_publish_removed`, reading capture's read-only counters through a small `CaptureStatus` view the collector exposes), `application/reload.py` (`_reload_config_loop`/`_apply_and_persist`). `infrastructure/plan_store.py` (`load`/`save` over `platform/data/dydx_config.toml`, from `dydx_collector/config.py`), `infrastructure/redis.py`.
  - [ ] The dYdX entrypoint passes these loops as `extra_loops`; `DydxCollector` keeps only `__init__` + book hooks (until 26.1 removes even those).
- [ ] Task 2 — applied set (AC: #2)
  - [ ] `Collector.apply(diff: PlanDiff) -> Applied(subscribed, unsubscribed, failed)` in `collector_core/collector.py` (calls the client's `subscribe`/`unsubscribe`, catches per-instrument failures, clears book state on unsubscribe); `_instrument_ids()` returns `applied ∩ plan`; `_process_data` counts `collector.unplanned_message` for non-applied ids; `StatusPublisher` marks `failed` ids `pending`; retry loop in capture (`extra_loops`). Tests: wire-failing subscribe/unsubscribe with a fake client.
- [ ] Task 3 — one loader (AC: #3)
  - [ ] `collector_core/config.py`: `load_venue_config(path, venue) -> tuple[CoreConfig, CollectionPlan]` (dYdX's `[[instruments]]` entries + `exclude` + `cap`; Bybit/HL flat tuples → static plans); `core_config_from_dict` unchanged in strictness; `plan_store.save` validates through it first. `bot_tui` `collector:status` replay test.
- [ ] Task 4 — shims, images, lists, docs (AC: #4)
  - [ ] `dydx_collector/{config,open_interest}.py` shims (`REMOVE_AFTER = "26-2-capture-package-and-venue-packages-with-entrypoints"`); `collector.dockerfile` `COPY platform/collection_control ./collection_control`; Makefile lists; `platform/CLAUDE.md` "Adding a venue" steps 2–4; `ARCHITECTURE.md`.

## Dev Notes

The plan-vs-applied split (AD-D17, adversary C3) is the substance: today `_instrument_ids()` returns the *plan* and a wire-failed unsubscribe leaves an instrument sampled and archived while `collector:status` says removed. `Collector.apply` returning `Applied(...)` and the sampler iterating `applied ∩ plan` closes it. The plan file already lives at `platform/data/dydx_config.toml` (mounted over `/app/dydx_collector/config.toml`).

### Migration rules that bind every story (spine AD-D12, MR1/MR2/MR4/MR14)

- **Deployable alone.** Frozen for the whole migration: Parquet schemas and catalog directory names, every Redis payload (`snapshots:raw`, `rankings:live`, `ranking:control`, `bots:status`, `bots:control`, `bots:history:*`, `bots:incidents:*`, `collector:status`, `collector:control`), the SQLite/TOML store schemas, compose service names, env vars, the `platform/data/` bind mounts. A replay/fixture test proving a payload or file is byte-identical before and after the move is the standard evidence.
- **Shims.** The old import path stays as a pure re-export: `from <new> import <names>` + `warnings.warn(..., DeprecationWarning)` + `REMOVE_AFTER = "<story key>"`. It defines nothing (a copied class body would register a second Arrow class and break `is` dispatch). Update every in-repo caller in the same story; a `DeprecationWarning` in the test run is a failure (TEST-04). `platform/tests/test_namespace.py` asserts `old.X is new.X`.
- **Same commit:** `platform/CLAUDE.md` citations, `platform/ARCHITECTURE.md`, `platform/docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines, both Makefile test lists (`test`, `test-live-paper`). `platform/tests/test_images.py` and `test_boundaries.py` (from 23.1) must pass.
- **Layering (AD-D2):** `domain/` imports only stdlib, `kernel/` and `nautilus_trader.model`/`core` types — no I/O, asyncio, Redis, SQLite, Parquet or the Nautilus runtime; `application/` holds `typing.Protocol` ports, services and the asyncio loops; `infrastructure/` implements ports and is imported only by the composition root. No module-level mutable runtime state (AD-D10). Every aggregate/port docstring names the invariant it protects (DESIGN-01).
- **Parent spine:** when this story resolves one of its Deferred items, strike it there with a `[amended <date>: Story <n>]` note (MR14).
- **Project rules:** `platform/CLAUDE.md` DATA-01..08, DATA-07 (no silent skips; `observability.error_ledger.record`), TEST-01..04 (real Nautilus objects, no mocks of internals, warnings are failures), READ-03 (type hints, mypy), SSOT-01..05, MEM-01..03, NAUT-01..03, FORK-01 (never touch `nautilus_trader/` or `crates/`).
- **Working directory:** `platform/` (`cd platform`); tests run as `python3 -m pytest -o addopts="" --rootdir=. <paths> -q`; `make test` runs the Makefile list inside the collector image.

### Project Structure Notes

- Target tree: spine "Structural Seed". Working dir `platform/` (renamed from `troll/` on 2026-09-21; historical docs cite `troll/`). Durable stores under `platform/data/`, never under a package.
- `platform/` is a namespace directory: never add `platform/__init__.py`; never import with a `platform.` prefix (contexts are top-level packages with `platform/` on `sys.path`).

### References

- Spine sections: AD-D17, AD-7 (inherited)
- Review findings that shaped this story: reviews/review-adversary.md C3, M9; reviews/review-rubric.md H4
- Code: `dydx_collector/collector.py:369-625`, `dydx_collector/config.py`, `dydx_collector/open_interest.py:40`, `collector_core/config.py`, `bot_tui/collector_state.py`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
