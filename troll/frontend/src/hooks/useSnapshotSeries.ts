import type { IChartApi, LineData, LogicalRange, Time, UTCTimestamp, WhitespaceData } from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchSnapshotSeries } from "../api/client";
import type { SnapshotSeriesPoint } from "../api/schema";

// ~15 minutes of 1-second rows -- approximates `dashboard.py`'s own Lines-mode chunk width
// (`_chunkSpanMs`'s 15-minute/900-row default, `ml_signals/dashboard.py:335`). Snapshot
// rows have no bar-count concept, so this is sized in rows-per-second terms, not reused
// from `useCandles.ts`'s bar-count constants (120/20), which don't apply here.
const INITIAL_LIMIT = 900;
const REFILL_MARGIN_ROWS = 150;

// A visible right edge within this many seconds of "now" is treated as the live edge
// (Task 3) -- anchor the very first Lines-mode fetch off `Date.now()` instead of a
// slightly-stale `getVisibleRange()` reading in that case, same as `dashboard.py`'s own
// `setCoinMode` treating "no explicit historical range" as "fetch the live window".
const LIVE_EDGE_THRESHOLD_SECONDS = 30;

// Matches the backend's `_SNAPSHOT_GAP_THRESHOLD_MS` (data_api/routes/snapshots.py)
// exactly, in seconds -- the page-boundary seam check below must use the same threshold
// the backend's own gap-marker insertion inside a single page uses, or a normal ~1-2.4s
// inter-snapshot spacing that happens to straddle a page cursor would render a false gap
// that the same spacing mid-page never would (AC #4 requires identical discipline).
const SEAM_GAP_THRESHOLD_SECONDS = 2.5;

export type LineDatum = LineData<Time> | WhitespaceData<Time>;

export interface SnapshotLinesData {
  bid: LineDatum[];
  ask: LineDatum[];
  mid: LineDatum[];
  micro: LineDatum[];
  price: LineDatum[];
}

const EMPTY_LINES: SnapshotLinesData = { bid: [], ask: [], mid: [], micro: [], price: [] };

function toDatum(timeMs: number, value: number | null | undefined): LineDatum {
  const time = (timeMs / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  // A `null` value (gap marker, AC #4/AD-F6) is passed straight through as native
  // whitespace data -- never filtered or reshaped, same discipline as useCandles/
  // useIndicatorSeries' own toChartDatum/toDatum.
  return value == null ? { time } : { time, value };
}

function toLines(items: SnapshotSeriesPoint[]): SnapshotLinesData {
  return {
    bid: items.map((i) => toDatum(i.t, i.bid)),
    ask: items.map((i) => toDatum(i.t, i.ask)),
    mid: items.map((i) => toDatum(i.t, i.mid)),
    micro: items.map((i) => toDatum(i.t, i.micro)),
    price: items.map((i) => toDatum(i.t, i.price)),
  };
}

/**
 * Cursor-paginated bid/ask/mid/micro/price history (AD-F3) for Lines mode -- mirrors
 * `useCandles.ts`'s own plain-hook shape (own `loadingRef`/`hasMoreOlderRef`, same
 * `before_ns`/`limit` page contract, no `bar_seconds`) and, like `useCandles`, is gated by
 * an `enabled` flag rather than only ever mounting while active: `ChartInner` keeps this
 * hook (and `useCandles`) alive across a mode toggle so switching back and forth doesn't
 * lose already-loaded history, and only ever fetches while its own mode is the active one.
 *
 * Both hooks independently subscribe to the same `chart.timeScale().subscribeVisibleLogicalRangeChange`
 * event -- the identical "single event source, multiple gated subscribers" pattern Story
 * 15.4's `useIndicatorSeries` already established alongside `useCandles`, extended here to
 * a third, mutually-exclusive data source rather than a new, parallel trigger mechanism.
 */
export function useSnapshotSeries(
  instrumentId: string,
  chart: IChartApi | null,
  enabled: boolean,
): SnapshotLinesData {
  const [lines, setLines] = useState<SnapshotLinesData>(EMPTY_LINES);
  const hasMoreOlderRef = useRef(true);
  const loadingRef = useRef(false);
  const earliestMsRef = useRef<number | null>(null);
  const hasLoadedInitialRef = useRef(false);

  const loadPage = useCallback(
    (beforeNs: number, limit: number, prepend: boolean): Promise<void> => {
      if (loadingRef.current) return Promise.resolve();
      loadingRef.current = true;
      return fetchSnapshotSeries(instrumentId, beforeNs, limit)
        .then((response) => {
          if (response.items.length === 0) {
            hasMoreOlderRef.current = false;
            return;
          }
          earliestMsRef.current = response.items[0].t;
          hasMoreOlderRef.current = response.has_more;
          const mapped = toLines(response.items);
          setLines((prev) => {
            if (!prepend || prev.bid.length === 0) return mapped;
            // Same page-boundary seam-gap check useCandles'/useIndicatorSeries' loadPage
            // already apply: a gap can straddle exactly the page cursor, which each
            // page's own gap-marker insertion (inside `_price_series_rows`) can't see --
            // it only looks inside its own queried range.
            const newestTime = mapped.bid[mapped.bid.length - 1].time as UTCTimestamp;
            const boundaryTime = prev.bid[0].time as UTCTimestamp;
            const seamGap = newestTime + SEAM_GAP_THRESHOLD_SECONDS < boundaryTime;
            const seamTime = (newestTime + 1) as UTCTimestamp;
            const combine = (a: LineDatum[], b: LineDatum[]): LineDatum[] =>
              seamGap ? [...a, { time: seamTime }, ...b] : [...a, ...b];
            return {
              bid: combine(mapped.bid, prev.bid),
              ask: combine(mapped.ask, prev.ask),
              mid: combine(mapped.mid, prev.mid),
              micro: combine(mapped.micro, prev.micro),
              price: combine(mapped.price, prev.price),
            };
          });
        })
        .catch((err: unknown) => {
          console.error(`useSnapshotSeries: failed to load snapshot series for ${instrumentId}`, err);
        })
        .finally(() => {
          loadingRef.current = false;
        });
    },
    [instrumentId],
  );

  useEffect(() => {
    if (!enabled || hasLoadedInitialRef.current) return;
    hasLoadedInitialRef.current = true;

    // Anchor the very first fetch off the chart's currently-visible range (Task 3) so the
    // newly-shown line series actually covers the window the operator was already looking
    // at, rather than a fresh default window that merely doesn't reset the axis labels.
    const visibleRange = chart?.timeScale().getVisibleRange() ?? null;
    const nowNs = Date.now() * 1_000_000;
    let beforeNs = nowNs;
    let limit = INITIAL_LIMIT;
    if (visibleRange) {
      const rightEdgeSeconds = visibleRange.to as number;
      const isLiveEdge = nowNs / 1_000_000_000 - rightEdgeSeconds < LIVE_EDGE_THRESHOLD_SECONDS;
      if (!isLiveEdge) {
        beforeNs = rightEdgeSeconds * 1_000_000_000;
        const spanSeconds = rightEdgeSeconds - (visibleRange.from as number);
        limit = Math.max(INITIAL_LIMIT, Math.ceil(spanSeconds));
      }
    }
    void loadPage(beforeNs, limit, false);
  }, [enabled, chart, loadPage]);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const handler = (range: LogicalRange | null) => {
      if (!enabled) return;
      if (!range || range.from >= REFILL_MARGIN_ROWS) return;
      if (!hasMoreOlderRef.current || loadingRef.current || earliestMsRef.current === null) return;
      void loadPage(earliestMsRef.current * 1_000_000, INITIAL_LIMIT, true);
    };
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(handler);
  }, [chart, loadPage, enabled]);

  return lines;
}
