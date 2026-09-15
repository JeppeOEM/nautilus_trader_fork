import type { IChartApi, LineData, LogicalRange, Time, UTCTimestamp, WhitespaceData } from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchIndicatorSeries } from "../api/client";
import type { IndicatorSeriesPoint } from "../api/schema";

// Mirrors useCandles.ts's own constants exactly -- co-paging (AD-F3/Story 15.4 AC #5)
// depends on both hooks using the identical INITIAL_LIMIT/BAR_SECONDS/REFILL_MARGIN_BARS
// tuple, not just a similarly-shaped one.
const INITIAL_LIMIT = 120;
const BAR_SECONDS = 60;
const REFILL_MARGIN_BARS = 20;

export type IndicatorDatum = LineData<Time> | WhitespaceData<Time>;

export interface UseIndicatorSeriesResult {
  ofi: IndicatorDatum[];
  obi: IndicatorDatum[];
  microprice: IndicatorDatum[];
  spread: IndicatorDatum[];
}

const EMPTY_RESULT: UseIndicatorSeriesResult = { ofi: [], obi: [], microprice: [], spread: [] };

function toDatum(timeMs: number, value: number | null | undefined): IndicatorDatum {
  const time = (timeMs / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  // A `null` metric (gap marker, or a thin/empty book for microprice/spread, or OFI's
  // pre-initialization bucket) is passed straight through as native whitespace data --
  // never filtered or reshaped (same discipline as useCandles' toChartDatum).
  return value == null ? { time } : { time, value };
}

function toResult(items: IndicatorSeriesPoint[]): UseIndicatorSeriesResult {
  return {
    ofi: items.map((i) => toDatum(i.t, i.ofi)),
    obi: items.map((i) => toDatum(i.t, i.obi)),
    microprice: items.map((i) => toDatum(i.t, i.microprice)),
    spread: items.map((i) => toDatum(i.t, i.spread)),
  };
}

/**
 * Cursor-paginated OFI/OBI/microprice/spread history (AD-F3) -- mirrors `useCandles.ts`'s
 * own plain-hook shape (own `loadingRef`/`hasMoreOlderRef`, same `before_ns`/`limit`/
 * `bar_seconds` page contract), independently subscribed to the same
 * `chart.timeScale().subscribeVisibleLogicalRangeChange` event `useCandles` already wires
 * with the identical margin/constants -- a deliberate one-trigger-drives-both design, not
 * two independently-timed fetches (Dev Notes). Callers key their component by instrument
 * id (`ChartPage.tsx`), same as `useCandles`, so a new instrument always gets a fresh hook
 * instance rather than needing in-hook reset logic.
 */
export function useIndicatorSeries(
  instrumentId: string,
  chart: IChartApi | null,
): UseIndicatorSeriesResult {
  const [result, setResult] = useState<UseIndicatorSeriesResult>(EMPTY_RESULT);
  const hasMoreOlderRef = useRef(true);
  const loadingRef = useRef(false);
  const earliestMsRef = useRef<number | null>(null);

  const loadPage = useCallback(
    (beforeNs: number, prepend: boolean): Promise<void> => {
      if (loadingRef.current) return Promise.resolve();
      loadingRef.current = true;
      return fetchIndicatorSeries(instrumentId, beforeNs, INITIAL_LIMIT, BAR_SECONDS)
        .then((response) => {
          if (response.items.length === 0) {
            hasMoreOlderRef.current = false;
            return;
          }
          earliestMsRef.current = response.items[0].t;
          hasMoreOlderRef.current = response.has_more;
          const mapped = toResult(response.items);
          setResult((prev) => {
            if (!prepend || prev.ofi.length === 0) return mapped;
            // Same page-boundary seam-gap check as useCandles' loadPage -- a gap can
            // straddle exactly the page cursor, which each page's own gap-marker
            // insertion can't see (it only looks inside its own queried range).
            const newestTime = mapped.ofi[mapped.ofi.length - 1].time as UTCTimestamp;
            const boundaryTime = prev.ofi[0].time as UTCTimestamp;
            const seamGap = newestTime + BAR_SECONDS < boundaryTime;
            const seamTime = (newestTime + BAR_SECONDS) as UTCTimestamp;
            const combine = (a: IndicatorDatum[], b: IndicatorDatum[]): IndicatorDatum[] =>
              seamGap ? [...a, { time: seamTime }, ...b] : [...a, ...b];
            return {
              ofi: combine(mapped.ofi, prev.ofi),
              obi: combine(mapped.obi, prev.obi),
              microprice: combine(mapped.microprice, prev.microprice),
              spread: combine(mapped.spread, prev.spread),
            };
          });
        })
        .catch((err: unknown) => {
          console.error(`useIndicatorSeries: failed to load indicator series for ${instrumentId}`, err);
        })
        .finally(() => {
          loadingRef.current = false;
        });
    },
    [instrumentId],
  );

  useEffect(() => {
    void loadPage(Date.now() * 1_000_000, false);
  }, [loadPage]);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const handler = (range: LogicalRange | null) => {
      if (!range || range.from >= REFILL_MARGIN_BARS) return;
      if (!hasMoreOlderRef.current || loadingRef.current || earliestMsRef.current === null) return;
      void loadPage(earliestMsRef.current * 1_000_000, true);
    };
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(handler);
  }, [chart, loadPage]);

  return result;
}
