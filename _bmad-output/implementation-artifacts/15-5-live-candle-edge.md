# Story 15.5: Live candle edge

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want the candlestick chart's currently-forming bar to update live without ever visibly diverging from the historical bars beside it,
so that I can trust the right edge of the chart as much as its paginated history.

## Acceptance Criteria

1. **`data_api` computes the forming bar server-side by calling `ml_signals.candles`' existing aggregation function** (`candle_dicts_from_snapshots`, the same one Story 15.3's `/api/candles` already uses for history) against incoming `snapshots:raw` ticks — never a new/parallel aggregation implementation (AD-F7/AD-F2).
2. **The computed bar is published on a derived `/ws/live` sub-channel, `candles:{instrument_id}:{bar_seconds}`, one bar per message, on tick and on bar-close** (AD-F7).
3. **The frontend subscribes to this channel for the live edge only — it never aggregates a candle itself from raw snapshot data** (AD-F7, AD-F2).
4. **A bar-size change (e.g. 1m → 1h) never leaves a stale live bar from the old bar-size overlapping the freshly-loaded historical bars for the new size.**
5. **Navigating away from a coin's chart page and back never carries a stale live bar over from the previous mount** — the live edge resumes cleanly.
6. **Test:** the server-computed forming bar matches `ml_signals.candles`' own aggregation for equivalent input ticks (TEST-01/03: real `DydxSecondSnapshot`/candle objects, no mocks).

## Tasks / Subtasks

- [ ] Task 1 — `data_api`: subscribe to `snapshots:raw`, not just `rankings:live` (AC: #1)
  - [ ] **`data_api` does not yet subscribe to `snapshots:raw` at all** — `redis_bus.RankingsBus` (Story 15.2) only handles `rankings:live`; `ws/live.py` only relays that one channel. Add a new bus (e.g. `data_api/live_candles.py`'s `LiveCandleBus`, mirroring `RankingsBus`'s shape at `data_api/redis_bus.py:44-124`) subscribed to `snapshots:raw` on its own connection, or extend the app's Redis subscription to both channels on one connection (mirroring `dashboard.py:_redis_listener`'s existing "one subscriber, two channels" pattern at `ml_signals/dashboard.py:2280-2312`) — either is acceptable, pick whichever keeps `redis_bus.py` from becoming a dumping ground for unrelated channel logic (DESIGN-01).
  - [ ] `snapshots:raw` payloads are JSON lists of `DydxSecondSnapshot.to_dict(s)` (`dydx_collector/collector.py:355-368`) — reconstruct real objects via `DydxSecondSnapshot.from_dict(d)` (`dydx_collector/second_snapshot.py:146-148`) before calling `candle_dicts_from_snapshots`, which accesses fields by attribute (`ml_signals/candles.py:99-127`), not by dict key. This also keeps the new code TEST-03-compliant (real Nautilus-adjacent objects, not ad hoc dicts) — do **not** replicate `dashboard.py`'s own ad hoc `_ingest_batch`/`_second_rolling`/`_live_candles_json` approach (`ml_signals/dashboard.py:1394-1429,1686-1700`), which computes mid-price candles from raw dicts directly and is exactly the kind of parallel/second aggregation implementation AD-F7 forbids repeating here.
  - [ ] Maintain a **small per-`(instrument_id, bar_seconds)` buffer scoped to only the current, not-yet-closed bar interval** (reset at each bar boundary) — not a large historical rolling window like `dashboard.py`'s 3600-entry `_second_rolling` deque, which serves purposes (full historical live-chart fallback) this story doesn't need. On each new tick in the buffer, recompute the forming bar via `candle_dicts_from_snapshots(bucket_snapshots, bar_seconds)` and take its one (last) entry.
  - [ ] **Only maintain a buffer for `(instrument_id, bar_seconds)` pairs with at least one active subscriber** — lazily create on first subscribe, tear down when the last subscriber for that pair disconnects. Computing forming bars for every instrument × every possible bar size regardless of whether any chart page is open would be wasted, unbounded-ish work (MEM-02 spirit: don't accumulate state for what nothing is watching). AD-F2 explicitly anticipates this: "subscription MAY be filtered per-instrument or per-channel... a future per-instrument filter... is compatible with this rule, not foreclosed by it."

- [ ] Task 2 — `/ws/live`: client-driven channel subscription (AC: #2, #3)
  - [ ] **This is new protocol surface, not just a new outbound message type.** Today's `/ws/live` (`data_api/ws/live.py`) is receive-only from the client's perspective — every connected client gets every `rankings:live` message unconditionally (Story 15.2, no filtering). A per-`(instrument_id, bar_seconds)` derived channel must **not** broadcast to every client (only to whoever's chart page currently wants that instrument/bar-size) — the client must tell the server what it wants. Add a minimal inbound message protocol, e.g. `{"subscribe": "candles:{iid}:{bar_seconds}"}` / `{"unsubscribe": "candles:{iid}:{bar_seconds}"}` sent by the client after the socket opens; `rankings:live` keeps flowing unconditionally to every client as today (no behavior change there).
  - [ ] On `unsubscribe` (or socket close), tear down that connection's interest — and, once no connection anywhere wants a given `(instrument_id, bar_seconds)` pair, tear down Task 1's buffer for it.

- [ ] Task 3 — Frontend: live-edge wiring, no stale bars across bar-size/remount (AC: #3, #4, #5)
  - [ ] Extend (or add a sibling to) `frontend/src/hooks/useLiveChannel.ts` to send the Task 2 subscribe/unsubscribe messages keyed by the current `iid`/`bar_seconds`, and to re-subscribe (dropping the old subscription first) whenever either changes.
  - [ ] On receiving a `candles:{iid}:{bar_seconds}` message, update — never append — the candlestick series' current last bar via the library's standard incremental live-update method (`ISeriesApi.update(bar)`, distinct from `setData()`'s full-page-load path Story 15.3 already uses) so the paginated historical bars from `useCandles` are never touched.
  - [ ] On `iid` change (route remount) or `bar_seconds` change, explicitly discard any previously-tracked live bar from local component state **before** the new subscription's first message arrives (AC #4, #5) — this is the exact "stale bar overlapping fresh history" failure mode the ACs call out; verify with a test that changing `iid`/`bar_seconds` clears the tracked live bar synchronously with the change, not only once a new message happens to arrive.

- [ ] Task 4 — Tests (AC: #6)
  - [ ] `data_api/tests/test_live_candles.py` (new): feed a sequence of real `DydxSecondSnapshot` objects (matching `snapshots:raw`'s wire shape) through the Task 1 buffer/forming-bar computation; assert the result at "bar close" matches `candle_dicts_from_snapshots` called directly, once, on the same full set of snapshots (the incremental path must converge to the same answer as the batch path it wraps — this is what actually proves AC #1/#6, not just code-reading).
  - [ ] A case asserting two consecutive ticks within the same bucket update the same forming-bar identity (same `t`), not two separate bars.
  - [ ] Frontend: a test that changing `bar_seconds` (or `iid`) clears the previously-rendered live bar before any new message arrives (mock `lightweight-charts` + the WS client, same boundary Story 15.3/15.4 established).
  - [ ] Run: `cd troll && PYTHONPATH=. python -m pytest data_api/tests -q` and `cd troll/frontend && npm run build && npm run test && npm run lint`, plus the full regression, per prior stories' convention.

## Dev Notes

- **Sequencing:** depends on Story 15.3 (candle aggregation path, `useCandles`, the one chart instance) and Story 15.4 (the chart instance/pane registry this live bar renders onto) actually being implemented first — same sequencing caution as Story 15.4's Dev Notes. As of this story's creation neither has a populated Dev Agent Record yet.
- **Why not reuse `dashboard.py`'s `_live_candles_json`/`_second_rolling`:** that path computes a mid-price-only candle from a large rolling dict buffer, entirely separate from `ml_signals.candles.candle_dicts_from_snapshots` (which uses the snapshot's own `open_price`/`high_price`/`low_price`/`close_price`/`buy_volume`/`sell_volume` fields, populated by the collector's own bar-builder, not a mid-price approximation). AD-F7 requires calling `candle_dicts_from_snapshots` itself, so this story is not a straight port of `_live_candles_json` — it is a new, smaller computation reusing the same aggregation function this epic already standardized on in Story 15.3.
- **This is the first two-way `/ws/live` protocol addition.** Every prior use of `/ws/live` (Story 15.2's `rankings:live` relay) was server-to-client only. Keep the inbound subscribe/unsubscribe message shape minimal (DESIGN-01) — this is not a general pub/sub gateway, just enough to scope the one derived channel this story adds. Document the exact message shape used in code comments (AD-F5's spirit: `/ws/live` frame types are hand-written but must cite their source — here, the shape is this story's own invention, so the comment should say so plainly rather than imply it mirrors an existing Redis wire format).
- **`troll/CLAUDE.md` constraints that apply:** AD-F7/AD-F2 (no parallel aggregation), MEM-02-adjacent (buffers only for actively-watched instrument/bar-size pairs, not every possible combination), DATA-01 (a bar-size/remount transition must never render a stale value as current — AC #4/#5 are this rule applied to the live edge specifically), TEST-03 (real `DydxSecondSnapshot` objects via `from_dict`, never hand-rolled dicts pretending to be one).

### Project Structure Notes

- New: `data_api/live_candles.py` (or equivalent — the `LiveCandleBus`/forming-bar buffer, naming is an implementation detail), `data_api/tests/test_live_candles.py`.
- Modified: `data_api/ws/live.py` (inbound subscribe/unsubscribe handling, relay the new derived channel), `data_api/app.py` (wire the new bus's lifespan startup, same pattern as `redis_bus.bus.run()`), `frontend/src/hooks/useLiveChannel.ts` (or a new hook alongside it), `frontend/src/components/chart/LightweightChart.tsx`/`ChartPage.tsx` (consume the live bar).
- Not modified: `ml_signals/candles.py` (reused unchanged), `ml_signals/dashboard.py` (its own live-candle path stays exactly as today until Story 15.10).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 15.5, lines 1383-1413] — this story's origin; all 6 Given/When/Then blocks map to this file's ACs.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — AD-F7 (the forming bar's one sanctioned live path, full text), AD-F2 (no new computation — reuses `ml_signals.candles`).
- [Source: troll/dydx_collector/collector.py:355-368] — `_publish_snapshot_batch`, confirms `snapshots:raw`'s wire shape (`DydxSecondSnapshot.to_dict(s)` list).
- [Source: troll/dydx_collector/second_snapshot.py:126-148] — `DydxSecondSnapshot.to_dict`/`from_dict`, the exact round-trip this story's Redis-message decoding uses.
- [Source: troll/ml_signals/candles.py:99-127] — `candle_dicts_from_snapshots`, the one aggregation function this story is allowed to call (attribute-access on its input, informs Task 1's `from_dict` requirement).
- [Source: troll/ml_signals/dashboard.py:1394-1429,1686-1700,2280-2312] — `_live_candles_json`/`_ingest_batch`/`_second_rolling`/`_redis_listener`, the existing (not reused) ad hoc live-candle path and the "one subscriber, two channels" precedent this story's own Redis subscription follows structurally, not in content.
- [Source: troll/data_api/redis_bus.py, troll/data_api/ws/live.py] — full files read this session; `RankingsBus`'s shape is the structural template for this story's new bus; today's `/ws/live` has no inbound message handling at all, confirming Task 2 is new protocol surface.
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] — Cross-Story Dependencies: "Story 15.5 (live candle edge) depends on 15.3's candle aggregation path and 15.4's established chart instance."

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
