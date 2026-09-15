import type {
  CandlestickData,
  HistogramData,
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
export type VolumeDatum = HistogramData<Time> | WhitespaceData<Time>;

function toChartDatum(item: CandleItem): ChartDatum {
  const time = (item.t / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  if (item.o == null || item.h == null || item.l == null || item.c == null) {
    // Gap marker (AC #5/AD-F6): all four OHLC fields are null together, never a mix --
    // passed straight through as native whitespace data, never filtered or reshaped.
    return { time };
  }
  return { time, open: item.o, high: item.h, low: item.l, close: item.c };
}

// Story 15.4: the volume pane is derived client-side from this same already-fetched `v`
// field -- no new query (DESIGN-01, this story's spec). `v` is only null on a gap-marker
// item (real candles always carry a volume, possibly 0.0), same nullity contract as the
// OHLC fields above.
function toVolumeDatum(item: CandleItem): VolumeDatum {
  const time = (item.t / 1000) as UTCTimestamp;
  return item.v == null ? { time } : { time, value: item.v };
}

interface CandlesState {
  candles: ChartDatum[];
  volume: VolumeDatum[];
}

const EMPTY_STATE: CandlesState = { candles: [], volume: [] };

export interface UseCandlesResult {
  candles: ChartDatum[];
  volume: VolumeDatum[];
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
  const [state, setState] = useState<CandlesState>(EMPTY_STATE);
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
          const mappedCandles = response.items.map(toChartDatum);
          const mappedVolume = response.items.map(toVolumeDatum);
          setState((prev) => {
            if (!prepend || prev.candles.length === 0) {
              return { candles: mappedCandles, volume: mappedVolume };
            }
            // A gap can straddle exactly the page boundary (the cursor point) -- each
            // page's own gap-marker insertion (AC #5) only sees gaps inside its own
            // queried range, so this seam needs its own check: if the newest incoming
            // (older) candle and the previously-earliest-loaded candle are more than
            // one bar apart, insert a marker at the seam too (AD-F6 applies just as
            // much to a boundary gap as an in-page one).
            // Every ChartDatum here is built by toChartDatum() above, so .time is
            // always a UTCTimestamp (never lightweight-charts' BusinessDay/string Time
            // variants).
            const newestTime = mappedCandles[mappedCandles.length - 1].time as UTCTimestamp;
            const boundaryTime = prev.candles[0].time as UTCTimestamp;
            if (newestTime + BAR_SECONDS < boundaryTime) {
              const seamTime = (newestTime + BAR_SECONDS) as UTCTimestamp;
              return {
                candles: [...mappedCandles, { time: seamTime }, ...prev.candles],
                volume: [...mappedVolume, { time: seamTime }, ...prev.volume],
              };
            }
            return {
              candles: [...mappedCandles, ...prev.candles],
              volume: [...mappedVolume, ...prev.volume],
            };
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

  return state;
}
