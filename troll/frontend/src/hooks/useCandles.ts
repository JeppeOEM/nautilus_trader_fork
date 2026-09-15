import type {
  CandlestickData,
  IChartApi,
  LogicalRange,
  Time,
  UTCTimestamp,
  WhitespaceData,
} from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchCandles } from "../api/client";
import type { CandleItem } from "../api/schema";

// Mirrors dashboard.py:206's old defaults (_CANDLE_VISIBLE_BARS=120,
// _CANDLE_REFILL_MARGIN_BARS=20) -- same initial window size and scroll-back trigger
// margin, now served through the cursor-paginated /api/candles contract (AD-F3).
const INITIAL_LIMIT = 120;
const BAR_SECONDS = 60;
const REFILL_MARGIN_BARS = 20;

export type ChartDatum = CandlestickData<Time> | WhitespaceData<Time>;

function toChartDatum(item: CandleItem): ChartDatum {
  const time = (item.t / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  if (item.o == null || item.h == null || item.l == null || item.c == null) {
    // Gap marker (AC #5/AD-F6): all four OHLC fields are null together, never a mix --
    // passed straight through as native whitespace data, never filtered or reshaped.
    return { time };
  }
  return { time, open: item.o, high: item.h, low: item.l, close: item.c };
}

export interface UseCandlesResult {
  candles: ChartDatum[];
}

/**
 * Cursor-paginated candle history (AD-F3): fetches the most recent `INITIAL_LIMIT` bars
 * on mount, then one more page -- via the same `before_ns`/`limit` contract -- each time
 * `chart`'s visible range scrolls within `REFILL_MARGIN_BARS` of the currently-loaded
 * left edge. Stops permanently in that direction once a response reports `has_more:
 * false` (AC #4). Callers key their component by instrument id (`ChartPage.tsx`) so a
 * new instrument always gets a fresh hook instance rather than needing in-hook reset
 * logic.
 */
export function useCandles(instrumentId: string, chart: IChartApi | null): UseCandlesResult {
  const [candles, setCandles] = useState<ChartDatum[]>([]);
  const hasMoreOlderRef = useRef(true);
  const loadingRef = useRef(false);
  const earliestMsRef = useRef<number | null>(null);

  const loadPage = useCallback(
    (beforeNs: number, prepend: boolean): Promise<void> => {
      if (loadingRef.current) return Promise.resolve();
      loadingRef.current = true;
      return fetchCandles(instrumentId, beforeNs, INITIAL_LIMIT, BAR_SECONDS)
        .then((response) => {
          if (response.items.length === 0) {
            hasMoreOlderRef.current = false;
            return;
          }
          earliestMsRef.current = response.items[0].t;
          hasMoreOlderRef.current = response.has_more;
          const mapped = response.items.map(toChartDatum);
          setCandles((prev) => {
            if (!prepend || prev.length === 0) return mapped;
            // A gap can straddle exactly the page boundary (the cursor point) -- each
            // page's own gap-marker insertion (AC #5) only sees gaps inside its own
            // queried range, so this seam needs its own check: if the newest incoming
            // (older) candle and the previously-earliest-loaded candle are more than
            // one bar apart, insert a marker at the seam too (AD-F6 applies just as
            // much to a boundary gap as an in-page one).
            // Every ChartDatum here is built by toChartDatum() above, so .time is
            // always a UTCTimestamp (never lightweight-charts' BusinessDay/string Time
            // variants).
            const newestTime = mapped[mapped.length - 1].time as UTCTimestamp;
            const boundaryTime = prev[0].time as UTCTimestamp;
            if (newestTime + BAR_SECONDS < boundaryTime) {
              const seamMarker: ChartDatum = { time: (newestTime + BAR_SECONDS) as UTCTimestamp };
              return [...mapped, seamMarker, ...prev];
            }
            return [...mapped, ...prev];
          });
        })
        .catch((err: unknown) => {
          // Mirrors useLiveChannel's own malformed-frame handling: log and keep the
          // previous state rather than crash the chart or leave an unhandled
          // rejection -- a failed refill/initial fetch is not fatal, just a page that
          // didn't load.
          console.error(`useCandles: failed to load candles for ${instrumentId}`, err);
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

  return { candles };
}
