import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum, VolumeDatum } from "../../../hooks/useCandles";
import { computeMeasurement, formatMeasurement } from "./MeasurementPrimitive";

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

  it("computes delta, percent and bar count, skipping gap bars", () => {
    const m = computeMeasurement({ time: t(60), price: 100 }, { time: t(240), price: 110 }, candles, volume);

    expect(m.priceDelta).toBe(10);
    expect(m.priceDeltaPct).toBe(10);
    expect(m.bars).toBe(3); // 60, 120, 240 -- the 180 whitespace entry is not a bar
  });

  it("sums volume over the range, skipping gap entries", () => {
    const m = computeMeasurement({ time: t(60), price: 100 }, { time: t(240), price: 110 }, candles, volume);

    expect(m.volume).toBe(60);
  });

  it("is direction-independent for range and signed for price", () => {
    const m = computeMeasurement({ time: t(240), price: 110 }, { time: t(120), price: 100 }, candles, volume);

    expect(m.bars).toBe(2);
    expect(m.volume).toBe(50);
    expect(m.priceDelta).toBe(-10);
    expect(m.priceDeltaPct).toBeCloseTo(-9.0909, 3);
  });

  it("has an undefined percent, never Infinity, for a zero start price", () => {
    const m = computeMeasurement({ time: t(60), price: 0 }, { time: t(120), price: 5 }, candles, volume);

    expect(m.priceDeltaPct).toBeNull();
    expect(formatMeasurement(m)[0]).toBe("+5 (n/a)");
  });

  it("formats sub-cent deltas with significant digits, not fixed decimals", () => {
    const m = computeMeasurement({ time: t(60), price: 0.00001234 }, { time: t(120), price: 0.00001334 }, candles, volume);

    expect(formatMeasurement(m)[0]).toContain("+0.000001");
  });
});
