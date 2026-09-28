---
title: 'Story 26.2: capture/ package and capture/venues/<v>/ with new entrypoints'
type: 'refactor'
created: '2026-09-28'
baseline_revision: '7fbdb4fe76bdf035d3f855fa9f10c9ead7f5df62'
final_revision: '6f0bed9892025a9ddc99ac2a483d3a0715426bc3'
status: 'done'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/26-2-capture-package-and-venue-packages-with-entrypoints.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-26-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Capture still lives in four legacy packages (`collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector`), and every venue is a `Collector` subclass. So "Adding a venue" is a recipe over a subclass, not over named files, and the DDD spine's Structural Seed (`capture/{domain,application,infrastructure,venues}`) is not the code that runs.

**Approach:** A mechanical `git mv` into `platform/capture/` shaped as the seed. `Collector` becomes the non-subclassable `CaptureService`. Each venue becomes `capture/venues/<v>/` with `client.py`, `trade_history.py`, `policies.py`, optional `open_interest.py`/`book_snapshot.py`, `config.py` and a composition-root `__main__.py`. The old packages become pure re-export shims keyed to 26.3. Behaviour, published language and the hot path stay unchanged.

## Boundaries & Constraints

**Always:**
- The published language stays frozen (AD-D12). That covers:
  - Parquet schemas and catalog directories;
  - every Redis payload;
  - ledger site strings (`collector.*`);
  - `ERROR_LEDGER_SERVICE` values, compose **service names** and container names;
  - env var names, `platform/data/` bind mounts, and the dYdX plan's container path `/app/dydx_collector/config.toml`.
- Every behaviour test keeps its scenario and assertions. Only imports, how a capture is constructed, and paths may change.
- The hot path (`tests/test_hotpath.py`) stays within the baseline: allocations ≤ `hotpath_baseline.json`, wall time ≤ 2×. The pre-story run gave 0.7128 blocks / 89.154 B / 104.9495 B peak per message.
- Shims follow MR2: `from <new> import <name>` only, `__all__`, `REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"`, and `warnings.warn(..., DeprecationWarning, skip_file_prefixes=("<frozen importlib",))`. A shim defines nothing, and every in-repo caller is repointed.
- Same-commit housekeeping (MR4) covers:
  - `platform/CLAUDE.md` citations;
  - the "Adding a venue" rewrite;
  - `ARCHITECTURE.md`;
  - `docs/DATA_DICTIONARY.md` §1;
  - the dockerfile `COPY`/`CMD`;
  - compose `command:`;
  - both Makefile test lists.
- Domain purity: `capture/domain/**` and `capture/venues/*/policies.py` import only the stdlib, `kernel` and Nautilus value types. `capture.infrastructure` is imported only by the composition roots.

**Block If:**
- The hot-path figures exceed the baseline and the excess cannot be removed without a behaviour change.

**Never:**
- Touching `nautilus_trader/` or `crates/`.
- Adding a dependency.
- Re-baselining `hotpath_baseline.json`.
- Renaming a compose service or container.
- Changing a ledger site string or a Redis payload.
- Leaving any subclass of `CaptureService` (or `Collector`) anywhere in `platform/`.
- Deleting the old packages (26.3 does that).
- Writing `sprint-status.yaml`.
- Parking the story `awaiting-operator`: operator steps go to the DEPLOY_CHECKLIST (OPS-01) and the story finalizes `done`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New entrypoint | `python3 -m capture.venues.<v>` in the container | the same capture as before; config from the same path/env var | — |
| Old entrypoint | `python3 -m dydx_collector.collector` (a stale command) | `ModuleNotFoundError`: loud, never a silent exit-0 restart loop | none: no shim exists for the three `<venue>_collector.collector` modules |
| Old import | `from collector_core.config import CoreConfig` | the successor object, plus a `DeprecationWarning` naming 26-3 | `test_namespace` asserts `old is new` |
| dYdX plan | the compose mount `./data/dydx_config.toml:/app/dydx_collector/config.toml:rw` | read and written back at that same path | `DYDX_PLAN_PATH` (already read by `archive`) may override it |
| REST OI poll | the Bybit/dYdX poll fails | ledgered `collector.open_interest_poll`, loop continues; Bybit keeps plan ids only, dYdX keeps every market | as before |

</intent-contract>

## Code Map

- `platform/collector_core/**`: moves to `capture/`:
  - `domain/*` → `capture/domain/*`.
  - `ports.py`, `sites.py`, `book_check.py` and `feed.py` → `capture/application/`.
  - `collector.py` → `capture/application/capture_service.py`, with the class renamed `CaptureService`.
  - `application/trade_backfill.py` → `capture/application/trade_backfill.py`.
  - `infrastructure/*`, `capture_lock.py` and `gap_markers.py` → `capture/infrastructure/`.
  - `config.py` is split: `CoreConfig` + `core_config_from_dict` (+ `_check_time_source`, `_trade_feeds`, the key tuples) → `capture/application/config.py`; the loader (`VenueSchema`, `VENUE_SCHEMAS`, parse helpers, the plan builders, `venue_config_from_dict`, `load_venue_config`, `load_toml`, `plan_toml_fields`) → `capture/infrastructure/config.py`.
  - `tests/*` → `capture/tests/`.
- `platform/{dydx,bybit,hyperliquid}_collector/**` → `capture/venues/{dydx,bybit,hyperliquid}/`:
  - `client`, `trade_history`, `policies`, `open_interest` and `book_snapshot` move as-is.
  - `collector.py` → `__main__.py`.
  - `tests/` moves, fixtures included.
  - `bybit_collector/config.toml` and `hyperliquid_collector/config.toml` move.
- Venue `config.py`:
  - dYdX: `DydxConfig`, `DYDX_MAX_WS_SUBSCRIPTIONS`, `DYDX_MAX_COLLECTED_INSTRUMENTS`, `CONFIG_PATH`.
  - Bybit: `BybitConfig`, `CONFIG_PATH`.
  - Hyperliquid: `CONFIG_PATH` and `STALE_BOOK_SECONDS = 12.0`, which the loader's HL defaults use, with its comment.
  - The infrastructure loader imports these. The venue configs import `CoreConfig` from `capture.application.config`, so there is no cycle.
- Shims to delete (keyed 26-2): `dydx_collector/config.py`, `bybit_collector/config.py`, and `dydx_collector/open_interest.py`'s `_MOVED_NAMES` (`classify_liquidity`). The existing `collector_core/trade_backfill.py` shim is retargeted, and `collector_core.collector`'s `_MOVED_NAMES` becomes a plain re-export.
- `platform/tests/{test_boundaries,test_images,test_namespace,test_skew_constants,test_hotpath,test_capture_archive_handoff}.py`: the guards and cross-cutting tests.
- `collection_control/{application/ports,application/status,infrastructure/plan_store,infrastructure/markets}.py` and its tests: callers of capture.
- `archive/tools/measure_lag.py` (`importlib` of the venue clients) and `archive/tests/test_one_deleter_one_rewriter.py` (the venue package names).
- Docs and config that cite the paths: `platform/{CLAUDE.md,ARCHITECTURE.md,README.md,Makefile,docker-compose.yml,collector.dockerfile,data_api.dockerfile,live_paper.dockerfile}`, `platform/docs/{DATA_DICTIONARY,DEPLOY_CHECKLIST,DATA_INTEGRITY_AUDIT,DATABASE_SETUP}.md`, the repo-root `CLAUDE.md`, `_bmad-output/project-context.md` and `frontend/src/pages/docs/kbData.ts`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/capture/**`: `git mv` per the Code Map, so history follows. Every package gets an `__init__.py` whose docstring states its role (DESIGN-01). Rewrite in-package imports to the new dotted paths.
- [x] `capture/application/capture_service.py`: rename `Collector` → `CaptureService`; there is no subclass.
  - Replace the `VENUE` ClassVar with a required keyword `venue: str`, stored as the public read-only `venue`. `run_forever` takes the capture lock with `capture.venue`.
  - `client` becomes a factory `Callable[[OnData, Ledger], VenueFeed]`. It is called once in `__init__` with the service's `_on_data` and `_ledger`, where the venue subclass used to build it.
  - Add `add_loops(*loops)`: loops the composition root builds from the service itself (the control plane, the polls), called before `run()`.
  - Add `async poll_loop(fetch, every_seconds, *, site, failure, plan_only)`. It is the former venue `_open_interest_loop` body, generalised: rows go straight into the buffer, never through `_on_data`; with `plan_only` only the plan's ids are kept; a failure is ledgered `site` with `failure`.
  - The CRITICAL logger becomes `capture.critical`.
  - Drop the `_MOVED_NAMES` table.
  - Update the docstrings to describe the venue composition roots instead of subclasses.
- [x] `capture/venues/{dydx,bybit,hyperliquid}/__main__.py`: the composition roots.
  - `build_capture(config, plan_ids, ...)` is the former subclass `__init__`. dYdX keeps the `store_deltas` and `control_plane` keywords.
  - `build_capture_from_file(config_path=CONFIG_PATH)` is the former `build_collector`.
  - dYdX keeps `INCIDENTS` and `main()`. Every module ends with an `if __name__ == "__main__":` guard.
  - dYdX `CONFIG_PATH = Path(os.environ.get("DYDX_PLAN_PATH", "/app/dydx_collector/config.toml"))`, commented as the frozen bind-mount target; its placeholder file stays where it is.
  - Bybit and HL keep their env vars, defaulting to `Path(__file__).parent / "config.toml"` in their `config.py`.
- [x] Shim packages `collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector` (and the `collector_core` subpackages):
  - Each `__init__.py` is docstring-only and maps old module → new.
  - One MR2 shim per moved non-test module, re-exporting its public names. `collector_core.collector` serves `run_forever` and `quarantine_corrupt_parquet`; `Collector` changed shape, so it is a `_REPLACED_NAMES` entry that raises naming `CaptureService` (review pass 2026-09-28).
  - No shim for `<venue>_collector.collector`: the class has no successor of that shape, and a stale `-m` must fail loudly.
  - Delete the three 26-2-keyed shims and the old `tests/` directories.
- [x] Callers: repoint every in-repo import, including `collection_control`, `archive/tools/measure_lag.py`, the `platform/tests/*` and the moved tests.
  - Tests construct through `CaptureService(..., venue=..., client=lambda on_data, ledger: fake)` or the venue `build_capture`.
  - Replace the capture tests' `views.catalog_reads.query_second_snapshots` with a local `capture/tests` helper over `ParquetDataCatalog`, which retires the `(CAPTURE, VIEWS)` legacy edge.
  - Replace "no venue overrides a core method but `__init__`" with an AST test that no class anywhere in `platform/` subclasses `CaptureService`/`Collector`.
  - Replace "every venue collector names its lock" with each venue's wiring test asserting `build_capture(...).venue`.
- [x] `platform/tests/test_boundaries.py`:
  - map `capture`;
  - set `COMPOSITION_ROOTS`/`_CAPTURE_ROOTS` to `capture.venues.<v>.__main__` (and the venue wiring tests), plus `capture.infrastructure.config` for `COLLECTION_CONTROL`;
  - point `_SITES_MODULE`/`_LEDGER_CALLER`/`_CAPTURE_INFRASTRUCTURE` at the new modules;
  - make the domain rule match `capture.domain.*` and `capture.venues.<v>.policies`;
  - exclude re-export shims from the capture-infrastructure rule;
  - empty `LEGACY_EDGES_UNTIL`, drop the `dydx_collector.config` mapping and the legacy-package exemption, so the full AD-D2 graph is active;
  - set `THIS_STORY`.
  - Update `test_images.py`'s expected entrypoints and literal module examples.
- [x] `platform/collector.dockerfile`:
  - add `COPY platform/capture ./capture` and keep the four shim COPYs;
  - set `CMD ["python3", "-m", "capture.venues.dydx"]`.
- [x] `platform/data_api.dockerfile`: drop the `collector_core`/`dydx_collector` COPYs only if the `data_api.app` closure needs neither; `test_images` proves it.
- [x] `platform/docker-compose.yml`:
  - give the `collector` service an explicit `command: python3 -m capture.venues.dydx`;
  - set the Bybit/HL commands to `python3 -m capture.venues.{bybit,hyperliquid}`;
  - point the Bybit/HL config mount sources at `./capture/venues/<v>/config.toml`;
  - add a comment recording that the `collector` service is deliberately **not** renamed, and why.
- [x] `platform/Makefile`:
  - put `capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests` in the `test` list in place of the four old dirs;
  - have `redeploy-all` rebuild `collector bybit_collector hyperliquid_collector`;
  - fix the prose citations.
- [x] Docs:
  - `platform/CLAUDE.md`: all citations, plus "Adding a venue" rewritten as steps 1–8 over `capture/venues/<v>/{client,trade_history,policies,config,__main__}.py` with the same evidence requirements;
  - `ARCHITECTURE.md`: the module map and the diagram show the target tree;
  - `DATA_DICTIONARY.md`: §1 cites `capture/`;
  - `DATA_INTEGRITY_AUDIT.md`, `DATABASE_SETUP.md`, `README.md`, the root `CLAUDE.md`, `project-context.md`, `kbData.ts`: citations;
  - the DDD spine: an `[amended 2026-09-26: Story 26.2]` note on AD-D6/Structural Seed naming the landed paths.
- [x] `platform/docs/DEPLOY_CHECKLIST.md`: one "Deferred operator actions" entry headed `26-2-capture-package-and-venue-packages-with-entrypoints` with its commit. It gives:
  - the order: `git pull`, `make build-base` if needed, then one `make redeploy-all` restarting all three collectors;
  - the Dozzle check, with the new logger names;
  - `GET /api/errors` flat;
  - the service-rename decision;
  - the Bybit/HL config-file move, including any local VPS edits.

**Acceptance Criteria:**
- Given the moved tree, when the full platform suite runs, then the only failures are the 9 pre-existing Redis-dependent ones (`data_api` `test_archive` ×6, `test_rankings` ×1, `test_rankings_mode` ×2), with no new warnings.
- Given `grep -rn "class .*(\(CaptureService\|Collector\))" platform/`, when run, then there are no hits outside test fakes of unrelated classes, and the AST test enforces it.
- Given `test_images.py`, when the compose `command:` lines and the dockerfile CMD are parsed, then `capture.venues.{dydx,bybit,hyperliquid}.__main__` are entrypoints and their closures are within the collector image's `COPY` set.
- Given `test_boundaries.py`, when run, then `LEGACY_EDGES_UNTIL` is empty and no import relies on a legacy-package exemption.
- Given `tests/test_hotpath.py --measure`, when run, then its figures are ≤ the baseline.

## Spec Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 0, medium 2, low 7)
- defer: 5 (high 0, medium 1, low 4)
- reject: 8 (high 0, medium 0, low 8)
- addressed_findings:
  - `[medium]` `[patch]` `collector_core.collector` re-exported `Collector` although its successor changed shape (client factory, required `venue=`, no `VENUE`), so a stale caller got only a DeprecationWarning and then a TypeError deep in `__init__`. `Collector` is now a `_REPLACED_NAMES` entry whose `__getattr__` raises naming `CaptureService` and `build_capture`; `test_namespace` checks the message.
  - `[medium]` `[patch]` The no-subclass guard matched only the literal base names, so `import CaptureService as Core; class V(Core)`, a rebinding `Base = CaptureService` or `type("V", (CaptureService,), {})` escaped it. `_capture_service_subclasses` now resolves import aliases and rebindings and catches `type()` subclasses; the self-test covers each form.
  - `[low]` `[patch]` The client factory ran before the ingest queue existed, so a client that called `on_data` from its constructor hit an AttributeError. The factory now runs once the buffer, queue and stop event exist, before the policies read its capabilities.
  - `[low]` `[patch]` The capture-infrastructure import rule exempted any module that assigns `REMOVE_AFTER`. `_is_reexport_shim` now also requires a pure shim body (docstring, imports, shim constants, `warnings.warn`, a raise-only `__getattr__`), with a test.
  - `[low]` `[patch]` `test_a_failed_round_is_ledgered_and_survived` stopped the loop inside the failing fetch, so it never proved survival. It is split into a ledger test and `test_the_loop_polls_again_after_a_failed_round`, which runs a second, successful round.
  - `[low]` `[patch]` dYdX's `CONFIG_PATH` defaulted to the absolute `/app/...` path, so a run outside the image failed where it used to load the in-tree placeholder. The default now resolves from the platform root: the same container path in the image, the placeholder elsewhere.
  - `[low]` `[patch]` `poll_loop` puts rows into the buffer without going through the gate, and nothing said so. The docstring now states that it is for ungated values only (open interest), plus a `Known limit:` for the sleep before the first fetch.
  - `[low]` `[patch]` `capture/tests/catalog_kit.py` copies the views reader with no `Known limit:`. Added one, naming the drift risk, the start that is not widened, and the upgrade path (`kernel.catalog_files`).
  - `[low]` `[patch]` A new `run_forever` docstring line was 136 columns and the new shim raise line was 104, over the 100-column limit. Both are rewrapped.

### 2026-09-28 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 0, medium 1, low 6)
- defer: 2 (high 0, medium 0, low 2)
- reject: 16 (high 0, medium 0, low 16)
- addressed_findings:
  - `[medium]` `[patch]` The previous pass's loud refusal of `Collector` was an `AttributeError`, and `from collector_core.collector import Collector` (the usual stale form) turns that into a generic "cannot import name" with no successor named. The shim now raises `ImportError(..., name=__name__)`; `test_namespace` checks both the attribute and the from-import form.
  - `[low]` `[patch]` `run_forever`'s docstring had a 155-column line, although the previous pass recorded it as rewrapped. It is rewrapped now.
  - `[low]` `[patch]` The `test_boundaries.py` comment called `Collector` "the `collector_core.collector` shim's alias", but the shim refuses it. The comment is corrected.
  - `[low]` `[patch]` The shim-purity check let a `__getattr__` whose `if` body assigns (e.g. `globals()[name] = ...`) pass as pure. Every nested statement must now be an `if` or a `raise`; the self-test covers both a pure replaced-name shim and a serving one.
  - `[low]` `[patch]` DEPLOY_CHECKLIST's "No `DeprecationWarning` line may appear" could not fail, because Python hides a `DeprecationWarning` raised outside `__main__`. It is replaced by a runnable `python3 -W error::DeprecationWarning -c "import capture.venues.<v>.__main__ ..."` check inside the collector container, which was verified locally.
  - `[low]` `[patch]` The Bybit and dYdX open-interest wiring tests compared only the `poll_loop` keywords. They now also assert the interval (`open_interest_poll_seconds`) and that the fetch calls this venue's `fetch_open_interest` with this config's environment or network.
  - `[low]` `[patch]` No test proved that a venue's real client holds this service's own callbacks. Each venue now has `test_the_client_feeds_this_capture_service`, which checks `_on_data` (and `_ledger` for Bybit and Hyperliquid).

## Design Notes

- **Prior attempt.** Run `20260926-152344-3beb` implemented this exact plan before it was stopped mid-dev; the work is pinned on branch `26-2-prior-attempt` (`bcea53b404`, parent = this spec's baseline). The implementer starts from that tree (`git read-tree -m -u HEAD 26-2-prior-attempt`, then drop its copy of this spec), and re-verifies every task and AC against it rather than trusting its checkboxes.

- **Client factory.** A venue client takes its data callback and ledger at construction, and the service must exist to supply them. The composition root therefore hands `CaptureService` a factory, not an instance:

  ```python
  capture = CaptureService(config, lambda on_data, ledger: BybitClient(on_data=on_data,
      environment=env, trade_feeds=config.trade_feeds, ledger=ledger), (candle_prune_loop(store),),
      venue="BYBIT", plan=plan_ids, archive=..., live_stream=..., second_sink=CandleSink(store),
      policies=CapturePolicies(canary=BybitSequenceCanary()), trade_history=...)
  capture.add_loops(functools.partial(capture.poll_loop, lambda: fetch_open_interest(env_name),
      config.open_interest_poll_seconds, site=sites.OPEN_INTEREST_POLL,
      failure="open interest poll failed", plan_only=True))
  ```

  The ingest path is untouched, since `_on_data` stays the bound method.
- **Service name not renamed.** `collector` → `dydx_collector` would change the container, Dozzle, the `make logs/test/nightly` targets and the operator's `~/.zshrc` helpers. The rename buys symmetry only, so it is recorded as declined in compose and the checklist.
- **dYdX plan path.** The container path is AD-D12-frozen and shared with `archive` (`DYDX_PLAN_PATH`) and `make nightly`, so it stays. 26.3 decides where the placeholder goes when `dydx_collector/` is deleted.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/dydx/tests capture/venues/bybit/tests capture/venues/hyperliquid/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -p no:cacheprovider`: expected 9 known failures only.
- `cd platform && PYTHONHASHSEED=0 PYTHONPATH=. python3 tests/test_hotpath.py --measure`: expected ≤ `tests/fixtures/hotpath_baseline.json`.
- `grep -rnE "^\s*(from|import) (collector_core|dydx_collector|bybit_collector|hyperliquid_collector)" platform --include=*.py`: expected no hits (the shims import only `capture`).

## Auto Run Result

Status: done

**Summary:** Story 26.2 moves capture into the spine's tree. `collector_core/` becomes `platform/capture/{domain,application,infrastructure}/`, and the three venue packages become `capture/venues/{dydx,bybit,hyperliquid}/`, each with `client.py`, `trade_history.py`, `policies.py`, optional `open_interest.py`/`book_snapshot.py`, `config.py` and a composition-root `__main__.py`.
- `Collector` is now `CaptureService`, and nothing subclasses it. It takes a client factory and a required `venue=`, and gains `add_loops` and `poll_loop`.
- Compose and the collector image run `python3 -m capture.venues.<venue>`.
- `collector_core`, `dydx_collector`, `bybit_collector` and `hyperliquid_collector` are pure MR2 re-export shims keyed to 26-3. `LEGACY_EDGES_UNTIL` is empty, so the full AD-D2 graph is enforced.
- The compose service `collector` keeps its name. The decision is recorded in compose and in the checklist.
- The redeploy steps are one "Deferred operator actions" entry in `docs/DEPLOY_CHECKLIST.md` (OPS-01). Nothing is parked.

The story was implemented and reviewed in commit 8c894e4aae. This run was a fresh follow-up review of the whole change since baseline 7fbdb4fe76, and it applied 7 more patches.

**Files changed:**
- Story commit 8c894e4aae, listed in the first pass:
  - `platform/capture/**`
  - the four shim packages
  - the guards under `platform/tests/`
  - compose, the dockerfiles and the Makefile
  - the repointed callers and the docs
- This follow-up pass:
  - `platform/collector_core/collector.py`: the `Collector` refusal is now an `ImportError` naming `CaptureService`.
  - `platform/capture/application/capture_service.py`: the `run_forever` docstring is rewrapped.
  - `platform/tests/test_namespace.py`: the replaced-name test also covers the from-import form.
  - `platform/tests/test_boundaries.py`:
    - a stricter `__getattr__` purity check, with self-test cases;
    - the corrected `_CAPTURE_SERVICE_NAMES` comment.
  - `platform/capture/venues/{bybit,hyperliquid}/tests/test_candle_wiring.py` and `platform/capture/venues/dydx/tests/test_candle_feed.py`:
    - the poll interval and fetch target are asserted;
    - new client-callback wiring tests.
  - `platform/docs/DEPLOY_CHECKLIST.md`: a runnable stale-import check replaces the unfalsifiable log check.
  - `_bmad-output/implementation-artifacts/deferred-work.md`: 2 new entries.

**Review findings (follow-up pass):**
- 7 patches applied (1 medium, 6 low).
- 2 deferred, both pre-existing:
  - the bot_tui instrument cap is 29 against dYdX's 30;
  - `CoreConfig` accepts `nan`/`inf` and truncates non-integer values.
- 16 rejected. Most were spec-mandated design:
  - the loader imports the venue configs;
  - `DYDX_PLAN_PATH` is read by the collector;
  - the placeholder stays in `dydx_collector/` until 26.3;
  - `poll_loop` is documented as ungated.

  The rest were pre-existing gaps, entries already in the ledger (kbData `bar_intervals`), or speculative forms outside the guard's purpose (`getattr`/`types.new_class` subclassing).

**Follow-up review recommended:** false. The patches are small and local: one exception type in a shim, test assertions, one comment, one docstring and one checklist line. Each is covered by a test or was run by hand.

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests research/tests alerting/tests views/tests candles/tests capture/tests capture/venues/{dydx,bybit,hyperliquid}/tests collection_control/tests ranking/tests bot_tui/tests data_api/tests observability/tests kernel/tests tests -q -p no:cacheprovider -W error::DeprecationWarning`: 9 failed, 2199 passed.
  - The 9 failures are the known Redis-dependent ones: `data_api` `test_archive` ×6, `test_rankings` ×1, `test_rankings_mode` ×2.
  - That is 3 more passing tests than the first pass, all of them new.
- `PYTHONHASHSEED=0 PYTHONPATH=. python3 tests/test_hotpath.py --measure`: 0.7128 blocks / 89.154 B / 104.9495 B peak / 2901 ns per message. The allocations are identical to the pre-story run and under `hotpath_baseline.json`.
- `python3 -W error::DeprecationWarning -c "import capture.venues.dydx.__main__, capture.venues.bybit.__main__, capture.venues.hyperliquid.__main__"` exits 0. This is the new checklist check, run locally.
- `uvx ruff@0.15.16 format --check` and `ruff check` pass on every touched file. No added line is over 100 columns.

**Residual risks:**
- The collector image has not been built or run end to end here. The VPS redeploy is still owed through DEPLOY_CHECKLIST: `make redeploy-all`, the Dozzle check for the `capture.*` logger names, the new stale-import check, and a flat `GET /api/errors`.
- Operators who edited `bybit_collector/config.toml` or `hyperliquid_collector/config.toml` on the VPS must carry those edits over to `capture/venues/<v>/config.toml`. The checklist entry says so.
