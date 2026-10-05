import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum, VolumeDatum } from "../../../hooks/useCandles";
import { gapRun } from "../../../lib/gaps";
import { buildMeasurementIndex, computeMeasurement, formatMeasurement, labelOrigin } from "./MeasurementPrimitive";

const t = (n: number): Time => n as Time;
const candle = (n: number): ChartDatum => ({ time: t(n), open: 1, high: 2, low: 0.5, close: 1.5 });

describe("computeMeasurement (Story 18.3)", () => {
  const candles: ChartDatum[] = [candle(60), candle(120), { time: t(180) }, candle(240), candle(300)];
  const volume: VolumeDatum[] = [
    { time: t(60), value: 10 },
    { time: t(120), value: 20 },
    { time: t(180) },
    { time: t(240), value: 30 },
    { time: t(300), value: 40 },
  ];
  const index = buildMeasurementIndex(candles, volume);

  it("computes delta, percent and bar count, skipping gap bars", () => {
    const m = computeMeasurement({ time: t(60), price: 100 }, { time: t(240), price: 110 }, index);

    expect(m.priceDelta).toBe(10);
    expect(m.priceDeltaPct).toBe(10);
    expect(m.bars).toBe(3); // 60, 120, 240 -- the 180 whitespace entry is not a bar
  });

  it("counts and sums nothing for a 720-slot gap run: equal to the run-free input (Story 32.1)", () => {
    const slots = gapRun(60, 86_400, 60).map((n) => ({ time: t(n) }));
    const plainCandles = [candle(60), candle(86_400)];
    const plainVolume: VolumeDatum[] = [{ time: t(60), value: 10 }, { time: t(86_400), value: 20 }];
    const start = { time: t(60), price: 100 };
    const end = { time: t(86_400), price: 90 };

    const holedIndex = buildMeasurementIndex(
      [plainCandles[0], ...slots, plainCandles[1]],
      [plainVolume[0], ...slots, plainVolume[1]],
    );
    const holed = computeMeasurement(start, end, holedIndex);

    expect(slots).toHaveLength(720);
    expect(holed).toEqual(computeMeasurement(start, end, buildMeasurementIndex(plainCandles, plainVolume)));
    expect(holed.bars).toBe(2);
  });

  it("sums volume over the range, skipping gap entries", () => {
    const m = computeMeasurement({ time: t(60), price: 100 }, { time: t(240), price: 110 }, index);

    expect(m.volume).toBe(60);
  });

  it("is direction-independent for range and signed for price", () => {
    const m = computeMeasurement({ time: t(240), price: 110 }, { time: t(120), price: 100 }, index);

    expect(m.bars).toBe(2);
    expect(m.volume).toBe(50);
    expect(m.priceDelta).toBe(-10);
    expect(m.priceDeltaPct).toBeCloseTo(-9.0909, 3);
  });

  it("has an undefined percent, never Infinity, for a zero start price", () => {
    const m = computeMeasurement({ time: t(60), price: 0 }, { time: t(120), price: 5 }, index);

    expect(m.priceDeltaPct).toBeNull();
    expect(formatMeasurement(m)[0]).toBe("+5 (n/a)");
  });

  it("formats sub-cent deltas with significant digits, not fixed decimals", () => {
    const m = computeMeasurement({ time: t(60), price: 0.00001234 }, { time: t(120), price: 0.00001334 }, index);

    expect(formatMeasurement(m)[0]).toContain("+0.000001");
  });

  it("counts a range ending between bars and one outside the data as empty", () => {
    expect(computeMeasurement({ time: t(130), price: 1 }, { time: t(170), price: 1 }, index).bars).toBe(0);
    expect(computeMeasurement({ time: t(400), price: 1 }, { time: t(500), price: 1 }, index)).toMatchObject({ bars: 0, volume: 0 });
  });

  it("sums a short range after a huge history to its own precision, not the history's", () => {
    // Plain running totals: (1e12 + 0.1 + 0.2) - (1e12 + 0.1) is off by ~1e-4 (the totals' ulp).
    const big: VolumeDatum[] = [{ time: t(60), value: 1e12 }, { time: t(120), value: 0.1 }, { time: t(180), value: 0.2 }];
    const m = computeMeasurement(
      { time: t(120), price: 1 },
      { time: t(180), price: 1 },
      buildMeasurementIndex([candle(60), candle(120), candle(180)], big),
    );

    expect(m.volume).toBeCloseTo(0.3, 12);
    expect(formatMeasurement(m)[2]).toBe("vol 0.3");
  });
});

describe("computeMeasurement live bar (DW-144)", () => {
  const candles: ChartDatum[] = [candle(60), candle(120)];
  const volume: VolumeDatum[] = [{ time: t(60), value: 10 }, { time: t(120), value: 20 }];
  const live = { time: t(180), volume: 5 };

  it("counts the forming bar and its volume once the range reaches it", () => {
    const m = computeMeasurement({ time: t(60), price: 1 }, { time: t(180), price: 1 }, buildMeasurementIndex(candles, volume), live);

    expect(m.bars).toBe(3);
    expect(m.volume).toBe(35);
  });

  it("does not double-count a live bar already promoted into the arrays", () => {
    const promoted = { time: t(120), volume: 99 };
    const index = buildMeasurementIndex(candles, volume);

    expect(computeMeasurement({ time: t(60), price: 1 }, { time: t(180), price: 1 }, index, promoted)).toMatchObject({ bars: 2, volume: 30 });
  });

  it("judges bars and volume apart: volume one entry behind still takes the live volume", () => {
    const lagging = buildMeasurementIndex([...candles, candle(180)], volume);

    expect(computeMeasurement({ time: t(60), price: 1 }, { time: t(180), price: 1 }, lagging, live)).toMatchObject({ bars: 3, volume: 35 });
  });

  it("leaves the live bar out of a range that ends before it", () => {
    const m = computeMeasurement({ time: t(60), price: 1 }, { time: t(120), price: 1 }, buildMeasurementIndex(candles, volume), live);

    expect(m).toMatchObject({ bars: 2, volume: 30 });
  });

  it("counts a live bar on empty arrays", () => {
    const m = computeMeasurement({ time: t(0), price: 1 }, { time: t(200), price: 1 }, buildMeasurementIndex([], []), live);

    expect(m).toMatchObject({ bars: 1, volume: 5 });
  });
});

describe("labelOrigin (DW-144)", () => {
  it("sits inside the rectangle's top-left corner when it fits", () => {
    expect(labelOrigin({ x1: 100, y1: 50, x2: 200, y2: 150 }, 80, 40, 800, 500)).toEqual({ x: 104, y: 54 });
  });

  it("is pushed back inside the pane at the right and bottom edges", () => {
    const origin = labelOrigin({ x1: 790, y1: 490, x2: 760, y2: 480 }, 80, 40, 800, 500);

    expect(origin).toEqual({ x: 716, y: 456 });
  });

  it("is pulled inside when the rectangle starts off the left/top", () => {
    expect(labelOrigin({ x1: -50, y1: -20, x2: 100, y2: 100 }, 80, 40, 800, 500)).toEqual({ x: 4, y: 4 });
  });

  it("pins to the top-left in a pane smaller than the text", () => {
    expect(labelOrigin({ x1: 10, y1: 10, x2: 20, y2: 20 }, 300, 200, 100, 100)).toEqual({ x: 0, y: 0 });
  });
});
