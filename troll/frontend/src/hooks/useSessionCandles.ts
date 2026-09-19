import type { Time } from "lightweight-charts";
import { useEffect, useMemo, useState } from "react";

import { fetchCandles } from "../api/client";
import type { CandleItem } from "../api/schema";
import type { ChartDatum, VolumeDatum } from "./useCandles";

const PAGE_LIMIT = 500;
// Bounds one session fetch (MEM-01 on the client): 40 pages x 500 bars = 20k bars.
const MAX_PAGES = 40;
// The refresh pulls just the newest few bars -- the in-progress session's tail.
const REFRESH_LIMIT = 5;

export interface SessionCandles {
  candles: ChartDatum[];
  volume: VolumeDatum[];
  /** Periods starting before this are only partially covered (the page cap stopped the
   * fetch short of them) -- `buildSessionProfiles` omits them. `null` = fully covered
   * (the fetch reached the wanted start, or the history is exhausted). */
  completeFrom: number | null;
}

interface FetchState {
  /** The bar size these items were fetched at: items of another bar size never mix. */
  barSeconds: number;
  /** Real bars only (gap markers dropped), oldest first, unique by `t`. */
  items: CandleItem[];
  /** The server reported no older history (`has_more: false`). */
  exhausted: boolean;
}

const EMPTY: FetchState = { barSeconds: 0, items: [], exhausted: false };

// A state built for another bar size is discarded, not merged into.
const forBarSize = (s: FetchState, barSeconds: number): FetchState =>
  s.barSeconds === barSeconds ? s : { ...EMPTY, barSeconds };

function isBar(item: CandleItem): boolean {
  return item.o != null && item.h != null && item.l != null && item.c != null && item.v != null;
}

function merge(prev: CandleItem[], incoming: CandleItem[]): CandleItem[] {
  const byTime = new Map(prev.map((i) => [i.t, i] as const));
  for (const item of incoming) if (isBar(item)) byTime.set(item.t, item);
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

/**
 * Story 18.8: the finest-timeframe candles a session/period profile needs, independent of
 * the chart's own loaded window (which is ~2h on open and only grows on scroll-back, so a
 * day's profile could never be built from it). Pages `/api/candles` back from now until
 * `sinceSeconds` is covered (or the history/page cap ends), then refreshes only the newest
 * bars once per bar interval -- so closed sessions are never re-fetched and only the
 * in-progress one moves. Idle while `enabled` is false.
 */
export function useSessionCandles(
  instrumentId: string,
  enabled: boolean,
  sinceSeconds: number,
  barSeconds: number,
): SessionCandles {
  const [state, setState] = useState<FetchState>(EMPTY);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let loaded = false;
    // Refresh responses can resolve out of order: only the latest request may apply.
    let refreshSeq = 0;
    let appliedSeq = 0;
    let newestSeconds: number | null = null;

    const load = async (): Promise<void> => {
      let cursorNs = Date.now() * 1_000_000;
      for (let page = 0; page < MAX_PAGES; page++) {
        const response = await fetchCandles(instrumentId, cursorNs, PAGE_LIMIT, barSeconds);
        if (cancelled) return;
        const items = response.items.filter(isBar);
        const earliest = response.items.length > 0 ? response.items[0].t : null;
        const covered = !response.has_more || earliest === null || earliest / 1000 <= sinceSeconds;
        setState((s) => {
          const base = forBarSize(s, barSeconds);
          return { ...base, items: merge(base.items, items), exhausted: base.exhausted || !response.has_more };
        });
        if (items.length > 0) newestSeconds = Math.max(newestSeconds ?? 0, items[items.length - 1].t / 1000);
        if (covered) break;
        cursorNs = earliest! * 1_000_000;
      }
    };

    const refresh = async (): Promise<void> => {
      if (!loaded) return;
      // Enough bars to bridge however long the last refresh was starved (a throttled
      // background tab, a stalled request) -- a fixed small limit would leave a hole.
      const missed = newestSeconds === null ? 0 : Math.ceil((Date.now() / 1000 - newestSeconds) / barSeconds);
      const limit = Math.min(PAGE_LIMIT, Math.max(REFRESH_LIMIT, missed + 1));
      const seq = ++refreshSeq;
      const response = await fetchCandles(instrumentId, Date.now() * 1_000_000, limit, barSeconds);
      if (cancelled || seq < appliedSeq) return;
      appliedSeq = seq;
      const fresh = response.items.filter(isBar);
      if (fresh.length > 0) newestSeconds = Math.max(newestSeconds ?? 0, fresh[fresh.length - 1].t / 1000);
      setState((s) => {
        const base = forBarSize(s, barSeconds);
        return { ...base, items: merge(base.items, fresh) };
      });
    };

    const report = (what: string) => (err: unknown) => console.error(`useSessionCandles: ${what} failed for ${instrumentId}`, err);
    // `loaded` also flips on a failed load: the refresh must keep running (the coverage
    // logic omits whatever history is still missing) rather than freeze the live session.
    load()
      .catch(report("load"))
      .finally(() => {
        loaded = true;
      });
    const id = setInterval(() => void refresh().catch(report("refresh")), barSeconds * 1000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [instrumentId, enabled, sinceSeconds, barSeconds]);

  return useMemo(() => {
    const fresh = forBarSize(state, barSeconds);
    const candles: ChartDatum[] = [];
    const volume: VolumeDatum[] = [];
    for (const i of fresh.items) {
      const time = (i.t / 1000) as unknown as Time;
      candles.push({ time, open: i.o!, high: i.h!, low: i.l!, close: i.c! });
      volume.push({ time, value: i.v! });
    }
    const first = fresh.items[0];
    // Covered = history ran out, or the fetch reaches the wanted start -- derived from the
    // CURRENT `sinceSeconds`, so asking for more sessions is "not covered" until fetched.
    const covered = fresh.exhausted || !first || first.t / 1000 <= sinceSeconds;
    return { candles, volume, completeFrom: covered ? null : first.t / 1000 };
  }, [state, barSeconds, sinceSeconds]);
}
