import type { IChartApi } from "lightweight-charts";
import { useCallback, useMemo, useState } from "react";

import type { IndicatorConfigEntry } from "../api/schema";
import { STORED_VWAP_SOURCE } from "../lib/anchoredVwap";
import type { Drawing } from "../lib/drawings";
import { entryId } from "../lib/indicatorId";
import { type PickerDatum, usePickerIndicatorValues } from "./usePickerIndicatorValues";

/** The backend's unlisted catalog entry serving a stored-source Anchored VWAP drawing's line. */
export const STORED_VWAP_INDICATOR = "AnchoredStoredVWAP";

/** The values-request entry of a stored-source drawing anchored at `time` (UTC seconds). */
export function storedVwapEntry(time: number): IndicatorConfigEntry {
  return { name: STORED_VWAP_INDICATOR, category: "custom", params: { anchor_t: String(Math.round(time * 1000)) } };
}

const NO_ERRORS: Record<string, string> = {};

export interface StoredAnchoredVwap {
  /** Each stored-source drawing's values, by drawing id (absent until its page arrived). */
  values: Record<string, PickerDatum[]>;
  /** Each drawing's replay error (e.g. an anchor older than the candle store), by drawing id. */
  errors: Record<string, string>;
}

/**
 * Story 33.6: the stored-source Anchored VWAP drawings' lines, `Σpv / ΣV` from each anchor, computed
 * by the server (the unlisted `AnchoredStoredVWAP` entry), never in the browser. One request entry
 * per distinct anchor, through a values hook instance of its own: these entries are never picker
 * panes, never saved in the coin's indicator config, and page with the candles like the panes do.
 *
 * Known limit: like every picker pane, the values are read when the anchors change and as the chart
 * scrolls back, not as live bars close, so the line ends at the newest bar of its last fetch until
 * the anchor moves or the chart is reopened. Upgrade path: refetch the newest page on a bar close,
 * as `useChartDerivatives` does for its panes.
 */
export function useStoredAnchoredVwap(
  instrumentId: string,
  chart: IChartApi | null,
  drawings: readonly Drawing[],
  barSeconds: number,
  enabled = true,
): StoredAnchoredVwap {
  // Off (Lines mode, where no Anchored VWAP is drawn), no anchor is requested at all.
  const stored = useMemo(
    () =>
      enabled
        ? drawings.flatMap((d) => (d.kind === "anchored_vwap" && d.source === STORED_VWAP_SOURCE ? [d] : []))
        : [],
    [drawings, enabled],
  );
  const anchorsKey = [...new Set(stored.map((d) => d.time))].sort((a, b) => a - b).join(",");
  const entries = useMemo(
    () => (anchorsKey === "" ? [] : anchorsKey.split(",").map((t) => storedVwapEntry(Number(t)))),
    [anchorsKey],
  );
  // Errors are kept across pages: each response reports only its own page's, and an anchor the store
  // cannot reach fails the newer pages while an older page holding the anchor succeeds -- that
  // success must not erase the error and leave a partial line reading as whole (audit D-187). They
  // are dropped when the request itself changes (anchors, instrument, bar width).
  // Held with the request they belong to, so a changed request reads none without an effect resetting them.
  const requestKey = `${instrumentId}|${barSeconds}|${anchorsKey}`;
  const [held, setHeld] = useState<{ key: string; errors: Record<string, string> }>({ key: requestKey, errors: {} });
  const onErrors = useCallback(
    (errors: Record<string, string>) =>
      setHeld((prev) => {
        if (Object.keys(errors).length === 0) return prev;
        return { key: requestKey, errors: prev.key === requestKey ? { ...prev.errors, ...errors } : errors };
      }),
    [requestKey],
  );
  const entryErrors = held.key === requestKey ? held.errors : NO_ERRORS;
  const series = usePickerIndicatorValues(instrumentId, chart, entries, barSeconds, onErrors);
  return useMemo(() => {
    const values: Record<string, PickerDatum[]> = {};
    const errors: Record<string, string> = {};
    for (const d of stored) {
      const id = entryId(storedVwapEntry(d.time));
      const data = series[`${id}.value`];
      if (data) values[d.id] = data;
      if (entryErrors[id] !== undefined) errors[d.id] = entryErrors[id];
    }
    return { values, errors };
  }, [stored, series, entryErrors]);
}
