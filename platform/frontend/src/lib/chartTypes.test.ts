import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { ChartDatum } from "../hooks/useCandles";
import {
  CHART_TYPES,
  MAX_COMPARE_SYMBOLS,
  PRICE_SCALE_MODES,
  firstVisibleClose,
  hollowColors,
  liveSeriesRow,
  seriesKindOf,
  seriesRows,
} from "./chartTypes";

const t = (n: number): Time => n as Time;
const COLORS = { up: "UP", down: "DOWN" };
// Up bar, down bar, a gap, then an up bar whose close is under the bar two slots back.
const DATA: ChartDatum[] = [
  { time: t(60), open: 10, high: 12, low: 9, close: 11 },
  { time: t(120), open: 11, high: 13, low: 10, close: 10.5 },
  { time: t(180) },
  { time: t(240), open: 9, high: 11, low: 8, close: 10 },
];

describe("the type and mode sets (views/preferences.py mirrors them)", () => {
  it("lists the seven types, four modes and the compare cap", () => {
    expect(CHART_TYPES).toEqual(["candles", "hollow", "bars", "line", "area", "baseline", "heikin_ashi"]);
    expect(PRICE_SCALE_MODES).toEqual(["normal", "log", "percent", "indexed"]);
    expect(MAX_COMPARE_SYMBOLS).toBe(3);
  });

  it("draws candle-shaped types on a Candlestick series", () => {
    expect(CHART_TYPES.map(seriesKindOf)).toEqual(["Candlestick", "Candlestick", "Bar", "Line", "Area", "Baseline", "Candlestick"]);
  });
});

describe("seriesRows on one fixture", () => {
  it("passes OHLC through for candles and bars, whitespace kept", () => {
    for (const type of ["candles", "bars"] as const) expect(seriesRows(type, DATA, COLORS)).toEqual(DATA);
  });

  it("maps line, area and baseline to the close", () => {
    for (const type of ["line", "area", "baseline"] as const) {
      expect(seriesRows(type, DATA, COLORS)).toEqual([
        { time: 60, value: 11 },
        { time: 120, value: 10.5 },
        { time: 180 },
        { time: 240, value: 10 },
      ]);
    }
  });

  it("colours hollow bars by the previous close and fills only the falling bodies", () => {
    const rows = seriesRows("hollow", DATA, COLORS);
    // Bar 0: no previous bar, close 11 >= own open 10 -> up, hollow.
    expect(rows[0]).toMatchObject({ color: "transparent", borderColor: "UP", wickColor: "UP" });
    // Bar 1: close 10.5 < open 11 -> filled; 10.5 < previous close 11 -> down.
    expect(rows[1]).toMatchObject({ color: "DOWN", borderColor: "DOWN" });
    expect(rows[2]).toEqual({ time: 180 });
    // Bar 3, first after the gap: close 10 >= own open 9 -> up, hollow.
    expect(rows[3]).toMatchObject({ open: 9, close: 10, color: "transparent", borderColor: "UP" });
  });

  it("is Heikin Ashi for heikin_ashi, the gap restarting the chain", () => {
    const rows = seriesRows("heikin_ashi", DATA, COLORS);
    expect(rows[0]).toEqual({ time: 60, open: 10.5, high: 12, low: 9, close: 10.5 });
    expect(rows[3]).toEqual({ time: 240, open: 9.5, high: 11, low: 8, close: 9.5 });
  });

  it("never changes the real data it reads", () => {
    const copy = structuredClone(DATA);
    for (const type of CHART_TYPES) seriesRows(type, DATA, COLORS);
    expect(DATA).toEqual(copy);
  });
});

describe("hollowColors", () => {
  it("is up and filled for a falling body above the previous close", () => {
    expect(hollowColors(8, { time: t(0), open: 12, high: 12, low: 9, close: 10 }, COLORS)).toEqual({
      color: "UP",
      borderColor: "UP",
      wickColor: "UP",
    });
  });

  it("is down and hollow for a rising body under the previous close", () => {
    expect(hollowColors(20, { time: t(0), open: 9, high: 12, low: 9, close: 10 }, COLORS)).toEqual({
      color: "transparent",
      borderColor: "DOWN",
      wickColor: "DOWN",
    });
  });
});

describe("liveSeriesRow", () => {
  const live = { time: t(300), open: 10, high: 12, low: 9, close: 11 };

  it("chains the forming HA bar on the last closed HA bar", () => {
    const rows = seriesRows("heikin_ashi", DATA, COLORS);
    // prev HA (9.5, 9.5): haO = 9.5, haC = (10+12+9+11)/4 = 10.5.
    expect(liveSeriesRow("heikin_ashi", DATA, rows, live, COLORS)).toEqual({ time: 300, open: 9.5, high: 12, low: 9, close: 10.5 });
  });

  it("colours a hollow forming bar against the previous real close and draws lines at the close", () => {
    expect(liveSeriesRow("hollow", DATA, seriesRows("hollow", DATA, COLORS), live, COLORS)).toMatchObject({ borderColor: "UP" });
    expect(liveSeriesRow("line", DATA, [], live, COLORS)).toEqual({ time: 300, value: 11 });
    expect(liveSeriesRow("bars", DATA, [], live, COLORS)).toEqual(live);
  });

  it("restarts after a trailing gap slot", () => {
    const gapped: ChartDatum[] = [...DATA, { time: t(300) }];
    const next = { ...live, time: t(360) };
    expect(liveSeriesRow("heikin_ashi", gapped, seriesRows("heikin_ashi", gapped, COLORS), next, COLORS)).toMatchObject({ open: 10.5 });
  });
});

describe("firstVisibleClose", () => {
  it("is the first real close at or after the visible range's left edge time", () => {
    expect(firstVisibleClose(DATA, 121)).toBe(10); // 180 is a gap: the next real bar
    expect(firstVisibleClose(DATA, 120)).toBe(10.5);
    expect(firstVisibleClose(DATA, 0)).toBe(11);
    expect(firstVisibleClose(DATA, 241)).toBeNull();
    expect(firstVisibleClose([], 0)).toBeNull();
  });

  it("finds the bar by time, not by a logical index another series' extra times would shift", () => {
    // A compare adding times 90 and 150 makes chart logical index 2 the time 120, data index 1.
    expect(firstVisibleClose(DATA, 120)).toBe(10.5);
  });
});
