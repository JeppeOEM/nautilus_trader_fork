---
title: 'DW bundle lint-and-test-hygiene: ruff-clean platform/, closed candle stores, deterministic seed test, fold guard'
type: 'chore'
created: '2026-10-05'
status: 'done'
final_revision: '8215f6a834b914280cfe4c25597c7de7db48e63b'
baseline_revision: '0940f71b475a173f09317f3ea623766aeff9acd8'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** Four deferred-work entries (DW-179, DW-180, DW-245, DW-192). First, pinned ruff 0.15.16 reports 19 findings over `platform/` and 1 unformatted file, so a commit that touches those lines is blocked by pre-commit. Second, the suite under `-W default` prints about 69 `ResourceWarning: unclosed database`, plus one unclosed event loop and one socket. Capture never closes the candle store it is handed, even on a crash-restart in production, and tests open stores and connections they never close. Third, `test_seed_wide_bar_reads_raw_seconds_plus_unflushed_tail` and four sibling seed tests read the wall clock. They fail when "now" sits within a second of a bucket edge. Fourth, the "only the two production folds turn seconds into bars" invariant from Story 24.1 has no automated guard.

**Approach:**
- Fix every ruff finding at its source and format the one file.
- Give the candle store a closing owner: the `SecondSink` port gets `close()`, and `CaptureService.run()` closes its sink however it ends.
- Close every store and connection the tests open.
- Inject a clock into `LiveCandleBus` and pin it in the seed tests.
- Add an AST static guard, `platform/tests/test_folds.py`, that allows exactly the two production folds plus the named verification reference folds.

## Boundaries & Constraints

**Always:**
- `ruff check platform/` and `ruff format --check platform/` with the pinned v0.15.16 and the repo config exit 0.
- A finding is fixed in code. A `# noqa: <RULE>` is allowed only where the rule is a false positive for that line, and it carries a one-line reason.
- Behaviour stays unchanged except the new sink close. A ruff fix in production code (`D401` docstring rewording, `SIM105`, `C901` extraction) keeps the semantics byte-for-byte.
- Sink close order: `run()` closes the sink after the final flush and the live-stream close, in an outer `finally`, so a failure before the main loop starts also closes it. A failing `close()` is ledgered (as `_disconnect` does), never raised over the original exception.
- The fold guard reuses `tests/_source_tree.python_modules()`, is AST-based, and has a module-level allowlist with a reason per entry. It fails on an allowlisted function that no longer exists or no longer matches. It carries a scanner self-test: a synthetic third fold is caught, and indicator per-bucket sampling (`views/chart_series.replay_bucket_samples`) is not.

**Block If:** A ruff finding can only be cleared by changing observable behaviour, or by editing `nautilus_trader/` or `crates/`.

**Never:**
- Edit `_bmad-output/implementation-artifacts/deferred-work.md`.
- Touch `nautilus_trader/`, `crates/`, or root `pyproject.toml` ruff config (no per-file ignores added to silence findings).
- Blanket `filterwarnings` or `-W ignore`.
- Commit the user's live `platform/data/preferences/*.toml` edits.
- Upgrade pytest-asyncio (pinned 0.23.8 in root `pyproject.toml`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean stop | `run()` exits via `stop()` | sink closed once, after final flush | none |
| Early crash | `fetch_instruments` raises in `run()` | sink still closed, exception propagates | none |
| Close fails | sink `close()` raises | ledgered at a capture site, original outcome kept | logged, not raised |
| Bucket edge | clock pinned at bucket start + k, `1 s < k < bucket - 1 s` | seed tests pass at any wall time | none |
| Third fold | a new function with max+min reductions and an OHLC record, or `bucket_start*`/`reduceat` | guard fails naming `module.function` | assertion message lists offenders |
| Indicator sampling | `replay_bucket_samples` | not flagged | none |

</intent-contract>

## Code Map

- `platform/capture/application/ports.py:238` -- `SecondSink` Protocol (add `close()`)
- `platform/candles/application/sink.py` -- `CandleSink` (add `close()` → `store.close()`)
- `platform/capture/application/capture_service.py:2398` -- `run()`; its `finally` closes `_live_stream` but never the sink
- `platform/capture/venues/{dydx,bybit,hyperliquid}/__main__.py` -- `store_from_env` opens the store per `build_capture` (that is, per restart attempt)
- `platform/candles/infrastructure/sqlite_store.py:319` -- `CandleStore` (has `close()`)
- `platform/capture/tests/test_collector.py` -- fake sinks `_RecordingSink` and `_OrderingSink` need `close()`
- Venue tests with leaks:
  - `capture/venues/dydx/tests/`: `test_collector_resilience.py`, `test_candle_feed.py`, `test_collector_trade_ohlc.py`, `test_ws_raw_sink.py`
  - `capture/venues/bybit/tests/`: `test_candle_wiring.py`, `test_sequence_canary.py`
  - `capture/venues/hyperliquid/tests/`: `test_candle_wiring.py`
  - all build capture through `build_capture` and never close it
- `platform/candles/tests/test_candle_store.py`, `test_forming_matches_stored.py:71`, `test_verified_days.py:62`; `platform/bots/tests/test_trade_history.py:352` -- unclosed connections and stores
- `platform/capture/venues/hyperliquid/tests/test_client.py:177` -- `asyncio.new_event_loop()` that is never closed
- `platform/views/live_candles.py:205,326` -- `LiveCandleBus.__init__`; `seed` reads `time.time_ns()`
- `platform/views/tests/test_live_candles.py` -- wall-clock seed tests at `:195`, `:224`, `:259`, `:274` and `:657`; fixed `_BASE_NS`
- `platform/kernel/fold.py:88` `fold_trades`, `platform/candles/domain/fold.py:142` `fold_arrays` -- the two production folds
- Verification reference folds: `platform/verification/domain/reference_signals.py:490` `_fold_bucket`, `candle_check.py:495` `merge_candles`, `trade_check.py:239` `fold_second`
- `platform/tests/_source_tree.py`, `platform/tests/test_boundaries.py` -- guard idiom

## Tasks & Acceptance

**Execution:**
- [x] Each ruff-flagged file -- fix every finding from `ruff check platform/` and run `ruff format` on `platform/bots/domain/config.py` -- DW-179.
  - The files: `.planning/debug/crossed-book-artifacts/reference_ws_check.py`, `bots/infrastructure/fills_store.py`, `bots/tests/test_node.py`, `capture/venues/bybit/tests/test_collector.py`, `capture/venues/dydx/tests/test_integration.py`, `data_api/app.py`, `data_api/tests/test_settings.py`, `data_api/tests/test_ws_live_candles.py`, `data_api/ws/live.py`, `research/tests/test_backtest_runner.py`, `views/preferences.py`, `views/tests/test_indicator_picker_custom.py`, `views/tests/test_indicator_picker_native.py`.
- [x] `ports.py`, `sink.py`, `capture_service.py` -- add `SecondSink.close()` and `CandleSink.close()`. Wrap `run()` so an outer `finally` closes the sink and ledgers a close failure. Add `close()` to the fake sinks -- the production owner for DW-180 (a restart no longer leaks one connection per attempt).
- [x] `capture/tests/test_collector.py` -- test that the sink is closed after a clean `run()` and after a `run()` that raises before its loops -- covers the matrix rows.
- [x] `platform/capture/venues/conftest.py` (new) -- autouse fixture that records every `CandleStore` built during a venue test and closes it at teardown -- covers every `build_capture` caller, now and future, without editing each helper.
- [x] Candle, bots and Hyperliquid tests -- close each connection and store: `contextlib.closing`, or `yield` then `close()` in the fixture; close the Hyperliquid loop in a `finally`.
- [x] `views/live_candles.py` -- keyword-only `clock: Callable[[], int] = time.time_ns`, used by `seed` -- DW-245.
- [x] `views/tests/test_live_candles.py` -- pin the clock in all five seed tests from `_BASE_NS` (mid-bucket). Make the `approx` exact.
- [x] `platform/tests/test_folds.py` (new) -- the AST fold guard, its allowlist and its scanner self-tests -- DW-192.

**Acceptance Criteria:**
- Given HEAD after the change, when the pinned ruff runs `check` and `format --check` on `platform/`, then both exit 0.
- Given the `make test` package list run on the host with `-W default`, when the suite finishes, then no `unclosed database` or unclosed-socket warning is printed. The only remaining `ResourceWarning` is the pytest-asyncio 0.23.8 event loop, recorded under Design Notes. The pass/fail set equals the baseline's: 29 environmental failures (redis down, the live `data/` dir, the git-walk tests).
- Given `views/tests/test_live_candles.py`, when it runs with the wall clock at any bucket edge, then it passes, because no seed test reads the real clock.
- Given a new module defining a third seconds→bars fold, when `tests/test_folds.py` runs, then it fails naming the function.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 0, low 6)
- defer: 0
- reject: 12: (high 0, medium 0, low 12)
- addressed_findings:
  - `[low]` `[patch]` `run_forever` built a service and broke out before `run()` when shut down while waiting for the capture lock, leaving its sink open. It now calls `_close_second_sink()` on that path, and the `run()` docstring says so.
  - `[low]` `[patch]` The order dependency between the sink close and the store-sharing `candle_prune_loop` was undocumented. The `run()` docstring now names it: loops are cancelled and gathered first.
  - `[low]` `[patch]` The conftest's `CandleStore.__init__` wrapper copied the real signature. It now forwards `*args, **kwargs`.
  - `[low]` `[patch]` Fold-detector blind spots (positional records, module-level or lambda folds, pandas aggregation) are now recorded as a `Known limit:` with an upgrade path in the `tests/test_folds.py` docstring.
  - `[low]` `[patch]` Five lines added by this diff were over 100 characters (the `live.py` docstring, the `preferences.py` docstring, the `test_settings.py` noqa, two in `test_folds.py`). All re-wrapped.
  - `[low]` `[patch]` The debug script `reference_ws_check.py`, after its move to asyncio for ASYNC220, had asyncio's 64 KiB `readline` limit and an orphaned `docker logs -f`. It now sets `limit=1<<24` and terminates and awaits the process in a `finally`.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 0, medium 0, low 7)
- defer: 0
- reject: 21: (high 0, medium 0, low 21)
- addressed_findings:
  - `[low]` `[patch]` `run_forever` leaked the first attempt's sink when `acquire_lock` or `quarantine_corrupt` raised (or was cancelled) between `build()` and `run()`. That span now closes the sink on any `BaseException` and re-raises, and the `run()` docstring names the path. New test: `test_run_forever_closes_the_sink_when_the_pre_run_quarantine_raises` (fails on the prior HEAD).
  - `[low]` `[patch]` The new `run_forever` close path had no test. It is now covered by the test above.
  - `[low]` `[patch]` The `_audit_catalog` docstring in `dydx/tests/test_integration.py` claimed the walk could not stall the loop. It now says the walk does block the running loop, and that this is harmless only because the collector has stopped.
  - `[low]` `[patch]` A D401 rewording in `test_indicator_picker_custom.py` changed the meaning ("Replay a placeholder"). It now describes what the function does.
  - `[low]` `[patch]` `test_the_sink_offers_exactly_the_ports_three_methods` never checked the method set. It now asserts the public callables are exactly `apply`, `watermarks` and `close`.
  - `[low]` `[patch]` `reference_ws_check.py` had two issues. Its comment implied the 16 MiB limit matched the old uncapped readline; it is now a `Known limit:` with an upgrade path. Its `finally` could raise `ProcessLookupError` over the original outcome; `terminate()` is now suppressed for that.
  - `[low]` `[patch]` The `tests/test_folds.py` `Known limit:` now also lists the subscript-OHLC plus floor-division bucketing shape.

## Design Notes

- Close the sink in `run()` rather than in `run_forever`, for the same ownership as `_live_stream`: the service closes what it was handed. In `run_forever`, `build()` hands over a fresh store each attempt, so this also ends the per-restart fd leak.
- Test teardown through the conftest wraps `CandleStore.__init__` (via `monkeypatch`) to record instances. It is the one place that sees every store, whatever import path `build_capture` used.
- Upstream warning (TEST-04 record): pytest-asyncio 0.23.8 `plugin.py` `_provide_clean_event_loop` installs a fresh loop after each async test that is never closed. That produces one `ResourceWarning: unclosed event loop` at interpreter exit. It is not fixable from platform code while the pin holds. Upgrade path: bump pytest-asyncio to ≥0.24 when the root pin moves.

## Verification

**Commands:**
- `/home/mrqdt/.cache/pre-commit/repog92hkgxa/py_env-python3.13/bin/ruff check platform/ && ... ruff format --check platform/` -- expected: exit 0
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. -W default -p no:cacheprovider <make test list> -q` -- expected: same 29 failures as baseline; `grep -c "unclosed database"` = 0
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. bots/tests --deselect bots/tests/test_node.py -q` (excluding the TradingNode tests that hang) -- expected: pass

## Auto Run Result

Status: done

**Summary:** This was a follow-up review pass over the completed bundle (DW-179, DW-180, DW-245, DW-192), baseline `0940f71b47` to `a79bcb9457`.
- The bundle's earlier result still holds: ruff is clean, `SecondSink.close()` is owned by `CaptureService.run()`, `LiveCandleBus` takes a `clock`, and the AST fold guard is in place.
- This pass closed one remaining sink-leak path in `run_forever` and fixed six smaller items in docs and tests.

**Files changed (this pass):**
- `capture/application/capture_service.py` -- `run_forever` closes a built, never-run sink when the lock or quarantine step raises. The `run()` docstring is updated.
- `capture/tests/test_coverage.py` -- a regression test for that path.
- `candles/tests/test_sink.py` -- the port's method set is now asserted.
- `capture/venues/dydx/tests/test_integration.py`, `views/tests/test_indicator_picker_custom.py` -- the docstrings are corrected.
- `.planning/debug/crossed-book-artifacts/reference_ws_check.py` -- a `Known limit:` for the line cap, and `ProcessLookupError` suppressed on terminate.
- `tests/test_folds.py` -- the `Known limit:` is extended.

**Review:** 7 low patches applied, 0 deferred, 21 rejected. The rejected findings were by design per the spec, pre-existing behaviour that had to stay byte-for-byte, or noise. They include the conftest approach, the `preferences` fall-through `else` (unchanged from baseline), a `to_thread` race (sink calls run on the loop thread), and file encoding (matches the `_source_tree` idiom).

**Verification:**
- Pinned ruff 0.15.16: `check` and `format --check` on `platform/` both exit 0.
- `capture/ candles/ views/tests tests/` with `-W default`: 1485 passed and 3 failed. The 3 failures are the environmental git-walk tests from the baseline (`test_legacy_names` x2, `test_notebook_rules`). `unclosed database` count: 0.
- The new `run_forever` test fails against the prior HEAD's `capture_service.py` and passes with the fix.

**Residual risks:**
- The pytest-asyncio 0.23.8 unclosed event loop warning remains (see Design Notes).
- The fold guard is still a heuristic, with its blind spots recorded as a `Known limit:`.
