import type { IChartApi, LineData, LogicalRange, Time, UTCTimestamp, WhitespaceData } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchIndicatorValues } from "../api/client";
import type { IndicatorConfigEntry, IndicatorValuesItem } from "../api/schema";

// Mirrors useCandles.ts/useIndicatorSeries.ts's own constants exactly -- co-paging (AD-F3)
// depends on every chart-history hook sharing the identical before_ns/limit/bar_seconds
// tuple, not just a similarly-shaped one.
const INITIAL_LIMIT = 120;
const DEFAULT_BAR_SECONDS = 60;
const REFILL_MARGIN_BARS = 20;

export type PickerDatum = LineData<Time> | WhitespaceData<Time>;

function toDatum(timeMs: number, value: number | null | undefined): PickerDatum {
  const time = (timeMs / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  // A missing/null value (gap marker, or a not-yet-initialized/uncaptured-delta indicator,
  // per this route's own I/O matrix row) is passed straight through as native whitespace
  // data -- never filtered or reshaped (same discipline as useCandles' toChartDatum).
  return value == null ? { time } : { time, value };
}

function toSeriesByKey(items: IndicatorValuesItem[]): Record<string, PickerDatum[]> {
  const keys = new Set<string>();
  for (const item of items) {
    for (const key of Object.keys(item.values ?? {})) keys.add(key);
  }
  const out: Record<string, PickerDatum[]> = {};
  for (const key of keys) {
    out[key] = items.map((item) => toDatum(item.t, item.values?.[key]));
  }
  return out;
}

/**
 * Cursor-paginated values for the picker's currently-configured indicators (AD-F3),
 * keyed by `f"{indicator_id}.{output_attr}"` -- the exact registry key
 * `LightweightChart.tsx`'s pane `Map` expects (AD-F4), one pane per output series so a
 * multi-output indicator (e.g. Bollinger Bands) renders one line per output without this
 * story touching `LightweightChart.tsx`'s one-series-per-pane-id model.
 *
 * Resets and re-fetches from scratch whenever `entries` changes (add/remove/param-apply)
 * -- each picker change is a materially different request (AD-F2/AD-F3: no unbounded/
 * stitched-across-requests state). Anchors its initial fetch to `Date.now()` (mirrors
 * useCandles/useIndicatorSeries), so a newly-added indicator backfills its own visible
 * history via the normal scroll-back trigger, not a full page reload (Design Notes).
 */
export function usePickerIndicatorValues(
  instrumentId: string,
  chart: IChartApi | null,
  entries: IndicatorConfigEntry[],
  barSeconds = DEFAULT_BAR_SECONDS,
  onErrors?: (errors: Record<string, string>) => void,
): Record<string, PickerDatum[]> {
  const [seriesByKey, setSeriesByKey] = useState<Record<string, PickerDatum[]>>({});
  const hasMoreOlderRef = useRef(true);
  const loadingRef = useRef(false);
  const earliestMsRef = useRef<number | null>(null);
  const itemsRef = useRef<IndicatorValuesItem[]>([]);
  // Set when the entries-change reset effect below fires while a stale request (for the
  // previous `entries`) is still in flight -- `loadPage`'s own `loadingRef` guard silently
  // drops that reset call otherwise, leaving `seriesByKey` empty until an unrelated
  // chart-scroll event happens to retrigger a fetch. Checked/cleared in `loadPage`'s
  // `finally` to retry the reset once the stale request clears.
  const pendingResetRef = useRef(false);

  // Entries compared by value, not by array identity -- ChartPage/IndicatorPicker may
  // rebuild the `entries` array on every render even when its content hasn't changed.
  const entriesKey = useMemo(
    () => JSON.stringify(entries.map((e) => ({ name: e.name, params: e.params ?? {} }))),
    [entries],
  );
  const requestEntries = useMemo(
    () => entries.map((e) => ({ name: e.name, params: e.params ?? {} })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entriesKey],
  );

  const loadPage = useCallback(
    (beforeNs: number, prepend: boolean): Promise<void> => {
      if (requestEntries.length === 0) return Promise.resolve();
      if (loadingRef.current) return Promise.resolve();
      loadingRef.current = true;
      return fetchIndicatorValues(instrumentId, beforeNs, INITIAL_LIMIT, barSeconds, requestEntries)
        .then((response) => {
          // Per-indicator failures (bad/stale entry): the good ones still plotted, so surface
          // which ones failed instead of leaving a silently missing pane.
          onErrors?.(response.errors ?? {});
          if (response.items.length === 0) {
            hasMoreOlderRef.current = false;
            return;
          }
          earliestMsRef.current = response.items[0].t;
          hasMoreOlderRef.current = response.has_more;
          if (prepend && itemsRef.current.length > 0) {
            // Same page-boundary seam-gap check as useCandles'/useIndicatorSeries' own
            // loadPage -- a real collection gap can straddle exactly the page cursor,
            // which each page's own gap-marker insertion can't see on its own.
            const barMs = barSeconds * 1000;
            const newestMs = response.items[response.items.length - 1].t;
            const boundaryMs = itemsRef.current[0].t;
            const seam: IndicatorValuesItem[] =
              newestMs + barMs < boundaryMs ? [{ t: newestMs + barMs, values: {} }] : [];
            itemsRef.current = [...response.items, ...seam, ...itemsRef.current];
          } else {
            itemsRef.current = response.items;
          }
          setSeriesByKey(toSeriesByKey(itemsRef.current));
        })
        .catch((err: unknown) => {
          console.error(`usePickerIndicatorValues: failed to load values for ${instrumentId}`, err);
        })
        .finally(() => {
          loadingRef.current = false;
          if (pendingResetRef.current) {
            pendingResetRef.current = false;
            resetAndLoadRef.current();
          }
        });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [instrumentId, requestEntries, barSeconds],
  );

  const resetAndLoad = useCallback(() => {
    itemsRef.current = [];
    hasMoreOlderRef.current = true;
    earliestMsRef.current = null;
    setSeriesByKey({});
    // Anchor to the chart's currently visible right edge, not Date.now() -- unlike
    // useCandles/useIndicatorSeries (whose one-time initial load always coincides with
    // the chart's live-edge starting view), this reset can fire mid-session while the
    // user has already scrolled back. Anchoring to "now" would load a window the current
    // viewport can't see at all, leaving the newly-added pane blank until an unrelated
    // scroll event happens to trigger a refill (Design Notes: backfills visible history
    // without a full page reload).
    const visibleRange = chart?.timeScale().getVisibleRange();
    const anchorNs = visibleRange ? (visibleRange.to as number) * 1_000_000_000 : Date.now() * 1_000_000;
    void loadPage(anchorNs, false);
  }, [loadPage, chart]);

  // Always holds the latest `resetAndLoad` (closed over the current `requestEntries`), so
  // `loadPage`'s `finally` above -- which may belong to a now-stale request generation --
  // retries against the up-to-date entries, not whatever `requestEntries` were in scope
  // when that in-flight fetch started.
  const resetAndLoadRef = useRef(resetAndLoad);
  resetAndLoadRef.current = resetAndLoad;

  useEffect(() => {
    // A reset triggered by an `entries` change (add/remove/param-apply) while the previous
    // entries' fetch is still in flight would otherwise be silently dropped by `loadPage`'s
    // own `loadingRef` guard, leaving `seriesByKey` stale/empty until an unrelated
    // chart-scroll event happens to retrigger a fetch. Defer the reset to that in-flight
    // fetch's own `finally` instead of dropping it.
    if (loadingRef.current) {
      pendingResetRef.current = true;
      return;
    }
    resetAndLoad();
  }, [resetAndLoad]);

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

  return seriesByKey;
}
