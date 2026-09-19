import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum, VolumeDatum } from "../hooks/useCandles";
import { buildVolumeProfile, joinCandlesWithVolume, type ProfileCandle } from "./volumeProfile";

const c = (open: number, high: number, low: number, close: number, volume: number): ProfileCandle => ({
  open,
  high,
  low,
  close,
  volume,
});

describe("buildVolumeProfile (Story 18.5)", () => {
  it("buckets the price range into equal rows, low -> high", () => {
    const p = buildVolumeProfile([c(10, 20, 10, 15, 4)], 5);

    expect(p.rows).toHaveLength(5);
    expect(p.rows[0].priceLow).toBe(10);
    expect(p.rows[4].priceHigh).toBe(20);
    expect(p.rows.map((r) => r.priceHigh - r.priceLow)).toEqual([2, 2, 2, 2, 2]);
  });

  it("spreads a candle's volume evenly over every row its range touches", () => {
    // Two candles fix the range 0..10 (rows of 2.5); the third spans rows 1..2 only.
    const p = buildVolumeProfile([c(0, 0.1, 0, 0.1, 1), c(10, 10, 9.9, 10, 1), c(3, 6, 3, 4, 10)], 4);

    expect(p.rows[1].upVolume).toBe(5);
    expect(p.rows[2].upVolume).toBe(5);
    expect(p.rows[0].upVolume).toBe(1);
    expect(p.rows[3].upVolume).toBe(1);
  });

  it("classifies up (close >= open, including doji) and down volume separately", () => {
    const p = buildVolumeProfile([c(1, 2, 1, 2, 3), c(2, 2, 1, 1, 5), c(1, 2, 1, 1, 7)], 1);

    expect(p.rows[0].upVolume).toBe(10); // 3 + the doji's 7
    expect(p.rows[0].downVolume).toBe(5);
    expect(p.totalVolume).toBe(15);
  });

  it("conserves total volume", () => {
    const p = buildVolumeProfile([c(1, 9, 1, 5, 7), c(4, 8, 3, 3, 11), c(2, 3, 2, 3, 13)], 6);

    expect(p.rows.reduce((s, r) => s + r.upVolume + r.downVolume, 0)).toBeCloseTo(31);
    expect(p.totalVolume).toBeCloseTo(31);
  });

  it("puts the POC at the centre of the fullest row (lowest row on a tie)", () => {
    const p = buildVolumeProfile([c(0, 1, 0, 1, 1), c(4, 5, 4, 5, 9), c(8, 10, 8, 9, 9)], 5);

    // rows of size 2: 0..2 =1, 4..6 = 9, 8..10 = 9 -> tie between row 2 and 4, lowest wins
    expect(p.poc).toBe(5);
  });

  it("grows the value area outward from the POC, fuller neighbour first, until it reaches the target", () => {
    // Range 0..6 -> size-1 rows with volumes 1, 2, 10, 4, 3, 1 (each candle sits in one row).
    const candles = [
      c(0, 0.8, 0, 0.5, 1),
      c(1.2, 1.8, 1.2, 1.5, 2),
      c(2.2, 2.8, 2.2, 2.5, 10),
      c(3.2, 3.8, 3.2, 3.5, 4),
      c(4.2, 4.8, 4.2, 4.5, 3),
      c(5.2, 6, 5.2, 5.5, 1),
    ];
    const p = buildVolumeProfile(candles, 6, 0.7); // total 21, target 14.7

    // POC row 2 (10) -> +row 3 (4) = 14 < 14.7 -> +row 4 (3) = 17 >= 14.7
    expect(p.val).toBeCloseTo(2, 9); // row 2's low edge
    expect(p.vah).toBeCloseTo(5, 9); // row 4's top edge
  });

  it("takes the upper neighbour on a tie and stops at the edges", () => {
    const p = buildVolumeProfile([c(0, 1, 0, 1, 5), c(1, 2, 1, 2, 5), c(2, 3, 2, 3, 5)], 3, 1);

    expect(p.val).toBe(0);
    expect(p.vah).toBe(3);
  });

  it("returns an empty profile for a row count below 1 or NaN instead of throwing", () => {
    expect(buildVolumeProfile([c(1, 2, 1, 2, 1)], 0.5).rows).toEqual([]);
    expect(buildVolumeProfile([c(1, 2, 1, 2, 1)], Number.NaN).rows).toEqual([]);
  });

  it("credits a high sitting exactly on a row boundary only to the row below", () => {
    // Range 0..2 -> rows [0,1) [1,2]; the first candle's high is exactly 1.
    const p = buildVolumeProfile([c(0, 1, 0, 1, 6), c(1.5, 2, 1.5, 2, 1)], 2);

    expect(p.rows[0].upVolume).toBe(6);
    expect(p.rows[1].upVolume).toBe(1);
  });

  it("caps huge row counts, skips non-finite candles and orders a low > high candle", () => {
    expect(buildVolumeProfile([c(1, 2, 1, 2, 1)], 1e9).rows).toHaveLength(500);
    expect(buildVolumeProfile([c(Number.NaN, 2, 1, 2, 1), c(1, 2, 1, 2, Infinity)], 5).rows).toEqual([]);

    const swapped = buildVolumeProfile([c(1, 1, 3, 2, 4)], 2); // low 3 > high 1
    expect(swapped.totalVolume).toBe(4);
    expect(swapped.rows[0].priceLow).toBe(1);
  });

  it("falls back to 70% for a NaN value-area percentage", () => {
    const candles = [c(0, 1, 0, 1, 5), c(1, 2, 1, 2, 5), c(2, 3, 2, 3, 5)];

    expect(buildVolumeProfile(candles, 3, Number.NaN)).toEqual(buildVolumeProfile(candles, 3, 0.7));
  });

  it("handles a very large slice without exceeding argument limits", () => {
    const many = Array.from({ length: 300_000 }, (_, i) => c(i, i + 1, i, i + 1, 1));

    expect(buildVolumeProfile(many, 10).totalVolume).toBeCloseTo(300_000);
  });

  it("returns an empty profile for no usable candles, and one row for a flat range", () => {
    expect(buildVolumeProfile([], 10).rows).toEqual([]);
    expect(buildVolumeProfile([c(1, 2, 1, 2, 0)], 10).totalVolume).toBe(0);

    const flat = buildVolumeProfile([c(5, 5, 5, 5, 3)], 10);
    expect(flat.rows).toHaveLength(1);
    expect(flat.poc).toBe(5);
    expect(flat.totalVolume).toBe(3);
  });
});

describe("joinCandlesWithVolume", () => {
  const t = (n: number): Time => n as Time;

  it("pairs by time and drops gap entries on either side", () => {
    const candles: ChartDatum[] = [
      { time: t(1), open: 1, high: 2, low: 1, close: 2 },
      { time: t(2) },
      { time: t(3), open: 2, high: 3, low: 2, close: 3 },
      { time: t(4), open: 2, high: 3, low: 2, close: 3 },
    ];
    const volume: VolumeDatum[] = [{ time: t(1), value: 10 }, { time: t(2) }, { time: t(3), value: 30 }, { time: t(4) }];

    expect(joinCandlesWithVolume(candles, volume).map((x) => x.volume)).toEqual([10, 30]);
  });
});
