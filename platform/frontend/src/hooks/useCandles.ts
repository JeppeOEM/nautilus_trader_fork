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

import { HttpError, fetchCandles } from "../api/client";
import type { CandleItem } from "../api/schema";
import type { LiveBar } from "./useLiveCandle";

// Mirrors dashboard.py:206's old defaults (_CANDLE_VISIBLE_BARS=120,
// _CANDLE_REFILL_MARGIN_BARS=20) -- same initial window size and scroll-back trigger
// margin, now served through the cursor-paginated /api/candles contract (AD-F3).
const INITIAL_LIMIT = 120;
// Exported so useLiveCandle.ts (Story 15.5) can subscribe to the same bar size instead
// of duplicating the literal -- the two paths must never silently drift apart.
export const BAR_SECONDS = 60;
const REFILL_MARGIN_BARS = 20;
// A failed page fetch (backend restarting, SSH tunnel hiccup, proxy 502) is retried
// forever with capped backoff -- the initial page runs once per mount, so without a retry
// one transient failure left the chart permanently blank until a manual reload.
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 10_000;
// Gateway statuses the SSH tunnel / Vite proxy return while data_api restarts -- retried. Any
// other HTTP status is the data API's own deterministic answer (e.g. its DATA-07 500 for an
// impossible candle, already in the error ledger): retrying the same request cannot fix it.
const TRANSIENT_HTTP = new Set([502, 503, 504]);

export type ChartDatum = CandlestickData<Time> | WhitespaceData<Time>;
export type VolumeDatum = HistogramData<Time> | WhitespaceData<Time>;

// Last line of defence behind the backend's `is_valid_candle`: a malformed candle is drawn as
// a gap (and logged), never as a strangely-shaped bar. Exported for tests.
export function isValidOhlc(o: number, h: number, l: number, c: number, v: number | null | undefined): boolean {
  return (
    [o, h, l, c].every(Number.isFinite) &&
    l <= Math.min(o, c) &&
    Math.max(o, c) <= h &&
    (v == null || (Number.isFinite(v) && v >= 0))
  );
}

function toChartDatum(item: CandleItem): ChartDatum {
  const time = (item.t / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  if (item.o == null || item.h == null || item.l == null || item.c == null) {
    // Gap marker (AC #5/AD-F6): all four OHLC fields are null together, never a mix --
    // passed straight through as native whitespace data, never filtered or reshaped.
    return { time };
  }
  if (!isValidOhlc(item.o, item.h, item.l, item.c, item.v)) {
    console.error("useCandles: dropping malformed candle, rendering as a gap", item);
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
  const malformed =
    item.o != null && item.h != null && item.l != null && item.c != null &&
    !isValidOhlc(item.o, item.h, item.l, item.c, item.v);
  return item.v == null || malformed ? { time } : { time, value: item.v };
}

/**
 * Upsert `incoming` into `prev` by time (incoming wins on a clash), ascending. Serves both the
 * newest-page refetch after a socket drop and the promotion of a closed live bar into history,
 * so a later setData() can never wipe bars the chart already showed. A hole between prev's last
 * bar and incoming's first (socket down longer than one page) gets one whitespace seam marker --
 * the same honesty rule the prepend seam applies at the older edge (AD-F6).
 */
export function mergeByTime<T extends { time: Time }>(prev: T[], incoming: T[], barSeconds: number): T[] {
  if (incoming.length === 0) return prev;
  const byTime = new Map<number, T>();
  for (const d of prev) byTime.set(d.time as number, d);
  const prevLast = prev.length > 0 ? (prev[prev.length - 1].time as number) : null;
  const seam = prevLast === null ? null : prevLast + barSeconds;
  if (seam !== null && (incoming[0].time as number) > seam && !byTime.has(seam)) {
    byTime.set(seam, { time: seam as UTCTimestamp } as unknown as T);
  }
  for (const d of incoming) byTime.set(d.time as number, d);
  return [...byTime.values()].sort((a, b) => (a.time as number) - (b.time as number));
}

interface CandlesState {
  candles: ChartDatum[];
  volume: VolumeDatum[];
}

const EMPTY_STATE: CandlesState = { candles: [], volume: [] };

export interface UseCandlesResult {
  candles: ChartDatum[];
  volume: VolumeDatum[];
  /** True while history is not loaded (fetch failed, or no candles yet); see `loadError`. */
  loadFailed: boolean;
  /** `{venue, market}` from the newest candles response (e.g. BYBIT/spot), `null` until loaded. */
  venueMarket: { venue: string; market: string } | null;
  /** Operator-facing reason while `loadFailed`, else `null`. */
  loadError: string | null;
  /** Refetch the newest page and merge it in -- after a `/ws/live` reconnect, the bars that
   * closed while the socket was down exist only on the server. */
  refreshNewest: () => Promise<void>;
  /** Promote a closed live bar into history state. */
  appendBar: (bar: LiveBar) => void;
}

/**
 * Cursor-paginated candle history (AD-F3): fetches the most recent `INITIAL_LIMIT` bars
 * on mount, then one more page -- via the same `before_ns`/`limit` contract -- each time
 * `chart`'s visible range scrolls within `REFILL_MARGIN_BARS` of the currently-loaded
 * left edge. Stops permanently in that direction once a response reports `has_more:
 * false` (AC #4). Callers key their component by instrument id (`ChartPage.tsx`) so a
 * new instrument always gets a fresh hook instance rather than needing in-hook reset
 * logic.
 *
 * `enabled` (Story 15.7, default `true`): while `false` (Lines mode is active), this hook
 * issues no requests at all -- neither the initial-page fetch nor a scroll-back refill --
 * so Candles-mode data isn't fetched behind a pane the operator isn't looking at. Once
 * loaded, `state` is simply left as-is (not cleared) so switching back to Candles mode
 * shows the same data without a redundant refetch; the initial-page fetch itself only
 * ever runs once per mount, whenever `enabled` first becomes `true` (a `hasLoadedInitialRef`
 * guard, not a plain `[enabled]` dependency, so toggling modes back and forth can't
 * re-trigger it and discard any scroll-back history already loaded).
 */
export function useCandles(
  instrumentId: string,
  chart: IChartApi | null,
  enabled = true,
  barSeconds = BAR_SECONDS,
): UseCandlesResult {
  const [state, setState] = useState<CandlesState>(EMPTY_STATE);
  const [venueMarket, setVenueMarket] = useState<UseCandlesResult["venueMarket"]>(null);
  const hasMoreOlderRef = useRef(true);
  const loadingRef = useRef(false);
  const earliestMsRef = useRef<number | null>(null);
  const hasLoadedInitialRef = useRef(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const refreshingRef = useRef(false);
  const unmountedRef = useRef(false);

  const loadPage = useCallback(
    (beforeNs: number, prepend: boolean): Promise<void> => {
      if (loadingRef.current) return Promise.resolve();
      loadingRef.current = true;
      const retry = (message: string): void => {
        // A fetch settling after unmount must not arm a timer nobody will ever clear.
        if (unmountedRef.current) return;
        setLoadError(message);
        attemptRef.current += 1;
        const delay = Math.min(RETRY_BASE_MS * 2 ** (attemptRef.current - 1), RETRY_MAX_MS);
        // The initial page retries from "now", never its frozen cursor: a coin whose first
        // bar lands after the first attempt would otherwise never load.
        const cursor = prepend ? beforeNs : Date.now() * 1_000_000;
        retryTimerRef.current = setTimeout(() => void loadPage(cursor, prepend), delay);
      };
      return fetchCandles(instrumentId, beforeNs, INITIAL_LIMIT, barSeconds)
        .then((response) => {
          attemptRef.current = 0;
          if (response.items.length === 0) {
            if (prepend) {
              hasMoreOlderRef.current = false;
              return;
            }
            // Nothing served yet (data_api just restarted, or a freshly listed coin): not a
            // terminal state -- keep asking, and say so instead of showing an empty chart.
            retry("No candles served yet for this instrument -- retrying...");
            return;
          }
          setLoadError(null);
          setVenueMarket({ venue: response.venue, market: response.market });
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
            if (newestTime + barSeconds < boundaryTime) {
              const seamTime = (newestTime + barSeconds) as UTCTimestamp;
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
          // Log and keep the previous state rather than crash the chart or leave an
          // unhandled rejection (console.error also lands in the ErrorBar, DATA-07).
          console.error(`useCandles: failed to load candles for ${instrumentId}`, err);
          if (err instanceof HttpError && !TRANSIENT_HTTP.has(err.status)) {
            setLoadError(`Data API answered ${err.status} -- history not loaded (see error bar)`);
            return; // deterministic: the same request would fail the same way
          }
          retry("Can't reach the data API -- history not loaded, retrying...");
        })
        .finally(() => {
          loadingRef.current = false;
        });
    },
    [instrumentId, barSeconds],
  );

  const refreshNewest = useCallback((): Promise<void> => {
    if (refreshingRef.current) return Promise.resolve();
    refreshingRef.current = true;
    return fetchCandles(instrumentId, Date.now() * 1_000_000, INITIAL_LIMIT, barSeconds)
      .then((response) => {
        if (response.items.length === 0) return;
        if (earliestMsRef.current === null) {
          // First data this mount has seen: scroll-back must be able to start from it.
          earliestMsRef.current = response.items[0].t;
          hasMoreOlderRef.current = response.has_more;
        }
        setState((prev) => ({
          candles: mergeByTime(prev.candles, response.items.map(toChartDatum), barSeconds),
          volume: mergeByTime(prev.volume, response.items.map(toVolumeDatum), barSeconds),
        }));
      })
      .catch((err: unknown) => {
        // The live socket is back up regardless; the next reconnect (or reload) retries.
        console.error(`useCandles: failed to refresh newest candles for ${instrumentId}`, err);
      })
      .finally(() => {
        refreshingRef.current = false;
      });
  }, [instrumentId, barSeconds]);

  const appendBar = useCallback(
    (bar: LiveBar): void => {
      const { volume, ...candle } = bar;
      setState((prev) => ({
        candles: mergeByTime(prev.candles, [candle], barSeconds),
        volume: mergeByTime(prev.volume, [{ time: bar.time, value: volume }], barSeconds),
      }));
    },
    [barSeconds],
  );

  useEffect(
    () => () => {
      unmountedRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!enabled || hasLoadedInitialRef.current) return;
    hasLoadedInitialRef.current = true;
    void loadPage(Date.now() * 1_000_000, false);
  }, [loadPage, enabled]);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const handler = (range: LogicalRange | null) => {
      if (!enabled) return;
      if (!range || range.from >= REFILL_MARGIN_BARS) return;
      if (!hasMoreOlderRef.current || loadingRef.current || earliestMsRef.current === null) return;
      void loadPage(earliestMsRef.current * 1_000_000, true);
    };
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(handler);
  }, [chart, loadPage, enabled]);

  return { ...state, venueMarket, loadFailed: loadError !== null, loadError, refreshNewest, appendBar };
}
