import type { Time } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import { fetchCandles } from "../api/client";
import type { CandleItem } from "../api/schema";
import type { ChartDatum, VolumeDatum } from "./useCandles";

const PAGE_LIMIT = 500;
// Bounds one paging run: 80 pages x 500 bars = 40k bars. The actual page budget is sized to the
// missing span (see `pageBudget`), never above this.
const HARD_MAX_PAGES = 80;
// MEM-01 on the client: never more bars held than one full run can fetch, whatever mix of paging
// runs, refreshes and rollovers built them up.
// Known limit: coverage is one contiguous span ending now, so a replay anchored further back than
// this many bars (40k: ~28 days of the daily profile's 1-minute bars) cannot reach its sessions --
// they show as "Showing n of m". Upgrade path: hold disjoint covered spans and page a window
// around the replay cutoff instead of everything from now back to it.
const MAX_ITEMS = HARD_MAX_PAGES * PAGE_LIMIT;
// How often the in-progress tail is refreshed (and a failed paging run retried), whatever the bar
// size: a 15-minute monthly bar must not leave the live profile up to 15 minutes stale.
const MAX_REFRESH_SECONDS = 60;
// The refresh pulls just the newest few bars -- the in-progress session's tail.
const REFRESH_LIMIT = 5;
/** DW-153: a Sessions/period edit waits this long for the next keystroke before it pages. */
export const SESSION_REQUEST_DEBOUNCE_MS = 300;

const pageBudget = (fromSeconds: number, sinceSeconds: number, barSeconds: number): number =>
  Math.min(HARD_MAX_PAGES, Math.ceil((fromSeconds - sinceSeconds) / barSeconds / PAGE_LIMIT) + 2);

const refreshMs = (barSeconds: number): number => Math.min(barSeconds, MAX_REFRESH_SECONDS) * 1000;

export interface SessionCandles {
  candles: ChartDatum[];
  volume: VolumeDatum[];
  /** Periods starting before this are only partially covered (the page cap stopped the
   * fetch short of them) -- `buildSessionProfiles` omits them. `null` = fully covered
   * (the fetch reached the wanted start, or the history is exhausted). */
  completeFrom: number | null;
  /** A paging run is fetching older history right now. */
  loading: boolean;
}

interface FetchState {
  /** `instrumentId|barSeconds` these items were fetched for: items of another key never mix. */
  key: string;
  /** Real bars only (gap markers dropped), oldest first, unique by `t`. */
  items: CandleItem[];
  /** Every bar from here (ms) to the newest refresh is held: how far back the paging reached.
   * `null` until the first page of this key landed. */
  coveredFromMs: number | null;
  /** The server reported no history older than `coveredFromMs` (`has_more: false`). */
  exhausted: boolean;
  /** When (ms) the last fetch that reached up to now was sent -- a refresh, or the first page of
   * a run from now. The refresh bridges from here, not from the newest bar: a quiet market or a
   * capture outage leaves the newest bar old without any refresh having been missed. */
  syncedMs: number | null;
}

const emptyFor = (key: string): FetchState => ({ key, items: [], coveredFromMs: null, exhausted: false, syncedMs: null });
const EMPTY = emptyFor("");

// A state built for another key is discarded, not merged into.
const forKey = (s: FetchState, key: string): FetchState => (s.key === key ? s : emptyFor(key));

function isBar(item: CandleItem): boolean {
  return item.o != null && item.h != null && item.l != null && item.c != null && item.v != null;
}

function merge(prev: CandleItem[], incoming: CandleItem[]): CandleItem[] {
  const byTime = new Map(prev.map((i) => [i.t, i] as const));
  for (const item of incoming) if (isBar(item)) byTime.set(item.t, item);
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

/** The oldest bars beyond MAX_ITEMS are dropped, and the coverage moves up with them. */
function capped(s: FetchState): FetchState {
  if (s.items.length <= MAX_ITEMS) return s;
  const items = s.items.slice(-MAX_ITEMS);
  return { ...s, items, coveredFromMs: items[0].t, exhausted: false };
}

/** Bars older than the wanted start are no longer needed (a rollover, a smaller count): dropped. */
function pruned(s: FetchState, sinceMs: number): FetchState {
  if (s.coveredFromMs === null || s.coveredFromMs >= sinceMs) return s;
  return { ...s, items: s.items.filter((i) => i.t >= sinceMs), coveredFromMs: sinceMs, exhausted: false };
}

/** A state writer for one key: the ref (read synchronously by the async runs) and the rendered
 * state are always written together, so they never disagree. */
function writerFor(
  ref: { current: FetchState },
  setState: (s: FetchState) => void,
  key: string,
): (fn: (s: FetchState) => FetchState) => void {
  return (fn) => {
    ref.current = fn(forKey(ref.current, key));
    setState(ref.current);
  };
}

const isCovered = (s: FetchState, sinceMs: number): boolean =>
  s.exhausted || (s.coveredFromMs !== null && s.coveredFromMs <= sinceMs);

/**
 * Story 18.8: the finest-timeframe candles a session/period profile needs, independent of
 * the chart's own loaded window (which is ~2h on open and only grows on scroll-back, so a
 * day's profile could never be built from it). Pages `/api/candles` back until `sinceSeconds`
 * is covered (or the history/page cap ends), then refreshes only the newest bars -- so closed
 * sessions are never re-fetched and only the in-progress one moves. Idle while `enabled` is false.
 *
 * DW-152/153: a new wanted start is served incrementally. A later one (a rollover, fewer
 * sessions) only prunes; an earlier one (more sessions) pages back from the oldest held bar, never
 * from now again. The request is debounced (`SESSION_REQUEST_DEBOUNCE_MS`; the first one for a
 * bar size goes out at once) and a run superseded by a newer request is aborted mid-page.
 */
export function useSessionCandles(
  instrumentId: string,
  enabled: boolean,
  sinceSeconds: number,
  barSeconds: number,
): SessionCandles {
  const key = `${instrumentId}|${barSeconds}`;
  const [state, setState] = useState<FetchState>(EMPTY);
  // The latest state, readable synchronously by the async runs below (written via `writerFor`).
  const stateRef = useRef<FetchState>(EMPTY);
  const [loading, setLoading] = useState(false);
  // Bumped when the held bars can no longer be trusted to be contiguous (see the refresh): the
  // paging effect then starts over from now.
  const [generation, setGeneration] = useState(0);
  // The current paging run, so the refresh's reset can abort it BEFORE clearing the state: a page
  // landing between the clear and the effect cleanup would otherwise seed a stale cursor.
  const runRef = useRef<AbortController | null>(null);
  // The key the paging effect last sent a request for: only a key's first request skips the
  // debounce (a failed first page must not make every later keystroke an undebounced request).
  const requestedKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    runRef.current = controller;
    const { signal } = controller;
    const update = writerFor(stateRef, setState, key);
    const sinceMs = sinceSeconds * 1000;

    const pageBack = async (): Promise<void> => {
      if (isCovered(forKey(stateRef.current, key), sinceMs)) {
        update((s) => pruned(s, sinceMs));
        setLoading(false);
        return;
      }
      setLoading(true);
      const fromNow = forKey(stateRef.current, key).coveredFromMs === null;
      let cursorMs = forKey(stateRef.current, key).coveredFromMs ?? Date.now();
      const sentMs = cursorMs;
      const pages = pageBudget(cursorMs / 1000, sinceSeconds, barSeconds);
      for (let page = 0; page < pages && forKey(stateRef.current, key).items.length < MAX_ITEMS; page++) {
        const response = await fetchCandles(instrumentId, cursorMs * 1_000_000, PAGE_LIMIT, barSeconds, signal);
        if (signal.aborted) return;
        const earliest = response.items.length > 0 ? response.items[0].t : null;
        const reached = Math.min(earliest ?? cursorMs, cursorMs);
        update((s) =>
          capped({
            ...s,
            items: merge(s.items, response.items.filter(isBar)),
            coveredFromMs: Math.min(reached, s.coveredFromMs ?? reached),
            exhausted: !response.has_more,
            syncedMs: fromNow && page === 0 ? sentMs : s.syncedMs,
          }),
        );
        // A page that does not reach older than its cursor would be re-requested unchanged: stop.
        if (!response.has_more || earliest === null || earliest <= sinceMs || earliest >= cursorMs) break;
        cursorMs = earliest;
      }
      update((s) => pruned(s, sinceMs));
      setLoading(false);
    };

    let retry: ReturnType<typeof setTimeout> | undefined;
    const attempt = (): void => {
      pageBack().catch((err: unknown) => {
        if (signal.aborted) return; // superseded, not failed
        console.error(`useSessionCandles: load failed for ${instrumentId}`, err);
        setLoading(false);
        // Whatever was paged before the failure stays; the rest is retried, never left missing.
        retry = setTimeout(attempt, refreshMs(barSeconds));
      });
    };
    // The first request for this key goes out at once; a later edit waits out the debounce so a
    // burst of keystrokes in the Sessions field becomes one request.
    const first = requestedKeyRef.current !== key;
    requestedKeyRef.current = key;
    const debounce = first ? undefined : setTimeout(attempt, SESSION_REQUEST_DEBOUNCE_MS);
    if (first) attempt();
    return () => {
      controller.abort();
      if (runRef.current === controller) runRef.current = null;
      clearTimeout(debounce);
      clearTimeout(retry);
      // An aborted run never reaches its own `setLoading(false)`.
      setLoading(false);
    };
  }, [instrumentId, key, enabled, sinceSeconds, barSeconds, generation]);

  // The live tail, independent of the wanted start: a rollover or a count edit never restarts it.
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const update = writerFor(stateRef, setState, key);
    // Refresh responses can resolve out of order: only the latest request may apply.
    let refreshSeq = 0;
    let appliedSeq = 0;

    const refresh = async (): Promise<void> => {
      const held = forKey(stateRef.current, key);
      // Nothing paged from now yet: the paging run owns the first fetch (and retries it).
      if (held.coveredFromMs === null || held.syncedMs === null) return;
      // Enough bars to bridge however long the refresh was starved (a throttled background tab,
      // a stalled request) -- a fixed small limit would leave a hole. The bar holding `syncedMs`
      // was still forming then, so it is fetched again too.
      const missed = Math.ceil((Date.now() - held.syncedMs) / 1000 / barSeconds);
      if (missed + 2 > PAGE_LIMIT) {
        // Starved past one page (a suspended laptop): the refresh cannot bridge it and the held
        // bars would silently skip the hole, so they are dropped and paged again from now.
        runRef.current?.abort();
        requestedKeyRef.current = null;
        update(() => emptyFor(key));
        setGeneration((g) => g + 1);
        return;
      }
      const limit = Math.max(REFRESH_LIMIT, missed + 2);
      const seq = ++refreshSeq;
      const sentMs = Date.now();
      const response = await fetchCandles(instrumentId, sentMs * 1_000_000, limit, barSeconds);
      if (cancelled || seq < appliedSeq) return;
      appliedSeq = seq;
      // A starvation reset (or a key change) while this was in flight emptied the state: the tail
      // alone would read as fully covered history, so it is dropped and the new paging run owns it.
      update((s) =>
        s.coveredFromMs === null
          ? s
          : capped({ ...s, items: merge(s.items, response.items), syncedMs: Math.max(s.syncedMs ?? sentMs, sentMs) }),
      );
    };

    const id = setInterval(
      () => void refresh().catch((err: unknown) => console.error(`useSessionCandles: refresh failed for ${instrumentId}`, err)),
      refreshMs(barSeconds),
    );
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [instrumentId, key, enabled, barSeconds]);

  return useMemo(() => {
    const held = forKey(state, key);
    const candles: ChartDatum[] = [];
    const volume: VolumeDatum[] = [];
    for (const i of held.items) {
      const time = (i.t / 1000) as unknown as Time;
      candles.push({ time, open: i.o!, high: i.h!, low: i.l!, close: i.c! });
      volume.push({ time, value: i.v! });
    }
    // Covered = history ran out, or the paging reaches the wanted start -- derived from the
    // CURRENT `sinceSeconds`, so asking for more sessions is "not covered" until fetched.
    const covered = held.coveredFromMs === null || isCovered(held, sinceSeconds * 1000);
    return {
      candles,
      volume,
      completeFrom: covered ? null : held.coveredFromMs! / 1000,
      loading: enabled && loading,
    };
  }, [state, key, sinceSeconds, enabled, loading]);
}
