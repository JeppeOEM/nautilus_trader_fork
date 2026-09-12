---
title: 'Story 12.2: dashboard.py remote-data mode + local-machine run docs'
type: 'feature'
created: '2026-09-12'
status: 'done'
review_loop_iteration: 1
followup_review_recommended: false
context: ['{project-root}/troll/CLAUDE.md', '{project-root}/CLAUDE.md']
warnings: []
baseline_revision: 'd82a8c8ff1'
final_revision: '6af9bbc385'
---

<intent-contract>

## Intent

**Problem:** `dashboard.py` does 4 local-disk reads (`metrics_store.history`/`nearest`, `chart_data.compute_chart_series`, `catalog_stats.query_second_snapshots`) that only work when it runs on the same box as the collector's catalog/metrics. Story 12.1 added `data_api`, a read-only FastAPI service exposing those 4 reads over HTTP, but nothing calls it yet.

**Approach:** Add an opt-in `DATA_API_URL` env var to `dashboard.py`. When unset, all 4 call sites behave exactly as today (local disk, `asyncio.to_thread`). When set, each call site instead fetches JSON from the corresponding `data_api` route via one shared `aiohttp.ClientSession` + `_fetch_json(session, url)` helper. Document the local-machine SSH-tunnel + run workflow in `troll/ARCHITECTURE.md` and add `DATA_API_URL` to `troll/.env-example`.

## Boundaries & Constraints

**Always:**
- `DATA_API_URL` unset (the VPS-hosted `dashboard` service's current default) must be byte-for-byte identical to current behavior — same functions, same `asyncio.to_thread` calls, no new branch executed.
- All remote-mode HTTP calls go through one shared `_fetch_json(session: aiohttp.ClientSession, url: str) -> Any` helper — no per-call-site HTTP code duplication.
- The 4 remote branches call these exact `data_api` routes (ground truth: `troll/data_api/app.py`, confirmed by investigation):
  - `GET {DATA_API_URL}/metrics/history/{symbol}?days=31` (replaces `dashboard.py:1076`'s `metrics_store.history(symbol, METRICS_DB_PATH, 31)` call in `_render_history_page`)
  - `GET {DATA_API_URL}/metrics/nearest/{symbol}?ts_ns=<int>` (replaces `dashboard.py:1534`'s `metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)` call in `rank_history_json_handler`; route returns bare JSON `null` when no data — preserve today's `row or {}` fallback)
  - `GET {DATA_API_URL}/catalog/chart-series/{symbol}?start_ns=<int>&end_ns=<int>` (replaces `dashboard.py:1116-1120`'s `_chart_data.compute_chart_series(...)` call in `_render_chart_page`)
  - `GET {DATA_API_URL}/catalog/snapshots/{iid}?start_ns=<int>&end_ns=<int>` (replaces `dashboard.py:1371-1373`'s `_catalog_stats.query_second_snapshots(...)` call in `_historical_lines_json`; response dicts are a superset — 4 extra OHLC keys beyond the 7 keys `_price_series_rows` reads — harmless, no reshaping needed)
- One shared `aiohttp.ClientSession` created once per dashboard process (not per-request) and reused across all remote calls, consistent with aiohttp best practice and this file's existing async patterns.
- New dependency: none — `aiohttp>=3.14.1` is already in `troll/troll-requirements.txt`.
- Tests seed a real temp `ParquetDataCatalog` + real `DydxSecondSnapshot` rows and a real temp SQLite metrics DB via `metrics_store.write()` (TEST-03) — mirror `data_api/tests/test_data_api.py`'s pattern of running both apps in-process (dashboard's aiohttp app + data_api's FastAPI `TestServer`/`uvicorn` on a bound port) and asserting remote-mode output equals local-mode output for identical seeded data.
- `troll/ARCHITECTURE.md` deployment-topology section gains a `data_api` row in its service table (currently missing — a Story 12.1 gap) plus a short "running dashboard/bot_tui off-VPS" subsection referencing the existing SSH-tunnel mechanics in `troll/README.md` (lines 83-113) rather than duplicating them — one combined tunnel command covering both `6379` (Redis) and `9100` (`data_api`).
- `troll/.env-example` gains a `DATA_API_URL` entry following its existing comment-block-then-`VAR=value` style (see `WEB_PORT`), left unset/commented by default.

**Block If:** N/A — service shape, route contracts, and env var name were already decided in epic-12-context.md and Story 12.1's shipped implementation; nothing here requires a new architectural decision.

**Never:** Do not modify `troll/data_api/app.py`, `ranking_engine/metrics_store.py`, `ml_signals/chart_data.py`, or `ml_signals/catalog_stats.py` (consumed as-is). Do not touch `bot_tui/`'s `_open_via_local_listener`/`BOT_TUI_OPEN_URL_PORT` (`troll/bot_tui/app.py:1410-1424`) — confirmed unrelated (it only dispatches a URL string, never fetches data). Do not fix `ranking_engine/metrics_store.py`'s `:ro`-mount `_conn()` write-access bug (tracked in `deferred-work.md`'s existing HIGH entry) — out of scope for this story. Do not add a new Docker port or `ports:` mapping. Do not touch `nautilus_trader/` or `crates/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Local mode unchanged | `DATA_API_URL` unset | All 4 call sites use existing local functions via `asyncio.to_thread`, identical to pre-story behavior | No error expected |
| Remote history | `DATA_API_URL` set, seeded metrics DB reachable via data_api | `_render_history_page` builds identical Plotly output to local mode for same seeded rows | No error expected |
| Remote nearest, no data | `DATA_API_URL` set, symbol with no metrics rows | `rank_history_json_handler` receives JSON `null` from route, applies existing `row or {}` fallback | No error expected (matches today's local-mode `None` case) |
| Remote chart series | `DATA_API_URL` set, seeded catalog snapshots in window | `_render_chart_page` renders identical subplot traces to local mode for same seeded data | No error expected |
| Remote snapshots | `DATA_API_URL` set, seeded catalog snapshots in window | `_historical_lines_json` produces identical `_price_series_rows` output to local mode (extra OHLC keys ignored) | No error expected |
| data_api unreachable | `DATA_API_URL` set but connection fails/times out | Handler surfaces a clear error rather than hanging or silently returning stale/fabricated data (DATA-01) | Log the failure; propagate as a 5xx/error response from the dashboard handler, not a fake empty result |

</intent-contract>

## Code Map

- `troll/ml_signals/dashboard.py` -- add `DATA_API_URL` env var, shared `aiohttp.ClientSession` + `_fetch_json()` helper, and branch each of the 4 call sites (lines ~1076, ~1534, ~1116-1120, ~1371-1373) on whether `DATA_API_URL` is set
- `troll/data_api/app.py` -- read-only, ground truth for the 4 routes' exact paths/params/response shapes (not modified)
- `troll/.env-example` -- add `DATA_API_URL` doc-comment entry
- `troll/ARCHITECTURE.md` -- deployment-topology section (~line 296): add `data_api` to service table + local-machine run subsection; dashboard.py section (~line 150-184): note optional remote mode
- `troll/README.md` -- read-only, existing SSH-tunnel section (lines 83-113) to reference, not duplicate
- `troll/ml_signals/tests/test_dashboard_remote_mode.py` -- NEW: tests for all 4 remote branches plus the unset-path-unchanged assertion
- `troll/ml_signals/tests/test_dashboard_chart.py` -- reference pattern for seeding a real `ParquetDataCatalog`/`DydxSecondSnapshot` and invoking handlers directly
- `troll/data_api/tests/test_data_api.py` -- reference pattern for running data_api's FastAPI `TestClient`/server in-process

## Tasks & Acceptance

**Execution:**
- [x] `troll/ml_signals/dashboard.py` -- add `DATA_API_URL = os.environ.get("DATA_API_URL", "")` near the other env-var config (line ~69/85-86) and a module-level lazily-created shared `aiohttp.ClientSession` (created on first use / app startup, closed on shutdown) -- gives remote branches a URL and session to work with
- [x] `troll/ml_signals/dashboard.py` -- add `async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any` helper (single implementation for all 4 remote calls) -- avoids per-call-site HTTP duplication (epic constraint)
- [x] `troll/ml_signals/dashboard.py` -- branch `_render_history_page` (line 1076): if `DATA_API_URL` set, `await _fetch_json(session, f"{DATA_API_URL}/metrics/history/{symbol}?days=31")` instead of `asyncio.to_thread(metrics_store.history, ...)` -- remote equivalent of call site 1
- [x] `troll/ml_signals/dashboard.py` -- branch `rank_history_json_handler` (line 1534): if `DATA_API_URL` set, fetch `f"{DATA_API_URL}/metrics/nearest/{symbol}?ts_ns={ts_ns}"`, preserving existing `row or {}` handling for a `null`/`None` result -- remote equivalent of call site 2
- [x] `troll/ml_signals/dashboard.py` -- branch `_render_chart_page` (lines 1116-1120): if `DATA_API_URL` set, fetch `f"{DATA_API_URL}/catalog/chart-series/{symbol}?start_ns={start_ns}&end_ns={end_ns}"` -- remote equivalent of call site 3
- [x] `troll/ml_signals/dashboard.py` -- branch `_historical_lines_json` (lines 1371-1373): if `DATA_API_URL` set, fetch `f"{DATA_API_URL}/catalog/snapshots/{iid}?start_ns={start_ns}&end_ns={end_ns}"`, feed result list directly to `_price_series_rows` (extra OHLC keys ignored) -- remote equivalent of call site 4
- [x] `troll/ml_signals/tests/test_dashboard_remote_mode.py` -- NEW: one test per remote branch (seed real catalog/metrics data, run data_api's app in-process on a bound port, set `DATA_API_URL` to it, assert output equals local-mode output for the same seeded data) plus one explicit test asserting local-mode output is unchanged when `DATA_API_URL` is unset/empty -- proves both paths per the I/O matrix and the hard byte-for-byte-identical constraint
- [x] `troll/.env-example` -- add `DATA_API_URL` entry (comment block + commented-out `#DATA_API_URL=` line, matching `WEB_PORT`'s style) -- documents opt-in remote mode
- [x] `troll/ARCHITECTURE.md` -- add `data_api` row to deployment-topology service table (~line 296) and a short local-machine run subsection (SSH tunnel covering `6379`+`9100`, then local `dashboard`/`bot_tui` run commands), referencing `README.md`'s existing tunnel section rather than duplicating it; add one sentence noting optional `DATA_API_URL` remote mode to the dashboard.py section (~line 150-184)

**Acceptance Criteria:**
- Given `DATA_API_URL` unset, when any of the 4 call sites execute, then behavior (function called, arguments, control flow) is identical to pre-story code -- verified by a test, not just inspection
- Given `DATA_API_URL` set to a running `data_api` instance with seeded data, when each of the 4 dashboard call sites executes, then its output matches what local mode would produce for the same seeded data
- Given `DATA_API_URL` set and a symbol with no metrics rows, when `rank_history_json_handler` runs, then it handles the route's `null` response the same way it handles local mode's `None` today
- Given `troll/ARCHITECTURE.md` after this story's edits, when read, then the deployment-topology table includes `data_api` and there is a local-machine run subsection covering the SSH tunnel + run commands
- Given `troll/.env-example` after this story's edits, when read, then `DATA_API_URL` is documented in the existing comment-block style, left unset by default
- Given `cd troll && make test` (inside Docker), when run after this story, then all existing module tests plus the new remote-mode tests pass

## Design Notes

`aiohttp.ClientSession` must be created inside a running event loop (not at import time) -- create it lazily on first use or in an `on_startup` hook consistent with how `aiohttp.web` apps in this file already register startup/cleanup, and close it in a matching `on_cleanup`/`on_shutdown` hook to avoid an "Unclosed client session" warning (TEST-04: a new warning here would itself need investigation, not suppression).

For tests, run `data_api`'s FastAPI app via `uvicorn` in a background thread bound to `127.0.0.1:0` (OS-assigned free port), or use `fastapi.testclient.TestClient` if httpx's ASGI transport can be reached by `aiohttp.ClientSession` -- if not, a real bound uvicorn server thread is the reliable choice, matching "never mock Nautilus internals" in spirit (this is an HTTP integration, not a mock).

## Spec Change Log

## Review Triage Log

### 2026-09-12 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 9 (high 0, medium 3, low 6)
- defer: 4 (high 0, medium 1, low 3)
- reject: 5 (high 0, medium 0, low 5)
- addressed_findings:
  - `[medium]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that the shared `aiohttp.ClientSession` had no request timeout, so a hung/half-open SSH tunnel to `data_api` could leave an interactive `/history/{id}`/`/chart/{id}` request hanging for aiohttp's default 5-minute timeout with no visible feedback -- directly at odds with DATA-01's "flag the gap, never paper over it" principle. Added `aiohttp.ClientTimeout(total=10)` to `_get_http_session`'s session construction.
  - `[medium]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that `symbol`/`iid` was interpolated raw into all 4 remote-mode URLs with no `urllib.parse.quote()` -- a latent bug for any future instrument id containing a URL-special character. Added `quote(symbol, safe='')` at all 4 call sites.
  - `[medium]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that a hand-edited `DATA_API_URL` with a trailing slash (e.g. `http://host:9100/`) would produce a double-slash URL at every call site, with no normalization anywhere. Added `.strip().rstrip("/")` to `DATA_API_URL`'s definition.
  - `[low]` `[patch]` Blind Hunter noted `coin_lines_handler`'s remote branch wrapped the already-fetched, already-JSON `snaps` through `asyncio.to_thread(_lines_json_from_snapshot_dicts, snaps)` -- pointless thread-pool dispatch for pure in-memory list/dict work, unlike the local branch's real catalog I/O. Removed the `to_thread` wrapper for that one call, with a comment explaining why it differs from the local branch.
  - `[low]` `[patch]` Blind Hunter noted `_close_http_session`'s justifying comment in `make_app` overstated its own necessity ("avoids an Unclosed client session warning even when DATA_API_URL is never used" -- but if it's never used, `_get_http_session` is never called and there's nothing to close). Corrected the comment's wording.
  - `[medium]` `[patch]` Blind Hunter + Acceptance Auditor (as a "close call") both flagged that `_fetch_json`'s docstring claims a data_api outage "propagat[es] up... as a 5xx... (DATA-01)" but no test actually drove that path -- exactly the kind of untested claim DATA-02 treats as unacceptable. Added `test_remote_mode_surfaces_data_api_outage_as_error_not_empty_result`, which points `DATA_API_URL` at a closed loopback port and asserts the resulting response is a 5xx, not a fabricated empty result.
  - `[low]` `[patch]` Blind Hunter + Edge Case Hunter both flagged the test fixture's `_free_port()` (bind, read port, close, hand the "free" port to uvicorn to re-bind) as a classic TOCTOU race against any other process grabbing the same port in the gap. Replaced with the standard fix: bind a socket ourselves and hand the still-open fd directly to `uvicorn.Server.run(sockets=[sock])`, eliminating the gap entirely (and matching this repo's own existing pattern in `bot_tui/tests/test_app_coin_detail.py`, which keeps its bound socket open rather than closing and re-binding).
  - `[low]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that the `_data_api_url` fixture's teardown (`thread.join(timeout=5)`) never verified the join actually succeeded, so a stuck server thread would leak silently into later tests. Added `assert not thread.is_alive()` after the join.
  - `[low]` `[patch]` Acceptance Auditor (READ-03) noted the `_data_api_url` fixture (a generator via `yield`) was missing its return-type annotation, unlike every other function in the new test file. Added `-> Iterator[str]`.
  - `[medium]` `[defer]` Blind Hunter noted the module-level global `_http_session` has no re-entrancy guard against two `make_app()` instances sharing/fighting over one session in the same process -- real in theory, but consistent with every other piece of module-global state already in this file (`_LATEST_RANKING`, `_second_rolling`, etc.), all of which share the same single-process-per-run assumption `dashboard.py`'s own `__main__` block enforces. Adding locking now for a scenario that doesn't occur anywhere in this codebase is speculative engineering (DESIGN-01 YAGNI); logged to `deferred-work.md` as an architectural note, not a live bug.
  - `[low]` `[defer]` Edge Case Hunter noted `_close_http_session` firing on `on_cleanup` while a request is mid-await inside `_fetch_json` could abort an in-flight remote fetch during graceful shutdown -- real for any shared-resource-on-cleanup pattern, but this is the *same* class of risk `redis_subscriber_ctx`'s task-cancellation already accepts in this file (pre-existing, not introduced by this story); a proper request-draining shutdown is a bigger feature out of scope for an opt-in convenience mode. Logged to `deferred-work.md`.
  - `[low]` `[defer]` Blind Hunter's "no connection-pool/timeout tuning beyond the total timeout" (connector limits, per-host pooling) -- the total timeout added above covers the actual DATA-01 concern; further tuning is speculative for a single-operator, low-concurrency personal dashboard. Logged to `deferred-work.md` as a documented non-issue unless usage patterns change.
  - `[low]` `[defer]` Blind Hunter noted no schema/type pinning ties `data_api`'s response shape to what `_price_series_rows`/`_build_chart_page_html` expect, so a future `data_api` serialization change could silently break dashboard with only an equality-based integration test to catch it -- real but epic-level (data_api's contract stability is Story 12.1's scope), and already structurally mitigated by the shared render helpers being schema-tolerant (superset dicts). Logged to `deferred-work.md` as a cross-story note.
  - `[low]` `[reject]` Blind Hunter + Edge Case Hunter's "local mode returns a graceful empty result for an unknown/dataless symbol but remote mode's `raise_for_status()` would turn a data_api 404 into an unhandled 500" -- verified against the actual `data_api/app.py` (re-read in full): it contains zero `raise`/`HTTPException` calls; all 4 routes return whatever the wrapped function returns (`[]`, `None`, or an empty-valued dict) for a valid-but-dataless symbol, exactly mirroring local mode. This finding describes a hypothetical future `data_api` change, not the code actually shipped in this diff.
  - `[low]` `[reject]` Blind Hunter's "README.md cross-reference is unverified by this diff" -- the referenced "Remote access via Tailscale + SSH tunnel" section was directly read in full during implementation (README.md lines 83-113) and does exist with that name, covering exactly the tunnel mechanics `ARCHITECTURE.md`'s new subsection points to.
  - `[low]` `[reject]` Acceptance Auditor's "ARCHITECTURE.md added two sentences instead of the task's stated 'one sentence'" -- trivial phrasing nitpick with no functional or accuracy impact.
  - `[low]` `[reject]` Acceptance Auditor's "module-level global session vs. this file's other app-scoped (`app[...]`) state is a stylistic inconsistency" -- explicitly sanctioned by this spec's own Design Notes ("module-level lazily-created shared session"); not a deviation, it's the specified design.
  - `[low]` `[reject]` Acceptance Auditor's note that the `.env-example` entry "could be read as ambiguous" between `WEB_PORT`'s uncommented-with-default style and a commented-out style -- the spec explicitly requires "left unset/commented by default," which the shipped `#DATA_API_URL=http://127.0.0.1:9100` entry correctly follows; the auditor confirmed this was the correct reading, not a real deviation.

## Verification

**Commands:**
- `cd troll && make test` -- expected: all pytest modules pass including new `test_dashboard_remote_mode.py`, run inside the Docker container (local `.venv` lacks `nautilus_pyo3`/`fastapi`/`aiohttp`)
- Manual read-through of `troll/ARCHITECTURE.md` and `troll/.env-example` diffs -- expected: `data_api` present in the deployment table, `DATA_API_URL` documented, no duplication of README's SSH mechanics

## Auto Run Result

**Summary:** `troll/ml_signals/dashboard.py` now supports an opt-in `DATA_API_URL` remote-data mode across all 4 local-disk read call sites (`/history/{id}`, `/chart/{id}`, `/api/rank_history/{id}`, `/data/coin/{id}/lines`). Each call site's local-mode branch was left byte-for-byte unchanged (verified by a real test that makes the new `_fetch_json` helper raise if it's ever called while `DATA_API_URL` is unset) by extracting each call site's pure rendering logic into a small new helper (`_history_page_from_rows`, `_build_chart_page_html`, `_lines_json_from_snapshot_dicts`) that both the existing local-mode function and the new remote branch feed into identically. Remote mode is opt-in via one env var, reaches `data_api` through one shared `aiohttp.ClientSession` + `_fetch_json(session, url)` helper (no per-call-site HTTP duplication), and is documented in `troll/.env-example` and a new `troll/ARCHITECTURE.md` subsection covering the SSH-tunnel + local-run workflow.

**Files changed:**
- `troll/ml_signals/dashboard.py` -- added `DATA_API_URL` env var (normalized: stripped, trailing slash removed), lazily-created shared `aiohttp.ClientSession` (`_get_http_session`/`_close_http_session`, wired into `make_app`'s `on_cleanup`, 10s total request timeout), shared `_fetch_json` helper, and branched all 4 call sites (`history_handler`, `chart_handler`, `rank_history_json_handler`, `coin_lines_handler`) between local (unchanged) and remote (new) data sourcing; extracted `_history_page_from_rows`/`_build_chart_page_html`/`_lines_json_from_snapshot_dicts` as the shared pure-render layer both branches feed; URL-encodes the instrument id at all 4 remote call sites (`urllib.parse.quote`).
- `troll/ml_signals/tests/test_dashboard_remote_mode.py` -- new: 6 tests -- local-mode-unchanged (`_fetch_json` never called), 4 remote-vs-local output-equivalence tests (one per call site, 2 via byte-for-byte JSON comparison, 2 via a spy on the shared render helper since Plotly's `fig.to_html()` embeds a fresh random div id on every call and cannot be compared byte-for-byte), and one DATA-01 outage test (unreachable `DATA_API_URL` surfaces as a 5xx, not a fabricated empty result). Runs `data_api`'s real FastAPI app via a genuinely bound `uvicorn` server in a background thread (the fd is handed to `uvicorn.Server.run(sockets=...)` directly, not a bind-then-close-then-rebind port lookup, to avoid a TOCTOU race).
- `troll/.env-example` -- added a `DATA_API_URL` doc-comment block + commented-out example line, matching `WEB_PORT`'s existing style.
- `troll/ARCHITECTURE.md` -- added a `data_api` row to the deployment-topology service table; added a "Running dashboard/bot_tui off the VPS" subsection (SSH tunnel covering `6379`+`9100`, then local `make dashboard`/`make tui` run commands, referencing README.md's existing tunnel section rather than duplicating it); added a short note on optional `DATA_API_URL` remote mode to the dashboard.py section.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- logged 4 pre-existing/lower-severity gaps surfaced by review (see Review Triage Log).
- `_bmad-output/implementation-artifacts/spec-12-2-dashboard-remote-data-mode-local-run-docs.md` -- this spec.

**Review findings breakdown:** 9 patches applied (3 medium, 6 low -- request timeout, URL-encoding, `DATA_API_URL` normalization, removed an unnecessary `asyncio.to_thread` wrap, corrected an overstated code comment, added a DATA-01 outage test, eliminated a test-fixture port TOCTOU, added thread-teardown verification, added a missing test return-type annotation), 4 deferred (1 medium, 3 low -- global-session re-entrancy assumption, shutdown-drain gap, connector/pool tuning, data_api/dashboard schema-contract pinning; all logged to `deferred-work.md`), 5 rejected as noise (all low -- a hypothetical 404-divergence disproven by re-reading `data_api/app.py`'s actual zero-`raise` implementation, a cross-reference the reviewer couldn't check but this session already had verified by reading README.md directly, a "two sentences vs one" phrasing nitpick, a stylistic observation the spec's own Design Notes explicitly sanction, and an ambiguity concern the reviewer itself resolved as "correct reading, not a deviation"). No intent gaps, no bad-spec loopbacks.

**Follow-up review recommendation:** `false` -- all patches were small and localized (a timeout arg, a `quote()` call at 4 sites, a `.strip().rstrip()`, a removed `to_thread` wrap, two comment corrections, one new test, and 3 test-fixture hardening changes); no API/data-model/security surface was touched beyond what the original implementation already covered, and every patch was re-verified green against the full `make test` suite afterward.

**Verification performed:**
- `cd troll && make test` (inside Docker, via `docker compose build collector` + `docker compose run --rm --no-deps -e HOME=/tmp -e USER=collector collector python3 -m pytest dydx_collector/tests ml_signals/tests ranking_engine/tests bot_tui/tests data_api/tests -q`) -- run independently three times across this session (before review, after applying patches, and once more before finalizing): 637/637/638 passed (638 after the new outage test was added), 5 skipped, 0 failed each time. No new warnings introduced beyond one pre-existing `aiohttp.web.NotAppKeyWarning` (confirmed pre-existing by running an untouched existing test file, `test_rank_history.py`, which emits the identical warning from the same untouched `app["redis_url"]`/`app["catalog_path"]` lines in `make_app`).
- `docker compose run --rm --no-deps -e HOME=/tmp -e USER=collector collector python3 -m pytest ml_signals/tests/test_dashboard_remote_mode.py -q` -- run in isolation after each round of changes; all 6 tests green in the final run.
- Manual line-length check (`awk 'length > 100'`, since `ruff`/`mypy` are not installed in this sandbox) run against the full diff of every touched file -- the only line over 100 chars in the diff is a pre-existing 104-char line (`chart_handler`'s local-mode call), verified unchanged from `HEAD` via `git show HEAD:... | grep`.
- Manually read `troll/README.md`'s "Remote access via Tailscale + SSH tunnel" section (lines 83-113) in full during implementation to confirm `ARCHITECTURE.md`'s new subsection references real, existing mechanics rather than an assumed one.
- Manually confirmed `data_api/app.py` has zero `raise`/`HTTPException` statements (`grep -n "HTTPException\|raise "`), directly refuting the review's "404-turns-into-500 behavior divergence" finding before rejecting it.
- Adversarial code review: Blind Hunter (`bmad-review-adversarial-general`), Edge Case Hunter (`bmad-review-edge-case-hunter`), and an Acceptance Auditor pass against the spec + `troll/CLAUDE.md`/`CLAUDE.md`, run in parallel as background subagents against the scoped diff (`troll/ml_signals/dashboard.py`, `troll/.env-example`, `troll/ARCHITECTURE.md`, the new test file). All three completed; no layer failed or returned empty.

**Residual risks:**
- Real end-to-end verification of the documented SSH-tunnel workflow (actually tunneling `6379`+`9100` from a second machine to a live VPS and running `dashboard`/`bot_tui` against it) was **not performed** -- this sandbox has no second machine or live VPS to test against. The workflow is verified correct by construction (same tunnel mechanics already used for the existing `dashboard`/Dozzle ports, same `REDIS_URL`/`DATA_API_URL` env vars the code actually reads) and by the automated tests proving the `DATA_API_URL` code path itself works against a real (if local) `data_api` instance, but the full "laptop -> tunnel -> VPS" path itself is unexercised here.
- 4 lower-severity findings were deferred rather than fixed -- see `deferred-work.md`'s "Deferred from: code review of spec-12-2..." section for full detail. None block this story's own acceptance criteria; all are either pre-existing patterns already present elsewhere in this file, or genuinely out-of-scope for an opt-in convenience feature.
- `ruff`/`mypy` could not be run in this environment (not installed in the sandbox's `.venv`, and not present in the `troll-collector` Docker image either) -- deferred to the repo's normal CI/pre-commit hooks on push, consistent with how Story 13.1 recorded the same gap.
