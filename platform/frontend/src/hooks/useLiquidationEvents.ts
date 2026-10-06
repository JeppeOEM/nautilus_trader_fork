import { useCallback, useEffect, useRef, useState } from "react";

import { HttpError, fetchLiquidations } from "../api/client";
import type { LiquidationItem } from "../api/schema";

// Story 33.5: the individual liquidations behind the price-pane markers and the Liquidation tape
// (`GET /api/coin/{iid}/liquidations`: the archive plus the live bus's unflushed tail), with the live
// `liquidations:{iid}` rows pushed in by the page, each venue event once.

/** The most liquidations the markers hold (MEM-01): a cascade can be thousands of rows a minute. */
export const MARKER_MAX_ROWS = 5000;
/** The tape lists exactly this many, the newest. */
export const TAPE_ROWS = 50;
// An older marker page asks for the route's maximum, so a busy window reaches the candles' left edge
// in few requests.
const MARKER_PAGE_LIMIT = 500;
const RETRY_BASE_MS = 1000;
const RETRY_MAX_MS = 10_000;
const TRANSIENT_HTTP = new Set([502, 503, 504]);

export type LiquidationEventsMode = "markers" | "tape";

function byTime(a: LiquidationItem, b: LiquidationItem): number {
  return a.ts_event - b.ts_event || (a.venue_event_id < b.venue_event_id ? -1 : a.venue_event_id > b.venue_event_id ? 1 : 0);
}

/**
 * `held` plus `incoming`, each `venue_event_id` once (a re-served tie group and a live row the next
 * page also carries are one event, audit D-170), oldest first, cut to the newest `max`: the tape's 50,
 * or the markers' cap, where an older page past it loses its oldest rows (and paging back stops) and a
 * long session's live rows push the oldest markers out at the left.
 */
export function mergeLiquidations(
  held: readonly LiquidationItem[],
  incoming: readonly LiquidationItem[],
  max: number,
): { rows: LiquidationItem[]; capped: boolean } {
  // A live row newer than everything held (the common case) is appended without re-sorting `held`.
  const last = held.at(-1);
  if (last !== undefined && incoming.length === 1 && byTime(last, incoming[0]) < 0) {
    const rows = [...held, incoming[0]];
    return rows.length <= max ? { rows, capped: false } : { rows: rows.slice(rows.length - max), capped: true };
  }
  const byId = new Map<string, LiquidationItem>();
  for (const row of held) byId.set(row.venue_event_id, row);
  for (const row of incoming) if (!byId.has(row.venue_event_id)) byId.set(row.venue_event_id, row);
  const rows = [...byId.values()].sort(byTime);
  return rows.length <= max ? { rows, capped: false } : { rows: rows.slice(rows.length - max), capped: true };
}

export interface UseLiquidationEventsResult {
  rows: LiquidationItem[];
  /** True once the first page answered; an empty first page is an id without the feed. */
  loaded: boolean;
  /** The markers reached `MARKER_MAX_ROWS` before the candles' left edge. */
  capped: boolean;
  error: string | null;
  /** Add live rows (`useLiveLiquidations`), deduplicated against what is held. */
  push: (rows: readonly LiquidationItem[]) => void;
  /** Re-read the newest page (after a reconnect: rows published meanwhile were missed). */
  refreshNewest: () => void;
  /** Under a Bar Replay (`replayBeforeNs` set, tape mode): the newest `TAPE_ROWS` before the cutoff
   * bar's end, one page of its own, dropped when the replay ends; null otherwise. */
  replayRows: LiquidationItem[] | null;
  /** The Bar Replay page failed ("load failed"): shown as a failure, never as "no liquidations". */
  replayError: string | null;
}

/**
 * Liquidation rows for one instrument. `markers` loads the newest page, then pages back while the
 * earliest row is later than `earliestCandleTime` (chart seconds), up to `MARKER_MAX_ROWS`; `tape`
 * holds the newest `TAPE_ROWS`. Off (`enabled` false) issues no request and accepts no live row.
 * Retries follow `useCandles`: a gateway status or network failure with capped backoff, any other
 * status is final ("load failed").
 */
export function useLiquidationEvents(
  instrumentId: string,
  mode: LiquidationEventsMode,
  enabled: boolean,
  earliestCandleTime: number | null,
  replayBeforeNs: number | null = null,
): UseLiquidationEventsResult {
  const [state, setState] = useState<{ rows: LiquidationItem[]; capped: boolean }>({ rows: [], capped: false });
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rowsRef = useRef<LiquidationItem[]>([]);
  const hasMoreRef = useRef(true);
  const busyRef = useRef(false);
  // A newest re-read asked for while another page was in flight: run once that page settles.
  const pendingNewestRef = useRef(false);
  const startedRef = useRef(false);
  const loadedRef = useRef(false);
  const enabledRef = useRef(enabled);
  const attemptRef = useRef(0);
  const loggedRef = useRef(false);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);
  const max = mode === "tape" ? TAPE_ROWS : MARKER_MAX_ROWS;
  const earliestCandleRef = useRef(earliestCandleTime);
  const loadOlderRef = useRef<() => void>(() => {});
  const loadRef = useRef<(beforeNs: number, older: boolean) => void>(() => {});

  const accept = useCallback(
    (incoming: readonly LiquidationItem[]): void => {
      const merged = mergeLiquidations(rowsRef.current, incoming, max);
      rowsRef.current = merged.rows;
      if (merged.capped && mode === "markers") hasMoreRef.current = false;
      setState((prev) => ({ rows: merged.rows, capped: prev.capped || (merged.capped && mode === "markers") }));
    },
    [max, mode],
  );

  const load = useCallback(
    (beforeNs: number, older: boolean): void => {
      if (busyRef.current) {
        if (!older && loadedRef.current) pendingNewestRef.current = true;
        return;
      }
      busyRef.current = true;
      const limit = mode === "tape" ? TAPE_ROWS : MARKER_PAGE_LIMIT;
      fetchLiquidations(instrumentId, beforeNs, limit)
        .then((response) => {
          if (unmountedRef.current) return;
          attemptRef.current = 0;
          loggedRef.current = false;
          setError(null);
          if (older || !loadedRef.current) hasMoreRef.current = response.items.length > 0 && response.has_more;
          loadedRef.current = true;
          setLoaded(true);
          accept(response.items);
        })
        .catch((err: unknown) => {
          if (unmountedRef.current) return;
          if (!loggedRef.current) console.error(`useLiquidationEvents: failed to load liquidations of ${instrumentId}`, err);
          loggedRef.current = true;
          if (err instanceof HttpError && !TRANSIENT_HTTP.has(err.status)) {
            setError("load failed");
            if (older) hasMoreRef.current = false;
            return;
          }
          setError("load failed, retrying...");
          if (!enabledRef.current) return;
          attemptRef.current += 1;
          const delay = Math.min(RETRY_BASE_MS * 2 ** (attemptRef.current - 1), RETRY_MAX_MS);
          retryRef.current = setTimeout(() => loadRef.current(older ? beforeNs : Date.now() * 1_000_000, older), delay);
        })
        .finally(() => {
          busyRef.current = false;
          if (unmountedRef.current) return;
          if (pendingNewestRef.current && enabledRef.current) {
            pendingNewestRef.current = false;
            loadRef.current(Date.now() * 1_000_000, false);
            return;
          }
          // A candle page that landed while this one was in flight found us busy: look again.
          loadOlderRef.current();
        });
    },
    [instrumentId, mode, accept],
  );

  // Markers page back with the candles: while the earliest row is later than the earliest candle.
  const loadOlder = useCallback((): void => {
    const earliest = rowsRef.current[0];
    const candle = earliestCandleRef.current;
    if (mode !== "markers" || !enabledRef.current || !loadedRef.current || busyRef.current) return;
    if (!hasMoreRef.current || earliest === undefined || candle === null) return;
    if (earliest.ts_event / 1_000_000_000 > candle) load(earliest.ts_event, true);
  }, [mode, load]);

  const push = useCallback(
    (live: readonly LiquidationItem[]): void => {
      if (enabledRef.current && live.length > 0) accept(live);
    },
    [accept],
  );

  const refreshNewest = useCallback((): void => {
    if (enabledRef.current && loadedRef.current) load(Date.now() * 1_000_000, false);
  }, [load]);

  useEffect(() => {
    enabledRef.current = enabled;
    if (!enabled) return;
    if (!startedRef.current) {
      startedRef.current = true;
      load(Date.now() * 1_000_000, false);
    } else if (loadedRef.current) {
      // Re-enabled: live rows were refused while off, so the held rows may be stale.
      load(Date.now() * 1_000_000, false);
    }
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      retryRef.current = null;
      pendingNewestRef.current = false;
      if (!loadedRef.current) startedRef.current = false;
    };
  }, [enabled, load]);

  useEffect(() => {
    earliestCandleRef.current = earliestCandleTime;
    loadRef.current = load;
    loadOlderRef.current = loadOlder;
    loadOlder();
  }, [earliestCandleTime, load, loadOlder]);

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
    };
  }, []);

  // Bar Replay's tape: the newest held 50 are all after the cutoff in a busy market, so the replay reads
  // its own page ending at the cutoff bar's end (`before_ns`), and drops it on leaving the replay.
  // A failed page is an error, never an empty tape: moving the cutoff (or re-entering the replay) asks
  // again.
  const [replayRows, setReplayRows] = useState<LiquidationItem[] | null>(null);
  const [replayError, setReplayError] = useState<string | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setReplayError(null);
    if (mode !== "tape" || !enabled || replayBeforeNs === null) {
      setReplayRows(null);
      return;
    }
    let current = true;
    fetchLiquidations(instrumentId, replayBeforeNs, TAPE_ROWS)
      .then((response) => {
        if (current) setReplayRows(mergeLiquidations([], response.items, TAPE_ROWS).rows);
      })
      .catch((err: unknown) => {
        if (!current) return;
        console.error(`useLiquidationEvents: failed to load the replay tape of ${instrumentId}`, err);
        setReplayRows(null);
        setReplayError("load failed");
      });
    return () => {
      current = false;
    };
  }, [mode, enabled, replayBeforeNs, instrumentId]);

  return { rows: state.rows, loaded, capped: state.capped, error, push, refreshNewest, replayRows, replayError };
}
