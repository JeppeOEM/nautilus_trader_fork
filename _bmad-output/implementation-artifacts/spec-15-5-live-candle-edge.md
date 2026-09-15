---
title: 'Story 15.5: Live candle edge'
type: 'feature'
created: '2026-09-15'
status: 'in-review'
baseline_revision: 'b424c292c5ee2dc97c162be5a46f3c88fe0e7fc8'
review_loop_iteration: 0
followup_review_recommended: false
context: [
  '{project-root}/troll/CLAUDE.md',
  '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md',
]
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The chart page (Story 15.3, plus Story 15.4's indicator panes) only shows paginated history via `GET /api/candles`/`/api/indicator-series`; the currently-forming candlestick bar never updates live, so the right edge of the chart is frozen until the operator reloads.

**Approach:** Add `data_api/live_candles.py`'s `LiveCandleBus` -- a second Redis subscriber (own connection, mirroring `RankingsBus`'s shape) on `snapshots:raw`, maintaining one small per-`(instrument_id, bar_seconds)` current-bucket buffer, lazily created per active subscriber and torn down when the last one leaves. Each incoming tick for a watched pair recomputes the forming bar via `candle_dicts_from_snapshots` (AD-F7/AD-F2, never a new aggregation) and fans it out to that pair's listener queues; a bucket boundary crossing resets the buffer to the new bucket, so the *next* published bar naturally carries a new `t` (bar-close needs no separate message -- the last publish for the old bucket already was its final state). Extend `/ws/live` (`ws/live.py`) with inbound `{"subscribe": "candles:{iid}:{bar_seconds}"}` / `{"unsubscribe": ...}` control messages (new protocol surface -- today's socket is receive-only); `rankings:live` keeps broadcasting unconditionally to every connection, unchanged. Frontend gets a new `useLiveCandle(instrumentId, barSeconds)` hook (sibling to `useLiveChannel.ts`, not a rewrite of it) owning its own dedicated `/ws/live` socket, feeding `LightweightChart`'s candlestick series (not the Story 15.4 indicator panes -- those stay history-only in this story) via `ISeriesApi.update()`.

## Boundaries & Constraints

**Always:**
- The forming bar is always produced by `ml_signals.candles.candle_dicts_from_snapshots` -- never a second, independent aggregation (AD-F7/AD-F2).
- `snapshots:raw` payloads are decoded via `DydxSecondSnapshot.from_dict()` before use -- never accessed as raw dicts (`candle_dicts_from_snapshots` reads `.ts_event`/`.open_price`/etc. by attribute).
- A `(instrument_id, bar_seconds)` buffer/subscription exists only while at least one WS connection actively wants it -- created on first `subscribe`, discarded on last matching `unsubscribe`/disconnect (MEM-02 spirit).
- `rankings:live` behavior is completely unchanged -- same unconditional broadcast to every `/ws/live` connection as today, and Story 15.2's `test_rankings_live_message_reflected_by_rest_and_ws_relay` must keep passing unmodified.
- The live-candle WS message shape (`{"channel": "candles:{iid}:{bar_seconds}", "bar": {...}}`) is this story's own invention (not a Redis wire format) -- the frontend's hand-written TS type for it says so in a comment (AD-F5's citation spirit).
- `useLiveCandle` resets its tracked live bar to `null`/absent synchronously when `instrumentId` or `barSeconds` changes, before any new-subscription message can arrive (AC #4/#5) -- `ChartPage.tsx`'s existing `key={iid}` remount (Story 15.3) already covers the `instrumentId` case for free; `barSeconds` changes need the hook's own effect to reset state at the top, before resubscribing.
- `LightweightChart.tsx` applies a live bar via `series.update()` only, on the candlestick series -- `useCandles`'s `series.setData()` path (whole-page-load) and Story 15.4's indicator-pane registry (`panesRef`) are never touched by the live edge.

**Block If:** None identified -- the WS multiplexing approach (per-connection forwarder tasks into one outbox queue) and exact buffer bucket-boundary logic are implementation-owned, not decisions requiring human input.

**Never:**
- No client-side candle aggregation from raw ticks anywhere in the frontend (AD-F7/AD-F2) -- the frontend only ever receives already-aggregated bars.
- No changes to `ml_signals/candles.py`'s shared functions' existing contract.
- No live updates to Story 15.4's indicator panes (OFI/OBI/microprice/spread/volume) -- out of scope; they remain paginated-history-only in this story.
- No bar-size selector UI -- out of scope; `useLiveCandle` must accept a `barSeconds` param correctly, but nothing in this story adds a control to change it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First tick for a newly-subscribed pair | Client sends `subscribe`, then a `snapshots:raw` batch containing that instrument | One `candles:{iid}:{bar_seconds}` message with a single-snapshot forming bar | No error expected |
| Second tick, same bucket | Another `snapshots:raw` tick within the same `bar_seconds` interval | A new message with the **same** `t`, updated `o/h/l/c/v` | No error expected |
| Bucket boundary crossed | A tick lands in the next `bar_seconds` interval | Buffer resets; next published bar carries a new (later) `t` -- previous bar's last publish stands as its final state | No error expected |
| Last subscriber for a pair disconnects/unsubscribes | `WebSocketDisconnect` or explicit `unsubscribe` | That pair's buffer and listener set are torn down; no further computation for it | Never leaks memory for an unwatched pair |
| `bar_seconds` changes mid-session (future-proofing, no UI yet) | Hook's `barSeconds` prop changes | Old subscription torn down, tracked live bar cleared before the new one's first message | Never renders a stale-bar-size bar as current |
| Route remount (`iid` changes) | Operator navigates to a different coin's chart page | `ChartInner`'s `key={iid}` remount gives `useLiveCandle` fresh state; no stale bar carries over | Never renders a stale-instrument bar as current |
| A `snapshots:raw` batch has no trade for the watched instrument this tick | Batch present but no matching `instrument_id`, or matching snapshot has `close_price: None` | No message published this tick (bar unchanged) | Not an error -- `candle_dicts_from_snapshots` returns `[]` for no-trade input, handled as a no-op |

</intent-contract>

## Code Map

- `troll/data_api/live_candles.py` -- NEW: `LiveCandleBus` (own Redis connection on `snapshots:raw`, per-`(iid, bar_seconds)` lazy buffer + listener queues, `subscribe`/`unsubscribe`/`handle_batch`/`run`), module-level `live_candle_bus` instance -- mirrors `redis_bus.py`'s `RankingsBus` shape (`redis_bus.py:44-124`).
- `troll/data_api/app.py` -- add `live_candles.live_candle_bus.run(redis_bus.REDIS_URL)` as a second task in the existing `lifespan()` (`app.py:67-83`), alongside `redis_bus.bus.run()`.
- `troll/data_api/ws/live.py` -- add inbound `receive_text()`/JSON handling for `{"subscribe"|"unsubscribe": "candles:{iid}:{bar_seconds}"}`; restructure the single receive-then-relay loop into a small per-connection outbox `asyncio.Queue` fed by forwarder tasks (one for `RankingsBus`, one per active live-candle subscription), so both channel types can be sent over the one socket concurrently with inbound reads.
- `troll/ml_signals/candles.py:99-128` -- reference only, unmodified; `candle_dicts_from_snapshots` is the one aggregation function this story calls, on a small in-progress-bucket buffer instead of a full history query.
- `troll/dydx_collector/second_snapshot.py:126-163` -- reference only; `DydxSecondSnapshot.to_dict`/`from_dict` is the exact `snapshots:raw` wire round-trip.
- `troll/dydx_collector/collector.py:355-368` -- reference only; confirms `snapshots:raw`'s payload is a JSON list of `to_dict()` results, batched across instruments per publish.
- `troll/frontend/src/hooks/useLiveCandle.ts` -- NEW: opens its own dedicated `/ws/live` WebSocket (sibling to `useLiveChannel.ts`, not a shared connection), sends `subscribe`/`unsubscribe` for `candles:{instrumentId}:{barSeconds}`, resets tracked bar to `null` synchronously on `instrumentId`/`barSeconds` change before resubscribing, reconnects with the same backoff discipline as `useLiveChannel.ts`.
- `troll/frontend/src/components/chart/LightweightChart.tsx` -- add an optional `liveBar?: ChartDatum | null` prop; a new effect calls `seriesRef.current?.update(liveBar)` (candlestick series only) when it changes -- additive, sits alongside the existing mount effect, the `data`/`setData()` effect, and Story 15.4's `panes`/registry effect without touching any of them.
- `troll/frontend/src/pages/ChartPage.tsx` -- wire `useLiveCandle(instrumentId, BAR_SECONDS)` into `ChartInner`, pass its bar to `LightweightChart`'s new `liveBar` prop, alongside the existing `panes` prop.
- `troll/data_api/tests/test_live_candles.py` -- NEW.
- `troll/frontend/src/hooks/useLiveCandle.test.ts` -- NEW.
- Not modified: `troll/data_api/redis_bus.py` (rankings path untouched), `troll/ml_signals/candles.py`, `troll/frontend/src/hooks/useCandles.ts` internals (only the `BAR_SECONDS` constant gains an `export`), `useLiveChannel.ts`, `useIndicatorSeries.ts`, `paneColors.ts` (all reused as-is).

## Tasks & Acceptance

**Execution:**
- [x] `troll/data_api/live_candles.py` -- `LiveCandleBus` class + module instance -- AC #1, #2
- [x] `troll/data_api/app.py` -- start `live_candle_bus.run()` in `lifespan()` -- required for #1/#2 to run at all
- [x] `troll/data_api/ws/live.py` -- inbound subscribe/unsubscribe handling + live-candle relay, `rankings:live` path unchanged -- AC #2, #3
- [x] `troll/frontend/src/hooks/useCandles.ts` -- export `BAR_SECONDS` -- required for `ChartPage.tsx` to pass the same value to both hooks
- [x] `troll/frontend/src/hooks/useLiveCandle.ts` -- dedicated WS hook, resets on `instrumentId`/`barSeconds` change -- AC #3, #4, #5
- [x] `troll/frontend/src/components/chart/LightweightChart.tsx` -- `liveBar` prop + `series.update()` effect -- AC #3
- [x] `troll/frontend/src/pages/ChartPage.tsx` -- wire the new hook + prop -- AC #3, #4, #5
- [x] `troll/data_api/tests/test_live_candles.py` -- real `DydxSecondSnapshot` objects, incremental-vs-batch convergence, same-bucket bar identity -- AC #6
- [x] `troll/frontend/src/hooks/useLiveCandle.test.ts` -- stale-bar-clear-on-change assertion -- AC #4, #5

**Acceptance Criteria:**
- Given a `snapshots:raw` tick for a subscribed `(iid, bar_seconds)` pair, when `LiveCandleBus` processes it, then it recomputes via `candle_dicts_from_snapshots` on that pair's current-bucket buffer only, never a separate/new aggregation
- Given two consecutive ticks in the same bucket, when both are processed, then both published messages carry the same `t` (same bar identity), not two bars
- Given no WS connection currently wants a `(iid, bar_seconds)` pair, when ticks for it arrive, then no buffer is created/maintained for it
- Given the last subscriber for a pair disconnects, when the bus notices, then that pair's buffer and listeners are torn down
- Given `barSeconds` changes on an existing chart mount, when the hook re-subscribes, then the previously-tracked live bar is cleared before the new subscription's first message can render
- Given the operator navigates to a different coin's chart and back, when `ChartInner` remounts, then no stale live bar from the previous instrument is ever shown
- Given a sequence of real `DydxSecondSnapshot` objects run through the incremental buffer path, when compared against `candle_dicts_from_snapshots` called once on the same full set, then the final bar matches exactly

## Spec Change Log

## Review Triage Log

## Design Notes

- **Why no explicit "bar closed" message:** `LiveCandleBus` only ever recomputes and publishes the *current* bucket's forming bar. The moment a new tick lands in a fresh bucket, the buffer resets and the newly published bar carries a different `t` -- `ISeriesApi.update()` treats a bar with a new `t` as opening the next bar, so the previous bar's last-sent state already stands as its closed value with no separate signal needed.
- **WS multiplexing shape (implementation-owned specifics):** one per-connection `outbox: asyncio.Queue`, a forwarder task per active source (the existing `RankingsBus` queue, plus one per live-candle subscription) that does `outbox.put_nowait(await source_queue.get())` in a loop, and a reader task doing `receive_text()`+`json.loads()` to add/remove live-candle forwarder tasks as subscribe/unsubscribe messages arrive. A sender task drains `outbox` into the socket. Keeps `rankings:live`'s existing behavior provably untouched (it's still just one queue among the forwarders) while adding the new scoped channel type without a bespoke pub/sub framework (DESIGN-01).
- **Why a dedicated second WS connection for the chart page, not extending `useLiveChannel`'s existing single-connection model:** `useLiveChannel<T>` assumes one message type and a stable `latest: T | null`; multiplexing rankings + scoped candle messages onto the *same* hook instance would need a discriminator/filter layered on top of every consumer, including `RankingsPage`, which has no reason to care about candle channels. A sibling hook with its own socket is simpler and has no shared-state reason not to be separate -- the backend still only relays `rankings:live` broadcast-to-everyone as today, so an extra idle connection receiving rankings updates it ignores is harmless for a single-operator tool.
- **Why the live bar bypasses Story 15.4's pane registry entirely:** the pane registry (`LightweightChart.tsx`'s `panesRef`) is keyed by indicator id and driven by the declarative `panes` prop diffed against history-fetched data; it has no live-update path at all today, and none of this story's ACs ask for one (OFI/OBI/microprice/spread/volume stay history-only). The live bar only ever touches the separate `seriesRef` (candlestick), via its own independent effect -- zero interaction with the `panes` effect.
- **`useCandles.ts`'s `BAR_SECONDS = 60` constant** is exported (not re-duplicated as a literal in `ChartPage.tsx`'s call to `useLiveCandle`), so the two paths can't silently drift to different bar sizes.

## Verification

**Commands:**
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests -q` -- expected: all pass including new `test_live_candles.py`
- `cd troll/frontend && npm run build && npm run test && npm run lint` -- expected: clean build, Vitest passes, lint clean
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` -- expected: full regression green

**Manual checks (if no CLI):**
- `docker compose up -d data_api` then connect a WS client to `/ws/live`, send `{"subscribe": "candles:BTC-USD-PERP.DYDX:60"}`, confirm `candles:...` messages arrive on live ticks and stop after `{"unsubscribe": ...}`.
