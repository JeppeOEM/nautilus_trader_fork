---
title: 'Story 23.1: observability/ context, one notifier, and the migration enforcement tests'
type: 'refactor'
created: '2026-09-21'
status: 'done'
baseline_revision: '0cb42a5838'
final_revision: '901f98a3b8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/23-1-observability-context-and-migration-guardrails.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-23-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The error ledger lives in the reader package `ml_signals`, so every writer imports it. Outbound notification has two separate transports (the collector's `_notify` and `data_api/alerts.py`'s webhook/Telegram posts), and the incident handler hardcodes dYdX. Nothing mechanically enforces the DDD migration's context boundaries, the dockerfile import closures, or the collector hot-path cost.

**Approach:**
- Create a stdlib-only `platform/observability/` package (`error_ledger`, `notify`, `watchdog`, `incidents`).
- Leave `DeprecationWarning` re-export shims at the old paths and repoint every caller.
- Add `platform/tests/{test_boundaries,test_images,test_hotpath}.py`, and extend `test_namespace.py` so it asserts shim identity and shim expiry. All of them run in `make test`.
- Close the dockerfile `COPY` gaps these tests find.
- Update the docs and both Makefile test lists.

## Boundaries & Constraints

**Always:**
- **Frozen contracts (AD-D12).** Payloads, env vars, compose service names, mounts, the `alerts.toml` key set and the store schemas stay exactly as they are.
- **Payloads unchanged.** Existing notification payload shapes stay byte-compatible:
  - ntfy: POST the body, with a `Title` header.
  - Telegram: `{"chat_id","text"}` JSON.
  - webhook: the raw body, with `Content-Type` JSON when the body parses as JSON, else `text/plain`.
- **Shims are pure re-exports.** A shim is `from <new> import <names>` plus `warnings.warn(DeprecationWarning)` plus `REMOVE_AFTER = "24-1-candles-context-behind-the-secondsink-port"`. A moved name inside a module that stays is served by a module `__getattr__` that warns, plus a module constant `MOVED_NAMES_REMOVE_AFTER` with the same key.
- **No `DeprecationWarning` from our own code** in the test run (TEST-04).
- **`observability/` imports only the standard library.**
- **No venue token in `observability/`.** The incident handler's instrument pattern, report title, raw-log location and evidence needle all come from the dYdX entrypoint, and so does the `nautilus_pyo3` flush function.
- **Notification failures are ledgered,** never logged-and-forgotten (DATA-07). A Telegram failure never logs the request URL, because it contains the bot token.
- **Boundary test coverage:**
  - The map is complete: an unmapped module fails.
  - Every legacy cross-context edge and every legacy `_private` cross-context import is listed with the story key that retires it.
  - An entry expires when that story is `done` in `sprint-status.yaml`.
  - An entry that no import uses any more also fails, so the table can only shrink.
- **Stdlib tooling only:** `ast`, `tracemalloc`, `time.perf_counter_ns`.

**Block If:**
- A frozen contract (payload, env var, schema, compose service name) would have to change to satisfy an AC.

**Never:**
- Never modify `nautilus_trader/` or `crates/`.
- Never write `sprint-status.yaml`.
- Never add a dependency.
- Never loosen a guard to make it pass. That includes no blanket `filterwarnings` and no silent skip.
- No per-context packaging split.
- No durable or cross-process ledger (that is story 23.3).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| operator page, ntfy set | `notify("operator", name, msg)`, `WATCHDOG_NTFY_URL` set | POST `msg` with `Title: name` | failure → ledger `observability.notify.ntfy` |
| operator page, unset | no `WATCHDOG_NTFY_URL` | `logger.critical(msg)` (today's behaviour) | — |
| alert, Telegram configured | `notify("telegram", …)` with both `TELEGRAM_*` set | `sendMessage` JSON to `TELEGRAM_API_BASE` | failure → ledger, exception type only, URL never logged |
| alert webhook | `notify(webhook_channel(url), …)` | POST the body to the URL | failure → ledger `observability.notify.webhook` |
| unknown channel | `notify("nope", …)` | nothing sent | `ValueError` (programming error) |
| legacy edge whose story is done | `sprint-status.yaml` row `done`, edge still imported | boundary test fails, naming the edge and the story | — |
| hot-path baseline missing | first run | baseline JSON written; test passes | — |
| baseline on another host | CPU fingerprint differs | allocation checks still assert; the wall-time test skips with the reason shown | — |

</intent-contract>

## Code Map

- `platform/ml_signals/error_ledger.py` -- moves verbatim to `observability/error_ledger.py`; becomes a shim
- `platform/collector_core/collector.py:312-404,1650-1710` -- `AlertTexts`/`_alert_transition` (generic) → `observability.watchdog`; `_notify` → `observability.notify`; `_watchdog_transition` (feed-silence text) stays
- `platform/dydx_collector/collector.py:634-843,881-930` -- `[WS_RAW]` flush loop, the incident subsystem → `observability.incidents` (parameterised); `main()` wires it
- `platform/data_api/alerts.py:199-256`, `data_api/routes/alerts.py:111` -- transports → `observability.notify`; `deliver()` maps an Alert to channels
- ~31 `from ml_signals import error_ledger` sites; tests in `collector_core/tests/test_watchdog.py`, `dydx_collector/tests/{test_collector_resilience,test_ws_raw_log_prune}.py`, `data_api/tests/test_alerts.py`, `ml_signals/tests/test_error_ledger.py`
- `platform/{collector,data_api,live_paper}.dockerfile`, `Makefile` (`test`, `test-live-paper`), `docker-compose.yml` (entrypoints)
- `platform/tests/test_namespace.py` -- existing guard; fails inside the image today (it computes `/app` as the platform dir)
- `collector_core/tests/fixtures/*trades*.json` -- recorded venue trades for the replay
- Docs: `platform/CLAUDE.md` DATA-07 and the "Adding a venue" step 4, `ARCHITECTURE.md` module map, `docs/DATA_INTEGRITY_AUDIT.md`, `docs/DATA_DICTIONARY.md`, the parent spine's Deferred list

## Tasks & Acceptance

**Execution:**
- [x] `platform/observability/{__init__,error_ledger,notify,watchdog,incidents}.py` + `observability/tests/` -- create.
  - `notify(channel, title, body)` has the channels `operator` (ntfy / CRITICAL log), `telegram`, and `webhook:<url>` (`webhook_channel(url)`).
  - `watchdog` holds `AlertTexts` plus `transition(...)`.
  - `incidents` holds `IncidentConfig`, `IncidentRule`, `IncidentHandler`, `raw_log_flush_loop(sync)` and `prune_stale_raw_logs`.
  - Port the existing tests to the new modules, and add notify adapter tests against a local HTTP server.
- [x] `ml_signals/error_ledger.py`, `collector_core/collector.py`, `dydx_collector/collector.py` -- turn the old paths into shims (whole-module shim and `__getattr__` respectively), repoint every caller, and wire dYdX's `IncidentConfig` in `main()`/`extra_loops`.
- [x] `data_api/alerts.py` -- `deliver()` becomes `notify(webhook_channel(url), …)` + `notify("telegram", …)` when configured; `telegram_configured` delegates to `observability.notify`.
- [x] `platform/tests/test_boundaries.py` -- build the checker.
  - `LEGACY_MODULE_TO_CONTEXT` (longest-prefix match) plus `LEGACY_SYMBOL_TO_CONTEXT` (the `catalog_stats` and `dydx_collector.open_interest` splits).
  - `GRAPH` holds the AD-D2 edges. `LEGACY_EDGES_UNTIL` and `LEGACY_PRIVATE_IMPORTS_UNTIL` hold story keys and are expired from `sprint-status.yaml`.
  - The rules: kernel/observability import no context, research imports no `data_api`, policies files count as domain, the exemption applies only within one unmoved package, and cross-cutting `platform/tests` may import anything.
- [x] `platform/tests/test_images.py` -- build the check.
  - Entrypoints: compose `command:` lines or the dockerfile `CMD` per service, plus the Makefile `-m` modules run in the collector service. The cron line runs only `make` targets.
  - Compute the `ast` closure and assert it is a subset of that dockerfile's `COPY` set.
  - Fix the gaps it finds.
- [x] `platform/tests/test_hotpath.py`, `tests/fixtures/hotpath_baseline.json` -- the replay:
  - Three `Collector`s: dYdX arrival-timed, Bybit and Hyperliquid venue-timed.
  - 30 instruments in total: a synthetic top-20 snapshot plus incremental deltas each, plus the fixture trades re-stamped to now.
  - Measure `tracemalloc` retained blocks, retained bytes and peak bytes per message, with gc off and a warm-up pass. Measure `perf_counter_ns` per message in a separate untraced pass. Take the median of 3.
  - The first run writes the baseline; later runs assert allocations ≤ baseline and wall time ≤ 2× baseline (on the same CPU fingerprint only).
- [x] `platform/tests/_source_tree.py` + `test_namespace.py` -- resolve the checkout from `PLATFORM_SOURCE_DIR` (it defaults to the tests' parent). Add shim identity/expiry assertions.
- [x] `Makefile` -- both test targets mount the repo read-only at `/src`, set `PLATFORM_SOURCE_DIR=/src/platform`, and include `tests` and `observability/tests`. `test-live-paper` ignores `test_hotpath.py`, because that image deliberately has no collector.
- [x] Dockerfiles -- `COPY platform/observability` in all three; `COPY platform/tests` in `live_paper`; plus whatever `test_images` demands.
- [x] Docs:
  - `CLAUDE.md` DATA-07 and the "Adding a venue" step-4 cite `observability.error_ledger`, plus the per-process Known limit.
  - `ARCHITECTURE.md` gains the `observability/` row.
  - `DATA_INTEGRITY_AUDIT.md` gains the hot-path baseline row.
  - `DATA_DICTIONARY.md` is re-cited.
  - Strike the parent spine's Deferred `data_api` image entry `[amended 2026-09-21: Story 23.1]`.

**Acceptance Criteria:**
- Given the tree after this story, when `make test` runs (collector image plus the read-only source mount), then `test_boundaries`, `test_images`, `test_namespace`, `test_hotpath` and `observability/tests` pass, and no `DeprecationWarning` is attributed to `platform/` code.
- Given `grep -rn "ml_signals import error_ledger\|collector_core.collector import _notify" platform --include=*.py`, when it runs, then the only hits are the shims.
- Given `make build` of the collector, data_api and live_paper images, when it runs, then all three build.
- Given three consecutive `test_hotpath` runs on the same host, when their measurements are compared, then all three pass against the committed baseline.

## Spec Change Log

## Review Triage Log

### 2026-09-21 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14 (high 0, medium 7, low 7)
- defer: 1 (high 0, medium 0, low 1)
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` The `_notify` shim served a function with a different signature. Moved to a raising replaced-names table in `collector_core/collector.py`.
  - `[medium]` `[patch]` The `_IncidentHandler` shim served a class with a different signature. Moved to dYdX `_REPLACED_NAMES`.
  - `[low]` `[patch]` The moved INCIDENTS constants were served as copies, so a monkeypatch of one was a silent no-op. Moved to `_REPLACED_NAMES`. `test_namespace` now asserts that replaced names raise.
  - `[medium]` `[patch]` ntfy/Telegram `Request()` was built outside the try, so a malformed URL killed the watchdog task or the alert thread. Moved inside the try, with tests.
  - `[low]` `[patch]` ntfy failures were ledgered with the raw exception, which can leak the bearer topic URL. Now sanitized like the other transports.
  - `[low]` `[patch]` A webhook body that raises `RecursionError` in `json.loads` escaped unledgered. Now caught.
  - `[medium]` `[patch]` The incident-report future was discarded, so write failures were lost silently (DATA-07). A done-callback now ledgers them under `observability.incidents.report`.
  - `[low]` `[patch]` A non-str `instrument_id` from a JSON payload could reach the debounce key or `fullmatch`. Now treated as None.
  - `[low]` `[patch]` The raw-log evidence glob and the prune glob disagreed. Unified on `*.log`, which matches the Rust writer's rotation naming.
  - `[medium]` `[patch]` `_source_tree` rglob walked `platform/data` and `node_modules`, and would pick up a `.venv`. Now `os.walk` prunes excluded dirs during the walk.
  - `[medium]` `[patch]` The hot-path replay depended on wall-clock drift (stale trades, second-boundary crossings). The clock is now pinned, the test asserts all-live-path counters, and the baseline was re-recorded once.
  - `[low]` `[patch]` `test_images` mis-parsed unsupported compose `command:` forms and uvicorn option order. It now fails loudly and picks the first non-option token.
  - `[low]` `[patch]` `test_namespace` `_constant` crashed collection on a non-literal. It now fails naming the module.
  - `[low]` `[patch]` ARCHITECTURE.md, the `observability/__init__` docstring and CLAUDE.md said every module may import observability. Now "every context except `kernel`", per AD-D2.

### 2026-09-21 — Review pass (follow-up, second)
- intent_gap: 0
- bad_spec: 0
- patch: 14 (high 0, medium 5, low 9)
- defer: 1 (high 0, medium 0, low 1)
- reject: 5
- addressed_findings:
  - `[medium]` `[patch]` The hot-path replay never applied a delta for the two venue-timed collectors: `_process_data` only holds them in venue mode, and nothing drained. Two thirds of the "book" messages measured a list insertion, and the held deltas were what "retained" counted. Every burst now ends with the second's close (`_drain_pending_deltas`, as `_sample_tick` runs it), the live-path assertion also checks no delta stays held or late and every instrument has a live book, and the baseline was re-recorded once (1.7406 blocks / 122.27 B / 143.49 B peak, 3,024 ns).
  - `[medium]` `[patch]` A missing baseline was written next to the image's copy of the test (`/app/tests`) and every check then passed against itself; a re-record through `make test` was impossible. The baseline now lives in the checkout (`PLATFORM_SOURCE_DIR/tests/fixtures`), a first run that cannot write there fails naming `make hotpath-baseline`, and that Makefile target records it with the checkout mounted writable.
  - `[medium]` `[patch]` Nothing failed on a stale caller of an old path: the shim's warning is attributed to the caller, pytest only lists it, and `-W error::DeprecationWarning:observability` could never match. `test_namespace` now scans every module's imports for a whole-module shim, a moved name or a replaced name and fails on any. It found one: `dydx_collector/tests/test_incident_config.py` took `_ns_to_iso` through the shim in a test that duplicated the generic shim tests; deleted.
  - `[medium]` `[patch]` Ledgered notification failures carried only the exception's type name, so a 401 read like a 404 or a refused connection. `_failure_detail` keeps `HTTPError.code` and an `OSError` `URLError.reason` (errno text), neither of which can carry the URL; tests for both.
  - `[medium]` `[patch]` A report cancelled by `asyncio.run`'s shutdown was ledgered at ERROR, which re-entered the handler on the closing loop (reproduced: `RuntimeError: Event loop is closed` in `emit`, a destroyed pending task, a never-awaited coroutine). A cancelled report is logged at INFO: nothing cancels these tasks but shutdown.
  - `[low]` `[patch]` The parent spine's Deferred `data_api` image entry was ticked as struck but untouched. Struck with the amendment, stating that the image already copied both packages and that `test_images.py` keeps it so.
  - `[low]` `[patch]` `allocations_per_message` duplicated `retained_blocks_per_message` under a name that means something else. Removed from the measurement and the baseline.
  - `[low]` `[patch]` `test_images` knew four value-taking `docker compose run` options and dropped a recipe silently when the mis-parsed "service" ran no in-repo module. The option set covers the documented ones and the service is asserted before the module.
  - `[low]` `[patch]` `unknown_or_done` honoured a legacy entry keyed on a `superseded` story forever. Superseded now expires it, telling the reader to re-key.
  - `[low]` `[patch]` The raw-log glob `<name>_*.log` missed the writer's unrotated `<name>.log`, so a config without `file_rotate` would scan no evidence and prune no orphan. Glob widened to `<name>*.log`; docstrings and the prune test cover both shapes.
  - `[low]` `[patch]` `classify_incident` let a `RecursionError` from pathologically nested JSON escape to `handleError` (no report, no ledger). Caught, with a test.
  - `[low]` `[patch]` The shim scan matched the text `DeprecationWarning` anywhere in a module, so a `filterwarnings` call would be judged a shim. It now finds a `warnings.warn(..., DeprecationWarning)` call in the AST; a scanner test covers the three shapes.
  - `[low]` `[patch]` `_EXCLUDED_DIRS` pruned `data`, `build`, `frontend` and `.planning` at any depth, hiding a package subdirectory of that name from every guard. Those four are pruned only directly under `platform/`.
  - `[low]` `[patch]` `bot_status._incident_transition`'s docstring argued against reusing `observability.watchdog.transition` with a rule (AD-4) that now argues for it. Reworded to the reason that still holds: a different persisted representation.

### 2026-09-22 — Review pass (follow-up, third)
- intent_gap: 0
- bad_spec: 0
- patch: 16 (high 0, medium 1, low 15)
- defer: 2 (high 0, medium 0, low 2)
- reject: 8
- addressed_findings:
  - `[medium]` `[patch]` The report file name was built from a structured message's `reason` and `instrument_id` unsanitised (the handler sits on the root logger, so any WARNING+ that parses as JSON chooses it): a path separator or NUL would write outside `report_dir` or fail every debounce window. `incidents.report_name` maps anything outside `[A-Za-z0-9._-]` to `_` and caps each part; test with `../../etc` and a NUL.
  - `[low]` `[patch]` `IncidentConfig` never checked its own contract (`iid`/`ticker` groups, a `{ticker}` needle), so a wrong config failed only inside `emit()`, where `handleError` swallows it. `__post_init__` raises `ValueError` at construction; two tests.
  - `[low]` `[patch]` `prune()` raised `FileNotFoundError` when an operator deleted a report between the glob and its stat, after the report was already written, so `_ledger_failed_report` ledgered a false "not written". Missing files are skipped; test simulates the deletion.
  - `[low]` `[patch]` The ledger re-entrancy path (`_ledger_failed_report`'s ERROR line re-entering the root-attached handler, bounded by the debounce) was asserted only in prose. A test attaches the handler to root, fails every report and asserts exactly one retry and two ledger entries.
  - `[low]` `[patch]` `hotpath_baseline.json` recorded `host.python` but nothing compared it; allocation counts are exact per interpreter. `_baseline()` now fails, naming `make test`/`make hotpath-baseline`, when the running Python differs.
  - `[low]` `[patch]` The burst-shape check compared only the message count; the recorded `burst` dict is now asserted equal to the current constants as well.
  - `[low]` `[patch]` Audit row D-65 said the guard "never self-baselines", true only under `make test`'s read-only mount (the spec's matrix has the first writable run record it). Row reworded to say both, plus the Python check.
  - `[low]` `[patch]` AD-D5 as worded forbade every deliberate per-message cost increase. The Makefile comment, the allocation failure text and D-65 now name the sanctioned path: re-baseline in its own reviewed change with the reason on D-65, never to make a run pass.
  - `[low]` `[patch]` `_WATCHDOG_REMINDER_NS` in `collector_core/collector.py` duplicated `watchdog.DEFAULT_REMINDER_NS`; it now aliases it.
  - `[low]` `[patch]` `error_ledger`'s Known limit named a redis adapter inside `observability/` that `test_observability_imports_only_the_standard_library` forbids; the note now says that story widens the stdlib rule to that one subpackage.
  - `[low]` `[patch]` `notify`'s docstrings claimed the kept failure detail "cannot carry the URL"; an SSL/DNS reason can name the host. Reworded to "the URL's path" (topic, token, hook secret).
  - `[low]` `[patch]` `test_notify`'s servers called `shutdown()` but never `server_close()`, leaking the listening socket until GC. A `_stop` helper closes it.
  - `[low]` `[patch]` `python_modules()` silently let `a/b.py` and `a/b/__init__.py` overwrite each other, hiding one from every guard. It now raises; self-test in `test_boundaries`.
  - `[low]` `[patch]` `test_images` dropped a compose service with `build:` but no `dockerfile:` from the check; it now fails loudly.
  - `[low]` `[patch]` `test_images`' closure missed `importlib.import_module("<literal>")` (`collector_core.measure_lag` loads the venue clients that way) and could not see a non-literal at all. Literal names join the closure, a non-literal fails; test on `measure_lag`.
  - `[low]` `[patch]` `test_namespace`'s shim scanner only read `from` imports, so a shim binding a name with `import a.b as c` went unchecked. Such a shim now fails collection; scanner test.
  - `[low]` `[patch]` `test_paths_are_the_compose_contract` asserted `INCIDENTS.report_dir` against a literal; it now reads the collector service's bind mount from `docker-compose.yml` (via `PLATFORM_SOURCE_DIR`, like the other static guards).

## Design Notes

- **Why the static tests read the source mount and not `/app`.** No single image holds the whole tree (`live_paper` is absent from the collector image), and `/app` is not named `platform`. Reading `/app` would silently check a subset, so `make test` mounts the checkout read-only instead.
- **Why expiry is keyed on `sprint-status.yaml`.** Expiring legacy entries this way lets the graph tighten one story at a time without editing the test's rules.
- **Starting state (run 125a).** The unreviewed dev-1 WIP of run 694d (`420bfe8dfa`) was applied uncommitted onto `32efbc8e6b`. It covers the `observability/` package, its tests, the shims, the caller repointing, and `tests/_source_tree.py`. Nothing of it is trusted: verify it against this spec. Still missing: `test_boundaries`, `test_images`, `test_hotpath`, the baseline, the `test_namespace` shim assertions (check them), the Makefile, the dockerfiles and the docs.
- **Image builds must not clobber the operator's local stack.** Build with `docker build -f platform/<x>.dockerfile --network host -t story-23-1/<x> .`. Never run `make up` or `docker compose up` from the worktree, because they share the compose project name and container names with the running collectors.
- **Replay Known limit.** The replay uses the base `Collector`, so venue `_apply_deltas` overrides are not on the measured path until the capture move. `tracemalloc` does not see Rust-heap allocations. Both limits are named in the test's docstring.

## Verification

**Commands:**
- `cd platform && docker run --rm --network host -v "$REPO":/src:ro -v "$PWD":/work/platform -w /work/platform -e PLATFORM_SOURCE_DIR=/src/platform -e HOME=/tmp -e USER=collector -u 1000:1000 story-23-1/collector:latest python3 -m pytest -o addopts="" --rootdir=. <lists> -q` -- expected: no new failures against the 10 pre-existing ones (dydx trade_ohlc x5, ofi_strategy x4, rankings redis x1). A stale caller of an old path is caught statically by `tests/test_namespace.py`, not by a `-W` filter (the shim attributes its warning to the caller, so a module filter never matched).
- `make hotpath-baseline` (only in a change that deliberately re-baselines) -- expected: `tests/fixtures/hotpath_baseline.json` rewritten in the checkout; `make test` then passes `test_hotpath` three times in a row.
- `docker compose -f platform/docker-compose.yml build collector data_api live-paper` -- expected: success
- `ruff check platform/observability platform/tests` + `ruff format --check` -- expected: clean

## Auto Run Result

Status: done

**Summary.** Third review pass (fresh, `review_loop_iteration` 0) over the whole story diff since `0cb42a5838`. The `observability/` context, the shims, the repointed callers and the four migration guards stand as delivered; this pass hardened them with 16 patches, the largest being the incident report's file name, which was built from log-supplied text without sanitisation. No dockerfile, payload, env var, schema or compose service changed.

**Files changed (this pass):**
- `platform/observability/incidents.py`: `report_name()` sanitises and caps the name parts; `IncidentConfig.__post_init__` validates the pattern groups and the needle; `prune()` skips a file deleted between glob and stat.
- `platform/observability/tests/test_incidents.py`: five new tests (unsafe name, two config rejections, prune under external deletion, root-attached ledger re-entry bounded by the debounce).
- `platform/observability/notify.py`, `error_ledger.py`: docstring precision (host vs path; the redis adapter's exemption from the stdlib rule is that story's explicit widening).
- `platform/observability/tests/test_notify.py`: `_stop()` closes the listening socket.
- `platform/collector_core/collector.py`: the reminder cadence aliases `watchdog.DEFAULT_REMINDER_NS`.
- `platform/tests/test_hotpath.py`: a baseline from another Python fails with the reason; the burst shape is compared; the allocation failure text names the sanctioned re-baseline path.
- `platform/tests/_source_tree.py`, `test_boundaries.py`: `python_modules(root)` rejects a module/package name collision; self-test.
- `platform/tests/test_images.py`: `build:` without `dockerfile:` fails; literal `import_module` calls join the closure, non-literal ones fail; test on `collector_core.measure_lag`.
- `platform/tests/test_namespace.py`: a re-export shim binding a name with a plain `import` fails; scanner test.
- `platform/dydx_collector/tests/test_incident_config.py`: the report dir is read from the compose file's collector bind mount.
- `platform/Makefile` (comment), `docs/DATA_INTEGRITY_AUDIT.md` D-65: the deliberate-cost-increase procedure; the self-baseline and Python-mismatch behaviour stated exactly.

**Review.** One adversarial pass and one edge-case pass over the diff since `0cb42a5838`: 16 patches applied (1 medium, 15 low), 2 deferred (pre-existing ruff findings on untouched lines of edited files; unclosed sqlite connections in collector tests that `-W default` surfaces), 8 rejected (spec-mandated design: notification failures ledgered under DATA-07, expiry keyed on `sprint-status.yaml`, the `ml_signals.error_ledger` shim and its expiry key; out of scope: a second venue wiring the incident handler; not real: the debounce dict is bounded by reasons x instruments, the Telegram env cannot change between `channels()` and `notify()` in a container, the `THIS_STORY` constant is accurate, the raw-log glob was settled in the second pass).

**Verification.**
- `ruff` v0.15.16 (`uvx`, repo config): `ruff format` and `ruff check` clean on every file this pass changed; the 17 remaining findings in the story's edited files are all on lines the story never touched (22 at baseline, 17 now; deferred). `mypy` 1.20.2 (`--disallow-incomplete-defs --ignore-missing-imports --follow-imports=silent`): no error in any changed file.
- Collector image `story-23-1/collector:latest`, checkout mounted read-only at `/src` with `PLATFORM_SOURCE_DIR=/src/platform`, working tree at `/work/platform`: `observability/tests` + `tests/test_boundaries.py` + `tests/test_images.py` + `tests/test_namespace.py` + `dydx_collector/tests/test_incident_config.py`: **148 passed** (131 before this pass).
- Same image, the full `make test` list: **1253 passed, 10 failed**, the same 10 pre-existing failures as at baseline (dydx trade_ohlc x5, ofi_strategy x4, rankings redis x1). Run with `-W default`: 163 warnings, none a `DeprecationWarning` from a `platform/` shim, none from `platform/observability` or `platform/tests` (the rest: pre-existing sqlite `ResourceWarning`s and the pandas/starlette deprecations already deferred).
- `tests/test_hotpath.py` three consecutive runs against the committed baseline (image Python 3.13.13 = baseline): 5 passed each time.
- No dockerfile changed, so the three image builds of the first pass stand; `test_images` re-parsed the changed Makefile and compose file.

**Residual risks.**
- The wall-time bound remains host-sensitive (observed up to 1.34x on an idle box against a 2x bound); it skips on any other CPU and now fails on any other Python.
- `make hotpath-baseline` measures the collector image's `/app`, so it records stale code if the image was not rebuilt from the checkout first.
- `live_paper/tests/test_node.py` still hangs on the existing image (pre-existing; story 25.3 deselects it).
- The static guards read `_bmad-output/implementation-artifacts/sprint-status.yaml` by design; a checkout without it fails `make test` loudly.

Status: done
