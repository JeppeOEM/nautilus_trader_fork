# Story 24.3: `alerting/` context as an observer of the forming bar

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

> DDD migration story (Epic 24). Spine: `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Parent spine (inherited AD-1..AD-11, read-only): `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`. Epic text: `_bmad-output/planning-artifacts/epics.md` → "Story 24.3".

## Story

As a trader who set a price alert,
I want alerts evaluated on the same forming bar the chart shows and delivered through the one notifier,
So that an alert never fires on a bar the chart never drew, and my Telegram or webhook settings work for every kind of page.

## Acceptance Criteria

1. **Given** `data_api/alerts.py` (`Alert`, `AlertStore`, `RunState`, `evaluate`, `render`, `post_webhook`, `post_telegram`, `AlertEngine`) and `data_api/routes/alerts.py`
**When** the story ships
**Then** `platform/alerting/` holds `domain/` (`Alert`, `FiringPolicy` for `once_per_bar_close | once_per_bar | only_once`, `RunState`, `evaluate`, `render`), `application/` (`AlertEngine` implementing `views.BarObserver`, subscribe/unsubscribe of SSE queues) and `infrastructure/` (`AlertStore` over `alerts.toml`, key set frozen; `Deliverer` implemented over `observability.notify`); `data_api/app.py`'s lifespan is the only place `AlertEngine` is attached to `LiveCandleBus` (composition-root wiring, no `alerting → views` import edge beyond the `BarObserver` type), `routes/alerts.py` is a thin adapter over `alerting.application`, and the frequency semantics tests from Story 20.2 pass unchanged

2. **Given** an alert names a channel, never a transport
**When** an alert fires
**Then** `Deliverer.deliver(alert, body)` calls `observability.notify(channel, title, body)` and the transport (Telegram, webhook URL) is chosen by the notifier's env configuration; the SSE stream, the `/api/alerts` contract and the frontend dialog are byte-for-byte unchanged (existing route tests pass)

3. **Given** MR2 and MR4
**When** the story is merged
**Then** `data_api.alerts` is a pure re-export shim with `REMOVE_AFTER = "25-1-..."`, the `data_api` image `COPY`s `alerting`, the Makefile test lists include `alerting/tests`, and `ARCHITECTURE.md` and the data dictionary cite `alerting/`

## Tasks / Subtasks

- [x] Task 1 — `platform/alerting/` (AC: #1)
  - [x] `domain/alert.py` (`Alert`, `status_of`, `new_alert`), `domain/policy.py` (`FiringPolicy` over `FREQUENCIES`, `RunState`, `_crossed`, `evaluate`, `render`), `application/engine.py` (`AlertEngine(store, deliverer)` implementing `views.BarObserver`; SSE subscribe/unsubscribe/forget), `infrastructure/toml_store.py` (`AlertStore`, `ALERTS_PATH`), `infrastructure/deliverer.py` (`Deliverer.deliver(alert, body)` → `observability.notify(alert.channel, title, body)`; `telegram_configured` moves to observability).
  - [x] `data_api/app.py` lifespan: construct `AlertEngine`, attach to `views.live_candles.live_candle_bus` — the composition-root wiring; `data_api/routes/alerts.py` calls `alerting.application` only. Existing tests in `data_api/tests/test_alerts*.py` move to `alerting/tests` and pass unchanged.
- [x] Task 2 — channel, not transport (AC: #2)
  - [x] `Alert` keeps its stored fields (key set frozen) but `webhook_url`/telegram flags are read as the *channel* selection by the deliverer; the notifier picks the transport from env. Byte-identical `/api/alerts` responses and SSE events (record fixtures first).
- [x] Task 3 — shims, images, lists, docs (AC: #3)
  - [x] `data_api/alerts.py` shim (`REMOVE_AFTER = "25-1-archive-context-archiveday-one-deleter-one-rewriter"`); `data_api.dockerfile` `COPY platform/alerting ./alerting`; Makefile lists add `alerting/tests`; `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`.

## Dev Notes

Alerting depends on views only through the `BarObserver` type; the wiring is the composition root's job (`data_api/app.py`), never an `alerting → views` import (adversary review C4). Delivery goes through `observability.notify` from 23.1; an `Alert` names a channel, never a transport (AD-D16).

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

- Spine sections: AD-D2 (BarObserver), AD-D16
- Review findings that shaped this story: reviews/review-adversary.md C4, M2
- Code: `data_api/alerts.py`, `data_api/routes/alerts.py`, `data_api/app.py:74-98`
- Rules: `platform/CLAUDE.md`; data dictionary `platform/docs/DATA_DICTIONARY.md`; audit `platform/docs/DATA_INTEGRITY_AUDIT.md`

## Dev Agent Record

### Agent Model Used

Claude Opus 5.5 (claude-opus-5-5), bmad-loop dev session.

### Debug Log References

- Pinned-payload tests (`test_api_alerts_json_is_pinned`, `test_toast_payload_is_pinned`) written and green against the unmoved `data_api/alerts.py` before any move; after the move only their monkeypatch target (`alerting.domain.alert`'s `uuid`/`time`) and the `AlertStore` import changed. `alerts.toml` text additionally pinned (`alerting/tests/test_toml_store.py::test_file_text_is_frozen`) against output recorded from the pre-move `AlertStore`.
- Suite: `5 failed, 1592 passed`, no warnings summary. Failures are all in the pre-existing list: `ml_signals/tests/test_ofi_strategy.py` x3, `test_ofi_strategy_indicator_consistency.py` x1, `data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay` (no Redis). The five dYdX `trade_ohlc` failures did not reproduce in this environment.

### Completion Notes List

- `platform/alerting/` created: `domain/` (`Alert`, `status_of`, `new_alert`; `FiringPolicy(StrEnum)`, `FREQUENCIES`, `RunState`, `evaluate`, `render`), `application/` (`AlertRepository`/`Deliverer` ports, `AlertEngine` as a structural `BarObserver` with `watched_bars()`/`on_bar()`, `AlertService` + `NoDeliveryChannel`), `infrastructure/` (`AlertStore` + `ALERTS_PATH`, `NotifyDeliverer`). Imports only stdlib, `tomli_w`, `kernel`, `observability`.
- `views.live_candles`: `BarObserver` gains `watched_bars()`; `LiveCandleBus.observers` replaced by `attach`/`detach`; observed pairs refreshed per batch (a raising observer ledgered at `live_candles.observer`), buffers folded for listened ∪ observed pairs and pruned when neither holds them, kept on last unsubscribe while observed; buffers hold `SecondOHLC` rows (`_second_row`); `on_bar` gets the identical published bar from live ticks only (never seed). Two `Known limit:` notes (observer-only pairs unseeded; O(bucket) refold per tick).
- Composition: `data_api/alert_wiring.py` builds store/deliverer/engine/service; `app.py`'s lifespan is the only `attach` (before the bus task) and detaches in `finally`; routes and `ws/live.py` go through `alert_wiring`. `data_api/alerts.py` is a pure shim (`REMOVE_AFTER` 25-1) with `_REPLACED_NAMES` for `store`, `engine`, `deliver`, `channels`, `telegram_configured`.
- Deviation (DATA-07, intent contract "every continue-past-failure in the bus observer path ledgers"): `AlertStore.record_fire`'s failed persist now calls `error_ledger.record("alerting.store.persist", ...)` instead of `logger.exception`; behaviour otherwise verbatim (fire still happens, in-memory state set).
- Deviation (boundary test): the new `test_alerting_infrastructure_is_imported_only_by_its_composition_root` allows `data_api.alerts` besides `data_api.alert_wiring`, because the prescribed shim re-exports `AlertStore`/`ALERTS_PATH` from `alerting.infrastructure`; the allow-list also fails on stale entries, so it shrinks when the shim dies.
- `_crossed` now accepts `cur: float | None` (mypy flagged the verbatim `state.last_price` call); semantics unchanged.
- Expired Story 24.1 shims deleted (`ml_signals/candles.py`, `ml_signals/candle_store.py`, `collector_core/build_candles.py`); boundary map rows removed, examples repointed; `test_images` closure example repointed to the `data_api.alerts` shim. `THIS_STORY` advanced; `(ALERTING, VIEWS)` legacy edge and `data_api.routes.alerts` row removed.
- Added tests: alerting unit tests (policy, store, engine/service on hand-built bars, deliverer), views bus matrix tests (observer-only pair, identical shared bar, unwatched width, untraded bucket start, prune, detach, kept on unsubscribe, raising observer x2, seed not dispatched), data_api bus-driven engine tests (4 original scenarios + uncharted pair) and a lifespan attach/detach test.
- Docs: `ARCHITECTURE.md` (alerting row, data_api row, ml_signals row), `docs/DATA_DICTIONARY.md` §2.5 + new §2.11 (`alerts.toml` owner, key set), `platform/CLAUDE.md` context list, `useAlertToasts.ts` comment, parent spine amendment noting the deleted 24.1 shims. Compose `ALERTS_PATH`/bind mount untouched.

### File List

- platform/alerting/__init__.py (new)
- platform/alerting/domain/__init__.py, alert.py, policy.py (new)
- platform/alerting/application/__init__.py, ports.py, engine.py, service.py (new)
- platform/alerting/infrastructure/__init__.py, toml_store.py, deliverer.py (new)
- platform/alerting/tests/__init__.py, test_policy.py, test_toml_store.py, test_engine.py, test_deliverer.py (new)
- platform/data_api/alert_wiring.py (new)
- platform/data_api/alerts.py (now a shim)
- platform/data_api/app.py
- platform/data_api/routes/alerts.py
- platform/data_api/ws/live.py
- platform/data_api/tests/test_alerts.py
- platform/views/live_candles.py
- platform/views/tests/test_live_candles.py
- platform/tests/test_boundaries.py
- platform/tests/test_images.py
- platform/ml_signals/candles.py (deleted)
- platform/ml_signals/candle_store.py (deleted)
- platform/collector_core/build_candles.py (deleted)
- platform/collector.dockerfile
- platform/data_api.dockerfile
- platform/Makefile
- platform/ARCHITECTURE.md
- platform/docs/DATA_DICTIONARY.md
- platform/CLAUDE.md
- platform/frontend/src/hooks/useAlertToasts.ts
- _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
