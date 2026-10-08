import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum } from "../hooks/useCandles";
import { heikinAshi, heikinAshiNext } from "./heikinAshi";

const t = (n: number): Time => n as Time;
const bar = (time: number, open: number, high: number, low: number, close: number): ChartDatum => ({
  time: t(time),
  open,
  high,
  low,
  close,
});

describe("heikinAshi (hand-computed)", () => {
  it("chains each bar's open on the previous HA bar", () => {
    // bar0: haC = (10+12+9+11)/4 = 10.5, haO = (10+11)/2 = 10.5, haH = 12, haL = 9.
    // bar1: haC = (11+13+10+12)/4 = 11.5, haO = (10.5+10.5)/2 = 10.5, haH = 13, haL = 10.
    expect(heikinAshi([bar(60, 10, 12, 9, 11), bar(120, 11, 13, 10, 12)])).toEqual([
      { time: 60, open: 10.5, high: 12, low: 9, close: 10.5 },
      { time: 120, open: 10.5, high: 13, low: 10, close: 11.5 },
    ]);
  });

  it("keeps whitespace and restarts the chain after a gap", () => {
    const out = heikinAshi([bar(60, 10, 12, 9, 11), { time: t(120) }, bar(180, 20, 24, 18, 22)]);

    expect(out[1]).toEqual({ time: 120 });
    // After the gap haO is the bar's own (o+c)/2 = 21, never bridged from the bar before it.
    expect(out[2]).toEqual({ time: 180, open: 21, high: 24, low: 18, close: 21 });
  });

  it("widens high and low to the HA open and close", () => {
    // prev HA (open 30, close 10) -> haO = 20, above this bar's real high of 15.
    const next = heikinAshiNext({ time: t(0), open: 30, high: 30, low: 10, close: 10 }, { time: t(60), open: 12, high: 15, low: 11, close: 14 });

    expect(next).toEqual({ time: 60, open: 20, high: 20, low: 11, close: 13 });
  });

  it("recomputes only the forming bar on a live update, from the last closed HA bar", () => {
    const closed = heikinAshi([bar(60, 10, 12, 9, 11), bar(120, 11, 13, 10, 12)]);
    const prev = closed[1] as { time: Time; open: number; high: number; low: number; close: number };

    // haC = (12+14+11+13)/4 = 12.5, haO = (10.5+11.5)/2 = 11.
    expect(heikinAshiNext(prev, { time: t(180), open: 12, high: 14, low: 11, close: 13 })).toEqual({
      time: 180,
      open: 11,
      high: 14,
      low: 11,
      close: 12.5,
    });
    expect(closed[1]).toEqual({ time: 120, open: 10.5, high: 13, low: 10, close: 11.5 });
  });
});
