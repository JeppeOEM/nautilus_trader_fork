# Story 21.6: No hidden errors (DATA-07)

Status: done

## Story

As the dashboard operator,
I want every failure in our own code to be loud and counted, never filtered, dropped or muted,
so that I can be certain when something is wrong instead of trusting a quiet log.

## Acceptance Criteria

1. `ml_signals.error_ledger.record()` logs ERROR + traceback and counts per site; `GET /api/errors` returns the counts and last detail.
2. Every data-dropping / carry-on site found by the survey uses it (collector, live candles, ranking engine, metrics computer, catalog reads, technicals).
3. No fabricated values on failure: unparseable `volume24H` no longer becomes 0; a failed technicals coin is reported in `errors`, not `{}`, and is never cached.
4. `/api/candles` fails the request (HTTP 500 + ledger) on an impossible candle instead of dropping it.
5. The frontend shows a non-dismissible `ErrorBar` for every `console.error`, uncaught error, unhandled rejection and backend ledger entry.
6. The vite ws-proxy reset log is still an ERROR (first line + burst count), only repeated stack traces are collapsed.
7. D-24 fixed at the cause: `normalize_snapshot_schema` migration (dry-run default, backup required), test reproduces the bug and the fix.
8. OOM (D-06) explicitly excluded.

## Dev Agent Record

### Completion Notes List

Survey covered ~90 `except`/`.catch` sites; converted the data-dropping ones in the collector, data_api, ranking_engine and ml_signals. Left as-is (already loud and not a malfunction of ours, or environmental): Redis reconnect warnings, non-JSON client WS frames, `webbrowser.open` failures, live_paper's ERROR-level lost-fill log (separate process), bot_tui redraw logging. NOT done: collector-process ledger counts are not in `/api/errors` (D-30); `normalize_snapshot_schema --apply` not run on any real catalog; vite burst count is reported at the next event, so a final burst's trailing count is not printed if no further reset follows.
