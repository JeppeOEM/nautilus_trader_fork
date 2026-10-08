import type { IChartApi } from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  HttpError,
  fetchFunding,
  fetchLiquidationBars,
  fetchMarkIndex,
  fetchOpenInterest,
} from "../api/client";
import type { FundingItem, LiquidationBarItem, MarkIndexItem, OpenInterestItem } from "../api/schema";
import { mergeNewest, prependPage } from "../lib/derivativeSeries";

// Story 33.5: one paginated reader for the derivatives panes, over Story 33.4's routes. Bucketed pages
// (open interest, mark/index, liquidation bars: `t` the bucket start in ms, gap rows `{t}`) and the
// funding event page (`t` = `ts_event` in ns, change-deduped, no gap rows) share the cursor contract
// of `/api/candles`.

export type DerivativeKind = "open-interest" | "mark-index" | "liquidation-bars" | "funding";

export interface DerivativeRows {
  "open-interest": OpenInterestItem;
  "mark-index": MarkIndexItem;
  "liquidation-bars": LiquidationBarItem;
  funding: FundingItem;
}

interface Page<T> {
  items: T[];
  has_more: boolean;
}

/** The initial and newest-page size: the candles' `INITIAL_LIMIT`. */
export const DERIVATIVE_PAGE_LIMIT = 120;
// An older funding page asks for the route's maximum: funding is an event series (Hyperliquid's can
// change every few seconds), so a 120-event page may cover only minutes of a 2-hour candle window.
// Known limit: an hour of second-by-second changes still takes several pages to reach the candles'
// left edge. Upgrade path: a bucketed funding route (the last event per bucket) like open interest's.
const OLDER_EVENT_LIMIT = 500;
// The retry policy of `useCandles` (and `useFootprint`): a gateway status or a network failure is
// retried with capped backoff; any other status is the data API's deterministic answer (its DATA-07
// 500 for a failed archive read included), so asking again cannot fix it.
const RETRY_BASE_MS = 1000;
// The archive lags the live feed by up to one flush (`CoreConfig.flush_interval_seconds`, 60 s), so a
// re-read right at a bar close can miss that bar's last values. While a route is drawn its newest page
// is re-read every `NEWEST_POLL_MS`, and once more `ARCHIVE_LAG_MS` after each bar close (the flush
// interval plus a margin for the write and the store apply).
// Known limit: a flush slower than `ARCHIVE_LAG_MS` (a stalled collector) leaves the closed bar on its
// live value (or whitespace) until the next poll. Upgrade path: the live channel announces each
// flushed bucket, and the page re-reads on that signal instead of on a timer.
export const NEWEST_POLL_MS = 60_000;
export const ARCHIVE_LAG_MS = 75_000;
const RETRY_MAX_MS = 10_000;
const TRANSIENT_HTTP = new Set([502, 503, 504]);

type Fetcher<T> = (instrumentId: string, beforeNs: number, limit: number, barSeconds: number) => Promise<Page<T>>;

const FETCHERS: { [K in DerivativeKind]: Fetcher<DerivativeRows[K]> } = {
  "open-interest": fetchOpenInterest,
  "mark-index": fetchMarkIndex,
  "liquidation-bars": fetchLiquidationBars,
  funding: (instrumentId, beforeNs, limit) => fetchFunding(instrumentId, beforeNs, limit),
};

/** A row's time in chart seconds (bucketed pages carry ms, the funding page ns). */
export function rowSeconds(kind: DerivativeKind, t: number): number {
  return kind === "funding" ? t / 1_000_000_000 : t / 1000;
}

function cursorNs(kind: DerivativeKind, t: number): number {
  return kind === "funding" ? t : t * 1_000_000;
}

export interface UseDerivativePagesResult<T> {
  /** Every loaded row, oldest first (bucketed pages with their gap rows and seam slots). */
  rows: T[];
  /** True once the first page answered (an empty page included): an empty answer is "no data". */
  loaded: boolean;
  /** Operator-facing reason the rows could not be loaded, else null. */
  error: string | null;
  /** Re-read the newest page and merge it by `t` (the route wins): on a reconnect. */
  refreshNewest: () => void;
  /** A candle closed: re-read the newest page now and once more `ARCHIVE_LAG_MS` later, when the
   * archive holds the closed bar. */
  refreshAfterClose: () => void;
}

type PageKind = "initial" | "older" | "newest";

function clearAll(timers: Set<ReturnType<typeof setTimeout>>): void {
  for (const timer of timers) clearTimeout(timer);
  timers.clear();
}

/**
 * Pages one derivatives route for the chart. While `enabled` is false it issues no request (no initial
 * page, no older page, no pending retry) and keeps what it loaded. The newest page comes first; older
 * pages follow while the earliest loaded row is later than `earliestCandleTime` (chart seconds, the
 * candles' own cursor), re-checked when the candles page back or the visible range moves, so the rows
 * never reach further back than the candles (MEM-01). Between two pages of a bucketed route the seam
 * gets `gapRun` slots; funding pages are deduplicated by `t` (audit D-170: a rounded cursor re-serves
 * the oldest tie group). A timeframe or instrument change remounts the chart, and this hook with it.
 */
export function useDerivativePages<K extends DerivativeKind>(
  kind: K,
  instrumentId: string,
  barSeconds: number,
  enabled: boolean,
  chart: IChartApi | null,
  earliestCandleTime: number | null,
): UseDerivativePagesResult<DerivativeRows[K]> {
  const [rows, setRows] = useState<DerivativeRows[K][]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef(rows);
  const busyRef = useRef(false);
  const hasMoreRef = useRef(true);
  const startedRef = useRef(false);
  const loadedRef = useRef(false);
  const enabledRef = useRef(enabled);
  const earliestCandleRef = useRef(earliestCandleTime);
  const attemptRef = useRef(0);
  const loggedRef = useRef(false);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  const loadRef = useRef<(beforeNs: number, page: PageKind) => void>(() => {});
  const loadOlderRef = useRef<() => void>(() => {});
  // A newest re-read asked for while another page was in flight: run once that page settles.
  const pendingNewestRef = useRef(false);
  const newestRetryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One timer per bar close: on a 1m chart the next close comes before a close's `ARCHIVE_LAG_MS`
  // re-read, so a close must never cancel the previous close's.
  const afterCloseRef = useRef(new Set<ReturnType<typeof setTimeout>>());
  const stepMs = kind === "funding" ? undefined : barSeconds * 1000;

  const accept = useCallback(
    (page: PageKind, items: DerivativeRows[K][], hasMore: boolean): void => {
      const held = rowsRef.current;
      if (page === "older") hasMoreRef.current = items.length > 0 && hasMore;
      if (page === "initial") hasMoreRef.current = hasMore;
      const next =
        page === "older" ? prependPage(held, items, stepMs) : page === "initial" ? mergeNewest([], items, stepMs) : mergeNewest(held, items, stepMs);
      rowsRef.current = next;
      loadedRef.current = true;
      setRows(next);
      setLoaded(true);
    },
    [stepMs],
  );

  const fail = useCallback(
    (err: unknown, beforeNs: number, page: PageKind): void => {
      // Logged once per failing streak (the ErrorBar shows it), not once per retry.
      if (!loggedRef.current) console.error(`useDerivativePages: failed to load ${kind} of ${instrumentId}`, err);
      loggedRef.current = true;
      // A pane already holding rows keeps them: a failed re-read is logged (above), never a blank pane.
      const holding = rowsRef.current.length > 0;
      if (err instanceof HttpError && !TRANSIENT_HTTP.has(err.status)) {
        if (!holding) setError("load failed");
        if (page === "older") hasMoreRef.current = false;
        if (page === "initial") startedRef.current = false; // asked again on the next enable
        return;
      }
      if (!holding) setError("load failed, retrying...");
      if (!enabledRef.current) {
        if (page === "initial") startedRef.current = false;
        return;
      }
      attemptRef.current += 1;
      const delay = Math.min(RETRY_BASE_MS * 2 ** (attemptRef.current - 1), RETRY_MAX_MS);
      // The initial and newest pages retry from "now", an older page from its own cursor; a newest
      // retry has its own timer, so it never replaces an older page's pending one.
      const cursor = page === "older" ? beforeNs : Date.now() * 1_000_000;
      const timerRef = page === "newest" ? newestRetryRef : retryTimerRef;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        loadRef.current(page === "older" ? cursor : Date.now() * 1_000_000, page);
      }, delay);
    },
    [kind, instrumentId],
  );

  const load = useCallback(
    (beforeNs: number, page: PageKind): void => {
      if (busyRef.current) {
        if (page === "newest") pendingNewestRef.current = true;
        return;
      }
      busyRef.current = true;
      const limit = page === "older" && kind === "funding" ? OLDER_EVENT_LIMIT : DERIVATIVE_PAGE_LIMIT;
      const fetcher: Fetcher<DerivativeRows[K]> = FETCHERS[kind];
      fetcher(instrumentId, beforeNs, limit, barSeconds)
        .then((response) => {
          if (unmountedRef.current) return;
          attemptRef.current = 0;
          loggedRef.current = false;
          setError(null);
          accept(page, response.items, response.has_more);
        })
        .catch((err: unknown) => {
          if (!unmountedRef.current) fail(err, beforeNs, page);
        })
        .finally(() => {
          busyRef.current = false;
          if (unmountedRef.current) return;
          if (pendingNewestRef.current && enabledRef.current) {
            pendingNewestRef.current = false;
            loadRef.current(Date.now() * 1_000_000, "newest");
            return;
          }
          // The candles may have paged back while this page was in flight (found us busy): look again.
          loadOlderRef.current();
        });
    },
    [kind, instrumentId, barSeconds, accept, fail],
  );

  /** One older page, when the earliest row is still later than the earliest loaded candle. */
  const loadOlder = useCallback((): void => {
    const earliest = rowsRef.current[0];
    const candle = earliestCandleRef.current;
    if (!enabledRef.current || !loadedRef.current || busyRef.current) return;
    if (!hasMoreRef.current || earliest === undefined || candle === null) return;
    if (rowSeconds(kind, earliest.t) <= candle) return;
    load(cursorNs(kind, earliest.t), "older");
  }, [kind, load]);

  const refreshNewest = useCallback((): void => {
    if (!enabledRef.current || !loadedRef.current) return;
    load(Date.now() * 1_000_000, "newest");
  }, [load]);

  const refreshAfterClose = useCallback((): void => {
    if (!enabledRef.current || !loadedRef.current) return;
    refreshNewest();
    const timers = afterCloseRef.current;
    const timer = setTimeout(() => {
      timers.delete(timer);
      refreshNewest();
    }, ARCHIVE_LAG_MS);
    timers.add(timer);
  }, [refreshNewest]);

  useEffect(() => {
    enabledRef.current = enabled;
    earliestCandleRef.current = earliestCandleTime;
    loadRef.current = load;
    loadOlderRef.current = loadOlder;
  }, [enabled, earliestCandleTime, load, loadOlder]);

  useEffect(() => {
    if (!enabled) return;
    const afterClose = afterCloseRef.current;
    if (!startedRef.current) {
      startedRef.current = true;
      load(Date.now() * 1_000_000, "initial");
    } else if (loadedRef.current) {
      load(Date.now() * 1_000_000, "newest"); // re-enabled: the rows held may be stale
    }
    const poll = setInterval(() => {
      if (loadedRef.current) loadRef.current(Date.now() * 1_000_000, "newest");
    }, NEWEST_POLL_MS);
    return () => {
      // Off means no request at all, a pending retry or re-read included.
      clearInterval(poll);
      for (const timer of [retryTimerRef, newestRetryRef]) {
        if (timer.current) clearTimeout(timer.current);
        timer.current = null;
      }
      clearAll(afterClose);
      pendingNewestRef.current = false;
      if (!loadedRef.current) startedRef.current = false;
    };
  }, [enabled, load]);

  // The candles paged back, or our first page landed: one older page if ours still ends later.
  useEffect(() => {
    if (enabled && loaded) loadOlder();
  }, [enabled, loaded, earliestCandleTime, loadOlder]);

  useEffect(() => {
    if (!chart || !enabled) return;
    const timeScale = chart.timeScale();
    const handler = (): void => loadOlder();
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(handler);
  }, [chart, enabled, loadOlder]);

  useEffect(() => {
    unmountedRef.current = false;
    const afterClose = afterCloseRef.current;
    return () => {
      unmountedRef.current = true;
      for (const timer of [retryTimerRef, newestRetryRef]) if (timer.current) clearTimeout(timer.current);
      clearAll(afterClose);
    };
  }, []);

  return { rows, loaded, error, refreshNewest, refreshAfterClose };
}
