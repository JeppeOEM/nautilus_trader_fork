---
title: 'Story 24.3 — `alerting/` context as an observer of the forming bar'
type: 'refactor'
created: '2026-09-25'
status: 'done'
baseline_revision: '18244a90ce0e336d0fa83a89fa14b81ade0fb446'
final_revision: 'e83ab01f1b9f0e60df407ff23ee6fc3e58700e8a'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-24-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/24-3-alerting-context-as-forming-bar-observer.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Alerting lives in `data_api/alerts.py` as module-level singletons (`store`, `engine`),
evaluates raw `DydxSecondSnapshot`s through an ad-hoc `LiveCandleBus.observers` callback list rather
than the forming bar the chart draws, and reaches into `views.rankings_bus` for queue helpers — the
`(ALERTING, VIEWS)` legacy edge that expires with this story.

**Approach:** Create the three-layer `platform/alerting/` context; make `AlertEngine` a structural
`views.BarObserver` that `LiveCandleBus` drives with the same `forming_bar` it publishes to the chart
(for every pair an active alert watches, chart listener or not); wire it only in `data_api`'s
composition root; deliver through `observability.notify` channels; leave a pure shim at
`data_api/alerts.py`; delete the three Story 24.1 shims that expire with this story.

## Boundaries & Constraints

**Always:**
- Frozen (AD-D12): `alerts.toml` key set and file text, `ALERTS_PATH` env var + default
  (`platform/data_api/alerts.toml`), compose bind mount, `/api/alerts` request/response JSON, the 422
  detail strings, the `/ws/live` toast `{"channel": "alerts", "alert": {"id", "message"}}` and every
  `/ws/live` candle message. Pin both payloads with literal-value tests recorded against the
  pre-move code before moving anything.
- The frequency semantics tests (`evaluate`/`render`/`status_of`/cross tests of Story 20.2) move to
  `alerting/tests` with only their import lines changed.
- `alerting` imports only stdlib, `tomli_w`, `kernel`, `observability`; never `views`, `candles`,
  `data_api`. `BarObserver` is satisfied structurally. `alerting/domain` imports stdlib only.
  No module-level mutable state in `alerting`; instances are built in `data_api/alert_wiring.py`.
- Every continue-past-failure in the bus observer path ledgers via `observability.error_ledger`.
- Shim idiom of `data_api/live_candles.py`; `REMOVE_AFTER = "25-1-archive-context-archiveday-one-deleter-one-rewriter"`;
  every in-repo caller repointed (`tests/test_namespace.py`).
- Working dir `platform/`; no new `DeprecationWarning`; `platform/CLAUDE.md` rules (DATA-07, TEST-01..04, READ-03, MEM-01..03, FORK-01).

**Block If:**
- A frozen payload/file text cannot be preserved without a contract change.
- The suite shows a regression beyond the ten pre-existing failures (dydx `trade_ohlc` ×5, `ofi_strategy` ×4, rankings redis ×1).

**Never:**
- Touch `nautilus_trader/`, `crates/`, or write `sprint-status.yaml`.
- Add a third seconds→bars fold or an incremental bar tracker in alerting: the bar comes from
  `candles.application.forming.forming_bar` via the bus only.
- Open a second `snapshots:raw` subscription; widen `_exempt`; delete or loosen a test (a test whose
  interface this story changes — `engine.on_snapshot`, `bus.observers` — is rewritten to the new one
  with the same inputs and expected outputs).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Alert pair not charted | active alert `(iid, 60)`, no `/ws/live` listener | bus folds a buffer for `(iid, 60)` and calls `on_bar` each traded tick | None |
| Charted and alerted | chart listener + alert on same pair | observer receives the identical `bar` dict published to the queue, `ts_ns` = the second's `ts_event` | None |
| No-trade second mid-bucket | forming bar republished with unchanged close | `evaluate` sees the same price: never fires | None |
| No-trade first second of a bucket | `forming_bar` is None | nothing published, nothing evaluated | None |
| Alert deleted / triggered / expired | pair leaves `watched_bars()` | its buffer is dropped on the next batch unless a chart listener holds it | None |
| Chart listener leaves an observed pair | last `unsubscribe` | buffer kept (still observed) | None |
| Observer raises | `watched_bars` or `on_bar` raises | other observers/listeners unaffected | `error_ledger.record("live_candles.observer", ..., exc)` |
| No channel | create with empty `webhook_url`, Telegram unset | 422, detail unchanged | `NoDeliveryChannel` → 422 in route |
| Failed send | webhook unreachable | fire still recorded + toasted | ledgered by `notify` at `observability.notify.webhook`, detail names `alert <id>` |

</intent-contract>

## Code Map

- `data_api/alerts.py` -- source of the move (Alert, AlertStore, RunState, evaluate, render, channels, deliver, AlertEngine, module `store`/`engine`).
- `data_api/routes/alerts.py` -- CRUD route over `alerts.store`/`alerts.engine`.
- `data_api/app.py:50-108` -- imports + `buses.live_candle_bus.observers.append(alerts.engine.on_snapshot)` (import-time wiring); lifespan at :80.
- `data_api/ws/live.py:48,190,226` -- `alerts.engine.subscribe/unsubscribe`.
- `data_api/buses.py` -- 24.2 composition-module idiom to mirror.
- `views/live_candles.py` -- `BarObserver` (on_bar only), `LiveCandleBus.observers` raw-snapshot list, `_apply_to_buffer`/`_publish`/`unsubscribe`/`_remember`.
- `views/tests/test_live_candles.py:407-435` -- structural `BarObserver` test double.
- `observability/notify.py` -- `notify(channel,title,body)`, `webhook_channel`, `TELEGRAM`, `telegram_configured`.
- `data_api/tests/test_alerts.py` -- tests to split.
- `tests/test_boundaries.py:58,127,170-171,202-208,241-244,630,650` -- `THIS_STORY`, shim map rows, `(ALERTING, VIEWS)` legacy edge, examples naming `data_api.alerts`/`ml_signals.candles`.
- `tests/test_images.py:434` -- closure example naming the expiring `ml_signals.candle_store`.
- Expiring shims: `ml_signals/candles.py`, `ml_signals/candle_store.py`, `collector_core/build_candles.py`.
- `data_api.dockerfile`, `collector.dockerfile`, `Makefile:147-149`, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, `CLAUDE.md`, `frontend/src/hooks/useAlertToasts.ts:13`.

## Tasks & Acceptance

**Execution:**

- [x] `data_api/tests/test_alerts.py` -- FIRST, against the unmoved code: add `test_api_alerts_json_is_pinned` (POST+GET with fixed id/created_ns via monkeypatched `uuid`/`time` giving the literal expected JSON) and `test_toast_payload_is_pinned` (literal toast dict). Run them green before moving.
- [x] `alerting/__init__.py` -- context docstring (charter, invariants: observes the forming bar, names channels not transports, no module state; dependency direction; enforcement note). No code.
- [x] `alerting/domain/alert.py` -- `Alert` (fields and order unchanged), `status_of`, `new_alert`, verbatim.
- [x] `alerting/domain/policy.py` -- `FiringPolicy(StrEnum)` with the three values, `FREQUENCIES = tuple(FiringPolicy)` values, `RunState`, `_crossed`, `evaluate`, `render` verbatim (comparisons against `FiringPolicy` members).
- [x] `alerting/application/ports.py` -- `AlertRepository` (`list/add/delete/record_fire`) and `Deliverer` (`channels(alert) -> tuple[str, ...]`, `deliver(alert, body)`) `Protocol`s, docstrings naming their invariants.
- [x] `alerting/application/engine.py` -- `AlertEngine(store, deliverer)`: `subscribe/unsubscribe/forget` (own `QUEUE_MAX = 1000` + drop-oldest put, equal to views' values), `watched_bars() -> frozenset[tuple[str, int]]` (active alerts' `(instrument_id, bar_seconds)`), `on_bar(instrument_id, bar_seconds, bar, ts_ns)` evaluating matching alerts with `bar["c"]`, `_fire` (record, deliver on a daemon thread, toast).
- [x] `alerting/application/service.py` -- `AlertService(store, deliverer, engine)`: `list()`, `create(**fields)` raising `NoDeliveryChannel` when `deliverer.channels(alert)` is empty, `delete(alert_id)` (store delete + `engine.forget`).
- [x] `alerting/infrastructure/toml_store.py` -- `AlertStore` verbatim + `ALERTS_PATH` (same env var/default). `alerting/infrastructure/deliverer.py` -- `NotifyDeliverer` over `observability.notify` (`channels` + `deliver` bodies verbatim).
- [x] `views/live_candles.py` -- `BarObserver` gains `watched_bars()`; replace `observers` with `attach/detach`; per batch refresh the observed pair set (ledger a raising observer), fold buffers for listened ∪ observed pairs, dispatch `on_bar` with the published bar from the live path only, keep an observed pair's buffer on last unsubscribe, prune buffers neither listened nor observed; buffers hold `SecondOHLC` rows (the `_remember` projection) instead of full snapshots. `Known limit:` notes: observer-only pairs are not seeded (o/h/l/v start at first observation; `c` exact); refold is O(bucket seconds) per tick (upgrade: incremental fold in `candles.domain`).
- [x] `views/tests/test_live_candles.py` -- test double gains `watched_bars`; new tests for the matrix's bus rows (observer-only pair, shared identical bar, prune, kept on unsubscribe, raising observer ledgered).
- [x] `data_api/alert_wiring.py` -- composition module: `store`, `deliverer`, `engine`, `service` instances (docstring in `buses.py` idiom). `data_api/app.py` lifespan: `buses.live_candle_bus.attach(alert_wiring.engine)` before the bus task, `detach` in `finally`; drop the import-time append. `data_api/ws/live.py`, `data_api/routes/alerts.py` -- use `alert_wiring.engine`/`alert_wiring.service`; route maps `NoDeliveryChannel` → 422 with the unchanged detail.
- [x] `data_api/alerts.py` -- pure re-export shim; `_REPLACED_NAMES` for `store`, `engine`, `deliver`, `channels`, `telegram_configured` naming their successors; `AlertEngine` is re-exported (its constructor now takes a `Deliverer`).
- [x] Tests split -- `alerting/tests/` (`__init__.py`, `test_policy.py` with the Story 20.2 tests, `test_toml_store.py`, `test_engine.py` with hand-built bars, `test_deliverer.py` with the webhook-ledger/telegram/channels tests); `data_api/tests/test_alerts.py` keeps route + pinned-payload tests and gains bus-driven engine tests (real `LiveCandleBus` + `DydxSecondSnapshot`: only-once single fire, failed persist still fires, toast, no-trade second).
- [x] Expired 24.1 shims -- delete `ml_signals/candles.py`, `ml_signals/candle_store.py`, `collector_core/build_candles.py`; remove their `test_boundaries.py` map rows; repoint `test_images.py:434` and the `test_boundaries.py:649-651` examples to a live shim.
- [x] `tests/test_boundaries.py` -- `THIS_STORY` = this story; drop the `(ALERTING, VIEWS)` edge and the `data_api.routes.alerts` row; keep `data_api.alerts` → `ALERTING` (shim); update the `data_api.alerts` examples; add a test that only `data_api.alert_wiring` (and alerting tests) import `alerting.infrastructure`.
- [x] `collector.dockerfile`, `data_api.dockerfile` -- `COPY platform/alerting ./alerting`; `Makefile` `test` list adds `alerting/tests`.
- [x] Docs, same commit -- `ARCHITECTURE.md` (alerting row/context), `docs/DATA_DICTIONARY.md` (`alerts.toml` owner), `CLAUDE.md` citations of `data_api/alerts.py`, `frontend/src/hooks/useAlertToasts.ts` comment; story file tasks ticked + Completion Notes.

**Acceptance Criteria:**
- Given `make test`'s list (with `alerting/tests`), when it runs, then `test_boundaries.py`, `test_images.py`, `test_namespace.py` pass, no `DeprecationWarning` appears, and only the ten pre-existing failures remain.
- Given an active alert and no browser connected, when `snapshots:raw` ticks cross its level, then it fires exactly per its frequency, evaluated on `forming_bar`'s close for `(instrument_id, bar_seconds)`.
- Given `platform/alerting`, when searched, then it imports nothing from `views`, `candles` or `data_api`, and `data_api/app.py`'s lifespan is the only `attach` call.

## Spec Change Log

## Review Triage Log

### 2026-09-25 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 0, medium 2, low 6)
- defer: 2: (high 0, medium 2, low 0)
- reject: 16
- addressed_findings:
  - `[medium]` `[patch]` `LiveCandleBus._apply_to_buffer` copied the whole bucket (`[*buffer, row]`) every tick, a second O(bucket) pass now that an always-on 1D alert keeps a buffer; appends in place.
  - `[medium]` `[patch]` An observer naming a non-positive `bar_seconds` (e.g. a hand-edited `alerts.toml` with `bar_seconds = 0`) made the bus divide by zero, raising out of `handle_batch` and stalling every chart; `_ask_watched` now ledgers and drops such pairs (`live_candles.observer`), with a test.
  - `[low]` `[patch]` Buffer memory comment said "a few MB"; corrected to ~10-15 MB per always-on 1D pair.
  - `[low]` `[patch]` `AlertEngine.watched_bars` judges expiry on the wall clock while `evaluate` uses event time; documented as a `Known limit:` with the upgrade path.
  - `[low]` `[patch]` `BarObserver` docstring overstated "same bar as the chart" across a seed republish; now states only `c` is guaranteed equal between a seed and the next tick.
  - `[low]` `[patch]` Lines over 100 chars in new docstrings (`routes/alerts.py`, `alerting/__init__.py`, `data_api/alerts.py`, `views/live_candles.py`, `tests/test_boundaries.py`) rewrapped; the formatter-mangled `_looks_like_url` early return restored to a plain `return v`.
  - `[low]` `[patch]` `QUEUE_MAX` copied by value into alerting had nothing keeping it equal to views'; `data_api/tests/test_alerts.py` pins them equal.
  - `[low]` `[patch]` Lifespan now binds the bus and engine once so shutdown detaches exactly what startup attached.

Rejected, with reasons: blocking TOML write in `record_fire` on the loop, per-fire daemon thread, one raising alert skipping the pair's rest, `Thread.start` failure after `record_fire`, DELETE/`on_bar` race, `RunState` for inactive alerts, untyped `create(**fields)` (all pre-existing, moved unchanged; `store.list()` per snapshot was already the old cost); the `data_api.alerts` shim's expiry key and `AlertEngine` signature (spec-mandated); ledger volume from a persistently broken observer (the existing per-tick ledger idiom, a real malfunction DATA-07 wants recorded); pinned-payload tests "hand-written" (they ran green against the unmoved code first); a buffer dropped on unsubscribe for a pair an alert began watching after the last batch (rebuilt next batch, `c` exact); no alert evaluation when lifespan is not run (production always runs it; a test proves attach/detach).

### 2026-09-25 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 1, low 3)
- defer: 1: (high 0, medium 1, low 0)
- reject: 16
- addressed_findings:
  - `[medium]` `[patch]` An observed width above one day (a hand-edited `alerts.toml` `bar_seconds`; only the route capped it) never rolled its bucket over, so its bus buffer grew for as long as the alert lived (MEM-02). `views.live_candles.MAX_OBSERVED_BAR_SECONDS = 86_400` now bounds observed widths; wider pairs are ledgered and skipped, with tests.
  - `[low]` `[patch]` A malformed `watched_bars()` element (not an `(str, int)` 2-tuple) raised `pair[1]` outside `_ask_watched`'s try and out of `handle_batch`; `_is_valid_pair` now checks the shape, so it is ledgered and skipped like a bad width.
  - `[low]` `[patch]` `alerting/__init__.py` and `data_api/alert_wiring.py` said `alert_wiring` is the only importer of `alerting.infrastructure`; both now name the `data_api.alerts` shim the boundary test also allows.
  - `[low]` `[patch]` A 124-character docstring line in the `data_api/alerts.py` shim was rewrapped to the 100-character limit.

Rejected, with reasons: blocking `alerts.toml` write and store lock on the loop, per-fire daemon thread, delete/`on_bar` race and its orphaned `RunState`, `once_per_bar_close` expiry between buckets (all pre-existing, moved verbatim, and rejected in the first pass); unknown `frequency` at load and non-atomic save (already deferred); O(bucket) refold and no alert-count cap (documented `Known limit:`; the route is local); `only_once` paying the width's refold (the same `Known limit:`); an unsubscribe→resubscribe re-seed of an observed buffer (seed only prepends rows older than the buffer's first, so there is no duplication; the drift is documented in `BarObserver`); wall-clock expiry drift (documented `Known limit:`); the shim's `AlertEngine` signature (spec-mandated); invalid pairs ledgered every batch (the deliberate per-tick ledger idiom); `AlertService.create` doing no validation of its own (its one caller, the route, validates); test helpers joining every daemon thread (pre-existing helper); suggested extra coverage (`once_per_bar_close` via the bus, create→watched), since the engine tests already cover the policy and the bus contract.

## Design Notes

**Why `watched_bars()` joins the port.** The bus folds only pairs someone needs; without it an alert
on an uncharted pair would never see a bar, and alerts must fire with no tab open. Asking the
observer once per batch keeps the bus free of alert knowledge and bounds buffers to watched pairs.

**Why buffers become `SecondOHLC`.** An always-on 1D alert keeps up to 86,400 seconds buffered; full
snapshots (20-level books) would cost hundreds of MB, the 7-field projection a few MB. `forming_bar`
already folds `SecondOHLC` (the seed prepends them), so publish cadence and bars are unchanged.

**Why equivalence holds.** A traded second's forming-bar close is that second's `close_price` (the old
input); an untraded second republishes the same close, which `evaluate` cannot fire on in any
frequency; an untraded bucket start publishes nothing, as before.

**Route tests stay in `data_api/tests`.** They drive `data_api.app`; `alerting` tests may not import
`data_api`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q` -- expected: only the ten pre-existing failures, no `DeprecationWarning`.
- `cd platform && uvx ruff format --check alerting views data_api tests && uvx ruff check alerting views data_api tests` -- expected: clean.
- `cd platform && grep -rnE '^(from|import) (views|candles|data_api)' alerting` -- expected: no hits.


## Auto Run Result

Status: done

### Summary of implemented change

Created the three-layer `platform/alerting/` context (`domain/alert.py`, `domain/policy.py` with
`FiringPolicy`, `application/{ports,engine,service}.py`, `infrastructure/{toml_store,deliverer}.py`).
`AlertEngine` is a structural `views.BarObserver`. `LiveCandleBus` folds a forming bar for every pair an
attached observer's `watched_bars()` names, whether or not a chart is open. It hands the observer the same bar
it publishes to `/ws/live`, so alerts evaluate on `forming_bar`'s close. Wiring lives only in
`data_api/alert_wiring.py` and `data_api/app.py`'s lifespan, and delivery goes through `observability.notify`.
`data_api/alerts.py` is a pure shim, and the three expired Story 24.1 shims were deleted. A follow-up
review pass bounded observed widths at one day, guarded against malformed observer pairs, and corrected
two docstrings.

### Files changed

- `platform/alerting/**`: the new context and its tests (policy, store incl. byte-exact TOML, engine, deliverer).
- `platform/views/live_candles.py`: `BarObserver.watched_bars`, `attach`/`detach`, observed-pair folding, pair/width guard (`MAX_OBSERVED_BAR_SECONDS`), `SecondOHLC` buffers, in-place append, `Known limit:` notes.
- `platform/views/tests/test_live_candles.py`: observer/bus tests for every bus row of the I/O matrix, plus malformed/oversized pairs.
- `platform/data_api/{alert_wiring.py,app.py,ws/live.py,routes/alerts.py,alerts.py}`: composition root, lifespan attach/detach, thin adapters, shim.
- `platform/data_api/tests/test_alerts.py`: pinned `/api/alerts` JSON and toast payload, bus-driven engine tests, route tests.
- `platform/tests/test_boundaries.py`, `platform/tests/test_images.py`: `THIS_STORY`, expired entries removed, the rule on who may import `alerting.infrastructure`.
- `platform/ml_signals/{candles,candle_store}.py`, `platform/collector_core/build_candles.py`: deleted (expired 24.1 shims).
- `platform/{collector,data_api}.dockerfile`, `platform/Makefile`: `alerting` copied and tested.
- `platform/{ARCHITECTURE.md,CLAUDE.md,docs/DATA_DICTIONARY.md}`, `platform/frontend/src/hooks/useAlertToasts.ts`: docs now cite `alerting/`.

### Review findings breakdown

- First pass: 8 patches, 2 deferred, 16 rejected.
- Follow-up pass: 4 patches (medium 1, low 3), 1 deferred (`AlertStore.add`/`delete` do not roll back the in-memory list on a failed save; pre-existing), 16 rejected.

### Verification

- Full suite (`alerting/tests views/tests candles/tests collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests`): 5 failed, 1601 passed. All five failures are on the pre-existing list (`ofi_strategy` ×4, rankings redis ×1). No `DeprecationWarning`.
- `uvx ruff format --check alerting views data_api tests`: clean. `uvx ruff check` on the files touched in the follow-up: clean.
- `grep -rnE '^(from|import) (views|candles|data_api)' platform/alerting`: no hits.

### Residual risks

- An always-on 1D alert refolds up to 86,400 rows per tick (documented `Known limit:`; the upgrade path is an incremental fold in `candles.domain`).
- Pairs that only an observer watches are not seeded: `o`/`h`/`l`/`v` start at the first observation (alerting reads only `c`).
- `make test` inside the Docker image was not run; the suite ran with the system `nautilus_trader`.
