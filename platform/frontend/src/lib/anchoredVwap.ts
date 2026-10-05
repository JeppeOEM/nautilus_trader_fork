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
 */

export const VWAP_SOURCES = ["hlc3", "close", "ohlc4"] as const;
export type VwapSource = (typeof VWAP_SOURCES)[number];
export const DEFAULT_VWAP_SOURCE: VwapSource = "hlc3";

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
