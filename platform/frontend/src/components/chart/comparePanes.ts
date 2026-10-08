import type { Time } from "lightweight-charts";

import type { ChartDatum } from "../../hooks/useCandles";
import type { LiveBar } from "../../hooks/useLiveCandle";
import { COMPARE_GROUP_PREFIX, COMPARE_PALETTE, SPREAD_GROUP, alignCompare, spreadBps } from "../../lib/compare";
import { formatDecimal } from "../../lib/units";
import { chartVar } from "./chartTheme";
import type { CompareFeedState } from "./CompareFeed";
import type { IndicatorPaneSpec } from "./LightweightChart";

// Story 33.9: the compare symbols' overlay specs and the Spread pane, built from real closes only
// (AD-F6) and aligned on the main series' slots. They ride the indicator panes mechanism: legend rows
// with the eye and the x, hide in place, data diffing.

/** Bps print with two decimals: a spread is a ratio of two prices, not a price of its own. */
const SPREAD_DECIMALS = 2;

/** One slot of the main series as the chart draws it, and whether a real bar fills it. */
export interface MainSlot {
  time: number;
  real: boolean;
}

/** The main series' slots: every displayed slot (gap whitespace too), plus the forming bar while it
 * is shown and newer than the history. */
export function mainSlots(displayed: readonly ChartDatum[], liveBar: LiveBar | null): MainSlot[] {
  const slots = displayed.map((d) => ({ time: d.time as number, real: "close" in d }));
  const live = liveBar ? (liveBar.time as number) : null;
  if (live !== null && (slots.length === 0 || live > slots[slots.length - 1].time)) slots.push({ time: live, real: true });
  return slots;
}

/** The compare's closes on the main slots: whitespace where either side has no bar (a main gap slot
 * included, so a compare line never runs across the main series' hole). Its forming bar counts only
 * while the main one is shown. */
export function compareRows(slots: readonly MainSlot[], feed: CompareFeedState | undefined, liveShown: boolean) {
  const rows = alignCompare(
    slots.map((s) => s.time),
    feed?.candles ?? [],
    liveShown ? feed?.liveBar : null,
  );
  return rows.map((row, i) => (slots[i].real ? row : { time: row.time }));
}

/** One overlay per compare symbol, in the palette's order; a feed that failed reads "no data".
 *
 * Known limit: every main or compare live tick re-runs the O(n) `alignCompare` over all n main slots
 * for each compare, plus once more for the Spread pane: at most ~4 full realignments per tick (three
 * compares and the spread), each rebuilding a Map of the compare's closes. Fine at the chart's paged
 * history sizes; the ceiling is a long history with three compares on a busy tick rate. Upgrade path:
 * memo each compare's aligned rows keyed on its candles array and the main times, and on a tick patch
 * only the live slot. */
export function comparePaneSpecs(
  symbols: readonly string[],
  feeds: Readonly<Record<string, CompareFeedState>>,
  slots: readonly MainSlot[],
  liveShown: boolean,
  hidden: ReadonlySet<string>,
): IndicatorPaneSpec[] {
  return symbols.map((iid, slot) => {
    const feed = feeds[iid];
    const precision = feed?.precision ?? null;
    const group = `${COMPARE_GROUP_PREFIX}${iid}`;
    return {
      id: group,
      kind: "Line",
      data: compareRows(slots, feed, liveShown),
      color: chartVar(COMPARE_PALETTE[slot % COMPARE_PALETTE.length]),
      placement: "overlay",
      group,
      groupLabel: iid,
      outputLabel: iid,
      configurable: false,
      hidden: hidden.has(iid),
      ...(precision ? { format: (value: number) => formatDecimal(value, precision.price) } : {}),
      ...(feed?.loadFailed ? { text: "no data" } : {}),
    };
  });
}

/** The Spread pane of the one compare: `(main / compare - 1) * 1e4` bps per slot, gaps kept. */
export function spreadPaneSpec(
  mainIid: string,
  compareIid: string,
  displayed: readonly ChartDatum[],
  mainLive: LiveBar | null,
  feed: CompareFeedState | undefined,
  slots: readonly MainSlot[],
  liveShown: boolean,
): IndicatorPaneSpec {
  const live = mainLive ? { time: mainLive.time as Time, close: mainLive.close } : null;
  const main = alignCompare(
    slots.map((s) => s.time),
    displayed,
    liveShown ? live : null,
  );
  return {
    id: SPREAD_GROUP,
    kind: "Line",
    data: spreadBps(main, compareRows(slots, feed, liveShown)),
    color: chartVar(COMPARE_PALETTE[0]),
    placement: "pane",
    group: SPREAD_GROUP,
    groupLabel: `Spread ${mainIid} / ${compareIid} (bps)`,
    outputLabel: "bps",
    configurable: false,
    zeroLine: true,
    format: (value: number) => `${formatDecimal(value, SPREAD_DECIMALS)} bps`,
  };
}
