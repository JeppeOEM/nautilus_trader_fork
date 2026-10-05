import type { IChartApi, UTCTimestamp } from "lightweight-charts";
import { useCallback, useEffect, useRef, useState } from "react";

import { HttpError, fetchFootprint } from "../api/client";
import type { FootprintItem } from "../api/schema";
import type { InstrumentPrecision } from "../lib/drawings";
import { mergeFootprintPages } from "../lib/footprint";

// Story 32.8: the footprint's bars come in pages of the candles' own size and cursor contract.
const PAGE_LIMIT = 120;
// The newest-page refresh asks for this many bars only: the server ends every page at its settle
// cutoff, so these are the newest servable bars, and a refresh that no longer overlaps what is
// held (a long absence) is caught up by the hole fill, page by page.
const REFRESH_LIMIT = 10;
// Counted in logical slots from the footprint's own earliest bar, the candles' refill margin.
const REFILL_MARGIN_BARS = 20;
// The newest page is refetched this often while the footprint is on: a bar is served only once it
// closed and settled server-side (`FOOTPRINT_SETTLE_SECONDS`, 420 s), so new bars appear by polling.
// A tick asks only once the bar after the newest held one can have closed (`refreshDue`), so a
// 1D/1W chart does not re-read days of trades every minute for a bar that cannot exist yet.
// Known limit (historical only): the forming bar and the last ~7 minutes never show a footprint,
// and a closed bar appears up to one refresh after it settled. Upgrade path: fold the `trades`
// Redis stream in the candles context and push closed bars' footprints like the live candle.
export const FOOTPRINT_REFRESH_MS = 60_000;
// The retry discipline of `useCandles`: a gateway status or a network failure is retried with
// capped backoff, any other HTTP status is the data API's deterministic answer and is not.
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 10_000;
const TRANSIENT_HTTP = new Set([502, 503, 504]);

// "fill": a page back from a non-overlapping newest page towards the newest bar held before it.
type PageKind = "initial" | "older" | "newest" | "fill";

interface Cursor {
  /** The oldest bar loaded (ms), the next older page's cursor; null until a page had bars. */
  earliestMs: number | null;
  /** The newest bar loaded (ms); a newest page starting after it left a hole to fill. */
  latestMs: number | null;
  /** While filling a hole: its older edge (the newest bar held before it), else null. */
  fillToMs: number | null;
  /** While filling a hole: the oldest bar the fill reached so far, the next fill page's cursor. */
  earliestFillMs: number | null;
  hasMore: boolean;
  busy: boolean;
  /** Whether the initial page was requested for the current row size. */
  started: boolean;
}

const freshCursor = (): Cursor => ({
  earliestMs: null,
  latestMs: null,
  fillToMs: null,
  earliestFillMs: null,
  hasMore: true,
  busy: false,
  started: false,
});

export interface UseFootprintResult {
  /** The loaded bars of the current row size, oldest first. */
  items: FootprintItem[];
  /** The definition's precisions the integers are in, from the latest response; null until one. */
  precision: InstrumentPrecision | null;
  /** Operator-facing reason the footprint could not be loaded, else null. */
  error: string | null;
}

/**
 * The volume footprint of the chart's closed bars (`GET /api/coin/{iid}/footprint`), paginated like
 * `useCandles`: the newest page on enable, one older page each time the visible range comes within
 * `REFILL_MARGIN_BARS` slots of the footprint's own earliest loaded bar, and the newest page again
 * every `FOOTPRINT_REFRESH_MS`. While `enabled` is false it issues no request at all (no initial
 * page, no refill, no refresh, no pending retry) and keeps what it loaded. A `rowTicks` change (0 =
 * auto) drops every page and starts over; nothing else refetches. A timeframe change remounts the
 * chart (and this hook) through `ChartInner`'s key.
 */
export function useFootprint(
  instrumentId: string,
  chart: IChartApi | null,
  barSeconds: number,
  enabled: boolean,
  rowTicks: number,
): UseFootprintResult {
  const [pages, setPages] = useState<{
    rowTicks: number;
    items: FootprintItem[];
  }>({ rowTicks, items: [] });
  const [precision, setPrecision] = useState<InstrumentPrecision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cursorRef = useRef<Cursor>(freshCursor());
  // Bumped on every row-size change: an answer for an older generation is discarded.
  const generationRef = useRef(0);
  const rowTicksRef = useRef(rowTicks);
  const enabledRef = useRef(enabled);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attemptRef = useRef(0);
  const unmountedRef = useRef(false);
  // The precisions the held pages are in: a response in others (a definition changed) replaces them.
  const precisionRef = useRef<InstrumentPrecision | null>(null);
  // The latest `load` / `loadOlder`, for the retry timer and a settled page to call without the
  // callbacks capturing themselves.
  const loadRef = useRef<(beforeNs: number, kind: PageKind) => void>(() => {});
  const loadOlderRef = useRef<() => void>(() => {});

  const accept = useCallback(
    (kind: PageKind, items: FootprintItem[], hasMore: boolean, replace: boolean): void => {
      const cursor = cursorRef.current;
      if (kind === "older") {
        cursor.hasMore = items.length > 0 && hasMore;
        if (items.length > 0) cursor.earliestMs = items[0].t;
      } else if (cursor.earliestMs === null && items.length > 0) {
        cursor.earliestMs = items[0].t;
        cursor.hasMore = hasMore;
      }
      trackHole(cursor, kind, items, hasMore, barSeconds * 1000);
      if (items.length === 0 && !replace) return;
      setPages((prev) => ({
        rowTicks,
        items: mergeFootprintPages(prev.rowTicks === rowTicks && !replace ? prev.items : [], items),
      }));
    },
    [rowTicks, barSeconds],
  );

  const load = useCallback(
    (beforeNs: number, kind: PageKind): void => {
      const cursor = cursorRef.current;
      if (cursor.busy) return;
      cursor.busy = true;
      const generation = generationRef.current;
      let succeeded = false;
      const retry = (message: string): void => {
        setError(message);
        // Switched off while the request was in flight: no retry, and the initial page is asked
        // for again on the next enable.
        if (!enabledRef.current) {
          if (cursorRef.current.earliestMs === null) cursorRef.current.started = false;
          return;
        }
        attemptRef.current += 1;
        const delay = Math.min(RETRY_BASE_MS * 2 ** (attemptRef.current - 1), RETRY_MAX_MS);
        // The initial page retries from "now", never its frozen cursor; older and fill pages keep theirs.
        const cursorNs = kind === "older" || kind === "fill" ? beforeNs : Date.now() * 1_000_000;
        retryTimerRef.current = setTimeout(() => {
          retryTimerRef.current = null;
          if (superseded(cursorRef.current, kind, cursorNs)) return;
          loadRef.current(cursorNs, kind);
        }, delay);
      };
      fetchFootprint(instrumentId, beforeNs, kind === "newest" ? REFRESH_LIMIT : PAGE_LIMIT, barSeconds, rowTicks)
        .then((response) => {
          if (unmountedRef.current || generation !== generationRef.current) return;
          attemptRef.current = 0;
          succeeded = true;
          setError(null);
          const held = precisionRef.current;
          const next = { price: response.price_precision, size: response.size_precision };
          // The held bars' integers are in the old precisions: drawn at the new ones they would be
          // wrong prices and sizes, so they are dropped and the pages start over from this one.
          const replace = held !== null && (held.price !== next.price || held.size !== next.size);
          if (replace) Object.assign(cursor, freshCursor(), { busy: true, started: true });
          if (held === null || replace) precisionRef.current = next;
          setPrecision(precisionRef.current);
          accept(kind, response.items, response.has_more, replace);
        })
        .catch((err: unknown) => {
          if (unmountedRef.current || generation !== generationRef.current) return;
          console.error(`useFootprint: failed to load the footprint of ${instrumentId}`, err);
          if (err instanceof HttpError && !TRANSIENT_HTTP.has(err.status)) {
            setError(`Data API answered ${err.status} -- footprint not loaded (see error bar)`);
            // A deterministic answer is not asked again on every pan: no older page after a failed
            // one, the hole fill ends, and an initial page is asked for again on the next enable.
            if (kind === "older") cursor.hasMore = false;
            if (kind === "fill") endFill(cursor);
            if (kind === "initial" && cursor.earliestMs === null) cursor.started = false;
            return;
          }
          // The newest-page refresh is retried by its own interval, never stacked.
          if (kind === "newest") return;
          retry("Can't reach the data API -- footprint not loaded, retrying...");
        })
        .finally(() => {
          if (unmountedRef.current || generation !== generationRef.current) return;
          cursor.busy = false;
          // Switched off mid-fill: the fill resumes on the next enable, never while off.
          if (succeeded && cursor.fillToMs !== null && cursor.earliestFillMs !== null) {
            if (enabledRef.current) loadRef.current(cursor.earliestFillMs * 1_000_000, "fill");
            return;
          }
          // A scroll-back that arrived while this request was in flight was dropped by the busy
          // guard: check the range again now, whatever kind of page this was.
          if (succeeded) loadOlderRef.current();
        });
    },
    [instrumentId, barSeconds, rowTicks, accept],
  );

  // The scroll-back rule: the visible range's left edge within the margin of the footprint's own
  // earliest bar. That bar must be a slot of the chart; a footprint already older than every loaded
  // candle waits for the candles' own refill (which moves the range and calls this again).
  const loadOlder = useCallback((): void => {
    const cursor = cursorRef.current;
    const timeScale = chart?.timeScale();
    if (!enabledRef.current || !timeScale || cursor.busy || !cursor.hasMore || cursor.earliestMs === null) return;
    const range = timeScale.getVisibleLogicalRange();
    const index = timeScale.timeToIndex((cursor.earliestMs / 1000) as UTCTimestamp, false);
    if (range === null || index === null || range.from >= index + REFILL_MARGIN_BARS) return;
    load(cursor.earliestMs * 1_000_000, "older");
  }, [chart, load]);

  useEffect(() => {
    enabledRef.current = enabled;
    loadRef.current = load;
    loadOlderRef.current = loadOlder;
  }, [enabled, load, loadOlder]);

  useEffect(() => {
    if (rowTicksRef.current === rowTicks) return;
    rowTicksRef.current = rowTicks;
    generationRef.current += 1;
    cursorRef.current = freshCursor();
    attemptRef.current = 0;
    if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    retryTimerRef.current = null;
  }, [rowTicks]);

  useEffect(() => {
    if (!enabled) return;
    const cursor = cursorRef.current;
    if (!cursor.started) {
      cursor.started = true;
      load(Date.now() * 1_000_000, "initial");
    } else if (cursor.fillToMs !== null && cursor.earliestFillMs !== null) {
      load(cursor.earliestFillMs * 1_000_000, "fill");
    }
    const refresh = setInterval(() => {
      if (refreshDue(cursorRef.current, barSeconds, Date.now())) load(Date.now() * 1_000_000, "newest");
    }, FOOTPRINT_REFRESH_MS);
    return () => {
      clearInterval(refresh);
      // Off means no request at all, a pending retry included; an initial page that never
      // arrived is asked for again on the next enable.
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
        if (cursorRef.current.earliestMs === null) cursorRef.current.started = false;
      }
    };
  }, [enabled, load, barSeconds]);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const handler = (): void => loadOlder();
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(handler);
  }, [chart, loadOlder]);

  useEffect(() => {
    // StrictMode's dev re-mount runs the cleanup and then this again: mounted once more.
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
  }, []);

  const items = pages.rowTicks === rowTicks ? pages.items : NO_ITEMS;
  return { items, precision, error };
}

const NO_ITEMS: FootprintItem[] = [];

/**
 * Whether a newest-page refresh can bring a bar not held yet: none is held, or the bar after the
 * newest held one has closed (a bar is served only after it closed, and settled on top of that).
 */
export function refreshDue(cursor: Pick<Cursor, "latestMs">, barSeconds: number, nowMs: number): boolean {
  return cursor.latestMs === null || nowMs >= cursor.latestMs + 2 * barSeconds * 1000;
}

/**
 * Whether a pending older or fill retry was overtaken while it waited: a page loaded meanwhile (a
 * pan's own older page, a refresh's fill) moved that cursor on, so the retry would fetch bars
 * already held and move the cursor back to them.
 */
function superseded(cursor: Cursor, kind: PageKind, beforeNs: number): boolean {
  if (kind === "older") return cursor.earliestMs !== null && beforeNs !== cursor.earliestMs * 1_000_000;
  if (kind === "fill") return cursor.earliestFillMs === null || beforeNs !== cursor.earliestFillMs * 1_000_000;
  return false;
}

function endFill(cursor: Cursor): void {
  cursor.fillToMs = null;
  cursor.earliestFillMs = null;
}

/**
 * Keep the loaded bars contiguous. A newest page whose oldest bar is after the newest bar already
 * held (the page was away longer than one page, e.g. a sleeping tab) leaves a hole that neither the
 * refresh nor the scroll-back would ever reach: start filling it back page by page, until a page
 * reaches the bar held before it, comes back empty or says nothing older exists. A page starting at
 * the bar right after the newest held one leaves none. A second hole while the first is still being
 * filled (the fill paused while off, or waiting on a retry) restarts the fill from the new hole and
 * runs it on to the first hole's older edge, re-reading the bars held between the two (merged, never
 * doubled) rather than tracking a list of holes.
 */
function trackHole(
  cursor: Cursor,
  kind: PageKind,
  items: readonly FootprintItem[],
  hasMore: boolean,
  barMs: number,
): void {
  const oldest = items.length > 0 ? items[0].t : null;
  const newest = items.length > 0 ? items[items.length - 1].t : null;
  if (kind === "newest" && cursor.latestMs !== null && oldest !== null && oldest > cursor.latestMs + barMs && hasMore) {
    cursor.fillToMs = cursor.fillToMs ?? cursor.latestMs;
    cursor.earliestFillMs = oldest;
  } else if (kind === "fill") {
    const done = oldest === null || !hasMore || cursor.fillToMs === null || oldest <= cursor.fillToMs;
    if (done) endFill(cursor);
    else cursor.earliestFillMs = oldest;
  }
  if (newest !== null && (cursor.latestMs === null || newest > cursor.latestMs)) cursor.latestMs = newest;
}
