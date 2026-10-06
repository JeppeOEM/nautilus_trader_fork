import type { TimedBar } from "./sessionProfile";

/**
 * The Anchored VWAP drawing's maths (Story 32.7): cumulative Σ(source × volume) / Σ volume from the
 * anchor bar onward, and the volume-weighted standard deviation behind its ±1σ / ±2σ bands. No DOM
 * and no chart library, so it is tested against a hand computation.
 *
 * Prices stay floats here (a VWAP is a quotient, never a stored price); a printed value goes
 * through `lib/units.ts` at the instrument precision, and the line is plotted as is.
 *
 * Known limit: the variance uses the running-moment form E[s²] − VWAP² (three running sums, O(1) a
 * bar), which loses digits when the standard deviation is tiny against the price (below about
 * 1e-7 of it); the result is clamped at zero so it can never be NaN. Upgrade path: Welford's
 * weighted update, if bands on a nearly flat instrument ever need the digits.
 *
 * Known limit: the source is each chart bar's own price (hlc3/close/ohlc4 of the bar at the chart's
 * timeframe), so the value depends on the bar size: a 1h bar's hlc3 is not the VWAP of its sixty 1m
 * bars, and switching timeframe moves the line and its legend value slightly. Upgrade path: weight
 * by per-trade prices once Story 32.8's per-trade data is on the chart.
 */

export const VWAP_SOURCES = ["hlc3", "close", "ohlc4"] as const;
export type VwapSource = (typeof VWAP_SOURCES)[number];
export const DEFAULT_VWAP_SOURCE: VwapSource = "hlc3";
// Story 33.6: the Anchored VWAP drawing's sources -- the bar prices above plus `stored`, the bars'
// exact stored `pv` and volume from the backend's unlisted `AnchoredStoredVWAP` entry. Mirrored by
// `views/preferences.py`'s `ANCHORED_VWAP_SOURCES` (`test_anchored_vwap_sources_mirror_the_frontend`).
export const ANCHORED_VWAP_SOURCES = ["hlc3", "close", "ohlc4", "stored"] as const;
export type AnchoredVwapSource = (typeof ANCHORED_VWAP_SOURCES)[number];
/** The source whose line the server computes (`AnchoredStoredVWAP`); it has no bands. */
export const STORED_VWAP_SOURCE = "stored" satisfies AnchoredVwapSource;

/** The price a bar contributes to the average. */
export function sourcePrice(bar: TimedBar, source: VwapSource): number {
  switch (source) {
    case "close":
      return bar.close;
    case "ohlc4":
      return (bar.open + bar.high + bar.low + bar.close) / 4;
    case "hlc3":
      return (bar.high + bar.low + bar.close) / 3;
  }
}

/**
 * The volume-weighted standard deviation from the running sums: Σv (`sumV`), Σv·s (`sumVS`) and
 * Σv·s² (`sumVS2`). `Σ v·(s − vwap)² / Σ v = Σv·s²/Σv − vwap²`. Clamped at zero: rounding can leave
 * the variance a hair negative, which `sqrt` would turn into NaN.
 */
export function weightedStdDevFromSums(sumV: number, sumVS: number, sumVS2: number): number {
  if (!(sumV > 0)) return 0;
  const mean = sumVS / sumV;
  return Math.sqrt(Math.max(0, sumVS2 / sumV - mean * mean));
}

/** The volume-weighted standard deviation of `values` under `weights` (0 for an empty or zero-weight set). */
export function volumeWeightedStdDev(values: readonly number[], weights: readonly number[]): number {
  let sumV = 0;
  let sumVS = 0;
  let sumVS2 = 0;
  values.forEach((s, i) => {
    const v = weights[i];
    sumV += v;
    sumVS += v * s;
    sumVS2 += v * s * s;
  });
  return weightedStdDevFromSums(sumV, sumVS, sumVS2);
}

export interface VwapPoint {
  time: number;
  vwap: number;
  /** One volume-weighted standard deviation of the source around `vwap` up to this bar. */
  sd: number;
  upper1: number;
  lower1: number;
  upper2: number;
  lower2: number;
  /** A gap slot (Story 32.1) lies between the previous point and this one: the line breaks here
   * instead of being drawn across the hole (`breakAtGaps`). */
  breakBefore?: boolean;
}

/**
 * The anchored VWAP of `bars` (ascending) from the first bar at or after `anchorTime`. A bar with
 * no usable value (a zero or non-finite volume, a non-finite price) contributes nothing and gets no
 * point, so the line is undefined until the first volume and never carries a NaN.
 */
export function anchoredVwap(bars: readonly TimedBar[], anchorTime: number, source: VwapSource): VwapPoint[] {
  const points: VwapPoint[] = [];
  let sumV = 0;
  let sumVS = 0;
  let sumVS2 = 0;
  for (const bar of bars) {
    if (bar.time < anchorTime) continue;
    const s = sourcePrice(bar, source);
    if (!(bar.volume > 0) || !Number.isFinite(bar.volume) || !Number.isFinite(s)) continue;
    sumV += bar.volume;
    sumVS += bar.volume * s;
    sumVS2 += bar.volume * s * s;
    const vwap = sumVS / sumV;
    const sd = weightedStdDevFromSums(sumV, sumVS, sumVS2);
    points.push({
      time: bar.time,
      vwap,
      sd,
      upper1: vwap + sd,
      lower1: vwap - sd,
      upper2: vwap + 2 * sd,
      lower2: vwap - 2 * sd,
    });
  }
  return points;
}

/**
 * `points` with `breakBefore` set on each point that follows a gap slot (a whitespace datum, no
 * `open`) in `chartBars` since the previous point, so the drawn line breaks over a collection hole
 * like an indicator line does, rather than joining its two sides. `chartBars` ascending.
 */
export function breakAtGaps(points: readonly VwapPoint[], chartBars: readonly { time: unknown }[]): VwapPoint[] {
  const out: VwapPoint[] = [];
  let i = 0;
  let gapSeen = false;
  for (const bar of chartBars) {
    if (i >= points.length) break;
    const t = bar.time as number;
    if (t === points[i].time) {
      out.push(gapSeen && i > 0 ? { ...points[i], breakBefore: true } : points[i]);
      gapSeen = false;
      i++;
    } else if (!("open" in bar) && i > 0) {
      gapSeen = true;
    }
  }
  for (; i < points.length; i++) out.push(points[i]);
  return out;
}

/**
 * Story 33.6: the stored-source Anchored VWAP's points from the values the server replayed for it
 * (`AnchoredStoredVWAP`, `Σpv / ΣV` from the anchor, one value per bar: `null`/absent before the
 * anchor, for a bar with no stored flow, and for a gap slot). No maths here: each value is the
 * point, at or after `anchorTime`; a missing value between two points breaks the line
 * (`breakBefore`), like `breakAtGaps`. There are no bands (`sd` 0): the stored columns carry no
 * per-trade prices to spread them.
 */
export function storedVwapPoints(
  values: readonly { time: unknown; value?: number | null }[],
  anchorTime: number,
): VwapPoint[] {
  const points: VwapPoint[] = [];
  let missing = false;
  for (const datum of values) {
    const time = datum.time as number;
    if (time < anchorTime) continue;
    const vwap = datum.value;
    if (typeof vwap !== "number" || !Number.isFinite(vwap)) {
      missing = points.length > 0;
      continue;
    }
    points.push({
      time,
      vwap,
      sd: 0,
      upper1: vwap,
      lower1: vwap,
      upper2: vwap,
      lower2: vwap,
      ...(missing ? { breakBefore: true } : {}),
    });
    missing = false;
  }
  return points;
}
