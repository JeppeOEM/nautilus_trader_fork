import { describe, expect, it } from "vitest";

import {
  anchoredVwap,
  breakAtGaps,
  sourcePrice,
  volumeWeightedStdDev,
  weightedStdDevFromSums,
} from "./anchoredVwap";
import type { TimedBar } from "./sessionProfile";
import { formatDecimal } from "./units";

const bar = (time: number, high: number, low: number, close: number, volume: number, open = close): TimedBar => ({
  time,
  open,
  high,
  low,
  close,
  volume,
});

// hlc3 of each bar: 10, 12, (skipped), 18.
const bars = [bar(100, 12, 8, 10, 2), bar(200, 15, 9, 12, 3), bar(300, 40, 1, 20, 0), bar(400, 21, 15, 18, 5)];

describe("volumeWeightedStdDev (Story 32.7)", () => {
  it("equals the hand computation sum(v (s - mean)^2) / sum(v)", () => {
    // mean = (2*10 + 3*12 + 5*18) / 10 = 14.6; variance = (2*21.16 + 3*6.76 + 5*11.56) / 10 = 12.04
    const sd = volumeWeightedStdDev([10, 12, 18], [2, 3, 5]);

    expect(sd).toBeCloseTo(Math.sqrt(12.04), 12);
  });

  it("is 0 for one value, an empty set and zero weights, and never NaN on rounding noise", () => {
    expect(volumeWeightedStdDev([7], [3])).toBe(0);
    expect(volumeWeightedStdDev([], [])).toBe(0);
    expect(volumeWeightedStdDev([5, 9], [0, 0])).toBe(0);
    // the sums of constant values can leave the variance a hair below zero
    expect(weightedStdDevFromSums(3, 3 * 0.1, 3 * 0.1 * 0.1 - 1e-18)).toBe(0);
  });
});

describe("anchoredVwap (Story 32.7)", () => {
  it("is the cumulative sum(src v) / sum(v) from the anchor bar, with the hand-computed bands", () => {
    const points = anchoredVwap(bars, 100, "hlc3");

    expect(points.map((p) => p.time)).toEqual([100, 200, 400]);
    expect(points[0]).toMatchObject({ vwap: 10, sd: 0, upper1: 10, lower1: 10 });
    // after bar 2: vwap (20 + 36) / 5 = 11.2, variance 126.4 - 125.44 = 0.96
    expect(points[1].vwap).toBeCloseTo(11.2, 12);
    expect(points[1].sd).toBeCloseTo(Math.sqrt(0.96), 12);
    // after bar 4: vwap 146 / 10 = 14.6, variance 12.04
    const last = points[2];
    expect(last.vwap).toBeCloseTo(14.6, 12);
    expect(last.sd).toBeCloseTo(Math.sqrt(12.04), 12);
    expect(last.upper1).toBeCloseTo(14.6 + Math.sqrt(12.04), 12);
    expect(last.lower1).toBeCloseTo(14.6 - Math.sqrt(12.04), 12);
    expect(last.upper2).toBeCloseTo(14.6 + 2 * Math.sqrt(12.04), 12);
    expect(last.lower2).toBeCloseTo(14.6 - 2 * Math.sqrt(12.04), 12);
  });

  it("prints the line and the bands at the instrument precision", () => {
    const last = anchoredVwap(bars, 100, "hlc3")[2];

    expect(formatDecimal(last.vwap, 2)).toBe("14.60");
    expect(formatDecimal(last.upper1, 2)).toBe("18.07");
    expect(formatDecimal(last.lower2, 2)).toBe("7.66");
  });

  it("starts at the anchor: earlier bars do not count", () => {
    const points = anchoredVwap(bars, 200, "hlc3");

    expect(points.map((p) => p.time)).toEqual([200, 400]);
    expect(points[0].vwap).toBe(12);
    expect(points[1].vwap).toBeCloseTo((3 * 12 + 5 * 18) / 8, 12);
  });

  it("leaves no point (and no NaN) for a zero-volume bar, and none until the first volume", () => {
    const points = anchoredVwap([bar(100, 12, 8, 10, 0), bar(200, 12, 8, 10, 0), bar(300, 15, 9, 12, 4)], 100, "hlc3");

    expect(points.map((p) => p.time)).toEqual([300]);
    expect(points.every((p) => Object.values(p).every(Number.isFinite))).toBe(true);
    expect(anchoredVwap([bar(100, 12, 8, 10, 0)], 100, "hlc3")).toEqual([]);
  });

  it("skips a bar with a non-finite price or volume", () => {
    const points = anchoredVwap([bar(100, Number.NaN, 8, 10, 2), bar(200, 12, 8, 10, Number.NaN), bar(300, 12, 8, 10, 2)], 100, "hlc3");

    expect(points.map((p) => p.time)).toEqual([300]);
  });

  it("takes the chosen source: close, hlc3 or ohlc4", () => {
    const b = bar(1, 12, 6, 9, 1, 3); // open 3, high 12, low 6, close 9

    expect(sourcePrice(b, "close")).toBe(9);
    expect(sourcePrice(b, "hlc3")).toBe(9); // (12 + 6 + 9) / 3
    expect(sourcePrice(b, "ohlc4")).toBe(7.5); // (3 + 12 + 6 + 9) / 4
    expect(anchoredVwap([b], 1, "ohlc4")[0].vwap).toBe(7.5);
  });
});

describe("breakAtGaps (Story 32.7)", () => {
  const p = (time: number) => anchoredVwap([bar(time, 12, 8, 10, 1)], time, "hlc3")[0];

  it("marks the first point after a gap slot, and only that one", () => {
    const points = [p(100), p(200), p(500), p(600)];
    const chartBars = [{ time: 100, open: 1 }, { time: 200, open: 1 }, { time: 300 }, { time: 400 }, { time: 500, open: 1 }, { time: 600, open: 1 }];

    expect(breakAtGaps(points, chartBars).map((q) => q.breakBefore === true)).toEqual([false, false, true, false]);
  });

  it("does not break across a real bar that has no point (zero volume), nor before the first point", () => {
    const points = [p(200), p(400)];
    const chartBars = [{ time: 100 }, { time: 200, open: 1 }, { time: 300, open: 1 }, { time: 400, open: 1 }];

    expect(breakAtGaps(points, chartBars).map((q) => q.breakBefore === true)).toEqual([false, false]);
  });
});
