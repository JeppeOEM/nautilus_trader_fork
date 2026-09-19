# Story 21.x: candlestick chart data correctness (1m and wider)

Status: implemented and verified locally in a headless browser via `make frontend-dev` (2026-09-19);
NOT yet committed or redeployed on the VPS (`make redeploy-no-paper` on nifelheim rebuilds data_api + UI).

Evidence (headless Chrome over the Vite dev server, CDP-logged): ETH 1m -- live bar updated in place
within the minute (v 0.001 -> 0.002); at the minute boundary the closed bar was promoted into history
and the chart kept it; `docker restart dydx-data-api` at ~58 s -> socket re-opened at 60 s, subscribe
re-sent and `/api/candles` refetched; zero console errors/exceptions across 150 s. SOL 4H -- first live
frame already equal to the REST 4H candle for the bucket (c=111.1, v=1348.8): seeded from the rollup.
Also fixed on the way: `frontend/src/test/setup.ts` never registered RTL `cleanup`, so hooks from one
test stayed mounted and their retry timers fired into later tests.

## Problem

The candlestick chart lost bars on refresh and showed stale history after scrolling back; the 1s
view looked frozen. Scope agreed with the operator: **the 1s view is out of scope**; fix the
candlestick chart at 1m–1W; live bar above 1h seeded from the minute rollup.

## Verified facts (2026-09-19, VPS via tunnel, SOL-USD, independent source: dYdX indexer REST + WS)

- **No trade loss.** Every indexer trade is in our bars with the exact size; our 60s candles equal the
  aggregate of our 1s rows for every overlapping minute (19/19).
- **Second-level timing lag is venue-side.** dYdX delivers each trade 1–3 s after its `createdAt`
  (measured 0.95–2.88 s). The collector stamps by receive second; bars lag venue time by that much.
  Only visible at 1s; recorded in the backlog.
- **Refresh lost the newest ≤60 s.** The collector flushes to the catalog every 60 s and the history
  route read only the catalog. Fixed: `LiveCandleBus._recent` (10-min per-coin tail of traded seconds,
  MEM-02 bounded) is merged into `/api/candles` (`routes/candles.py:_catalog_plus_recent`) — same
  aggregation function, one path (AD-F7).

## Fixes in this story

Backend (`data_api/live_candles.py`):
- `seed()` no longer skips buckets wider than 1h: it reads the current bucket from the minute rollup
  (`query_minute_rollups`, rows mapped to `SecondOHLC`) and, for every bar size, unions the unflushed
  tail (`recent_rows`). Closes the old "D-18 residual". Tests: `test_seed_wide_bar_reads_minute_rollup_plus_unflushed_tail`,
  `test_seed_includes_unflushed_recent_seconds`.

Frontend:
- `useLiveCandle(iid, bs, { onReconnect, onBarClosed })`: event handlers, no extra effect.
- `useCandles`: `appendBar` promotes a closed live bar into history state (so a later `setData` —
  scroll-back prepend, replay, mode flip — can never wipe bars already shown); `refreshNewest`
  refetches and merges the newest page after a socket reconnect (`mergeByTime`, seam marker for a
  hole longer than one page). Empty initial page is retried from a fresh cursor with a visible
  message; a deterministic HTTP error (not 502/503/504) is reported, not retried forever.
- `LightweightChart`: live `update()` guarded against the series' real last point
  (`lastPaintedTimeRef`, reset by every `setData`) — removes the un-resettable latch and the
  "Cannot update oldest data" crash path; the live effect runs after the panes effect so the volume
  pane's `setData` cannot erase the forming volume bar; `data` is a dependency so the forming bar is
  repainted right after any `setData`. Time axis shows clock labels (`timeVisible`/`secondsVisible`).
- `ChartPage`: shows `loadError` text; wires the two handlers.

## Verification

1. `make frontend-dev` (tunnel down or `LOCAL_DEV=true` so data_api binds 19100). Chart at 1m and 4H:
   live bar updates within the minute; closed bars persist after scrolling back; 4H live bar equals
   the history candle right after subscribe; refresh keeps the last minute.
2. Restart `data_api` with the page open → reconnect → newest page refetched, chart never blank.
3. Per-minute cross-check against dYdX's indexer (scratch `verify.py`): 60s OHLC/volume must match for
   every overlapping minute except the edge minute (venue delivery lag).
4. `data_api` pytest (in the `troll-data_api` image) and `cd frontend && npx tsc -b && npx vitest run`.

## Backlog (named, not in this story)

- Collector second loop: `sleep(1.0)` + body drifts ~30 ms/tick → one wall second skipped every
  ~33 s; `ts_event` unaligned; trades attributed to the receive second (`collector.py` `_second_loop`).
  Visible only at 1s.
- `config.py` default `snapshot_interval_seconds=0.5` vs `config.toml` 1.0 (affects rollup `seconds_observed`).
- 1s: time-linear axis (whitespace per missing bucket) + per-bucket empty heartbeat on the live channel.
- `partial` rollup bars are drawn like complete ones; browser-vs-server clock for the history cursor;
  `catalog_stats` filename parsing unguarded; `_recent` not trimmed for a silent instrument (bounded).
