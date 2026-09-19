# Story 21.3: Live candle seeding and volume

Status: done

Split from the original 21.1 draft (2026-09-19 incident: odd candles, wrong volume, slow charts, vite `EPIPE`/`ECONNRESET`). Read `troll/docs/DATA_INTEGRITY_AUDIT.md` first.

## Story

As the dashboard operator,
I want the live forming candle to carry the true open/high/low/volume of its whole bucket,
so that the newest candle and its volume bar never jump or diverge from history.

## Acceptance Criteria

1. **Given** a `LiveCandleBus` subscribe **When** the first tick arrives mid-bucket **Then** the forming bar covers the entire bucket so far: the buffer is seeded from the catalog (one bucket, bounded) before the first publish; test mid-bucket subscribe.
2. **Given** the frontend **Then** the client merge block in `LightweightChart.tsx` (`dataRef.current.find`) is deleted, and `bar.v` flows through `toLiveBar`/`useLiveCandle` to the volume series `update`.
3. **Given** bar-size change, remount, or WS reconnect **Then** no stale/overlapping live bar remains and out-of-order frames are ignored (tests).
4. Applying a tick is O(1) on the frontend.

## Tasks / Subtasks

- [ ] Task 1: seed buffer in `data_api/live_candles.py` + tests
- [ ] Task 2: delete merge; carry volume; frontend tests

## Dev Notes

- Production actions (`make redeploy-all`, `repair_catalog --apply`) are operator-approved, never run by the dev agent.

### References

- [Source: troll/docs/DATA_INTEGRITY_AUDIT.md] register D-01…D-23
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] chart invariants (AD-F2 one aggregation path, AD-F3 cursor contract, AD-F6 gap markers, AD-F7 live edge)
- Binding rules `troll/CLAUDE.md`: DATA-01/02/05, MEM-01/02, TEST-01/03/04, NAUT-01, FORK-01

## Dev Agent Record

### Agent Model Used

### Completion Notes List

Review fixes (independent reviewer): seed no longer prepends the previous bucket after a rollover race; a failed seed read can be retried; seed uses `query_second_ohlc`; volume pane repaints when its pane is re-created. Residual: seconds between the catalog's last flush and the first live tick are in neither source, so the forming bar can understate a few seconds.

`LiveCandleBus.seed()` (catalog, one bucket, off-loop, once per pair, ≤1h buckets) wired in `ws/live.py`; client merge deleted; `LiveBar.volume` drives the volume pane via `series.update`. If the volume pane is added after the first tick it shows the next tick's volume (not the held one).

### File List
