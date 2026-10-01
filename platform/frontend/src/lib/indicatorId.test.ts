import { describe, expect, it } from "vitest";

import { entryId, indicatorId, splitSeriesKey } from "./indicatorId";
import { outputStyle } from "./indicatorStyle";

// The ids below are what `views/indicator_picker.py`'s `indicator_id` produces for the same
// inputs (its own tests pin the Python side), so a drift in either spelling fails here.
describe("indicatorId mirrors the backend's indicator_id", () => {
  it("is the bare name without params, name_k=v pairs sorted by key otherwise", () => {
    expect(indicatorId("CancelPressure", {})).toBe("CancelPressure");
    expect(indicatorId("CancelPressure", undefined)).toBe("CancelPressure");
    expect(indicatorId("SimpleMovingAverage", { period: 20 })).toBe("SimpleMovingAverage_period=20");
    expect(indicatorId("CandlePattern", { trend_bars: 3, pattern: "ENGULFING" })).toBe(
      "CandlePattern_pattern=ENGULFING,trend_bars=3",
    );
  });

  it("spells a boolean like Python's str() and keeps a fractional float", () => {
    expect(indicatorId("X", { on: true, off: false, k: 2.5 })).toBe("X_k=2.5,off=False,on=True");
  });

  it("adds the source only when it is not close, so every default id is unchanged", () => {
    expect(indicatorId("SimpleMovingAverage", { period: 20 }, "close")).toBe("SimpleMovingAverage_period=20");
    expect(indicatorId("SimpleMovingAverage", { period: 20 }, undefined)).toBe("SimpleMovingAverage_period=20");
    expect(indicatorId("SimpleMovingAverage", { period: 20 }, "hl2")).toBe("SimpleMovingAverage_period=20:hl2");
    expect(indicatorId("X", {}, "ohlc4")).toBe("X:ohlc4");
  });

  it("gives SMA(20) on close and on hl2 two different instances", () => {
    const close = { name: "SimpleMovingAverage", params: { period: 20 }, category: "native" };
    expect(entryId(close)).not.toBe(entryId({ ...close, source: "hl2" }));
  });

  it("splits a series key at its LAST dot, because a param value may hold dots", () => {
    expect(splitSeriesKey("BollingerBands_k=2.5,period=20.upper")).toEqual({
      id: "BollingerBands_k=2.5,period=20",
      output: "upper",
    });
    expect(splitSeriesKey("SimpleMovingAverage_period=20:hl2.value")).toEqual({
      id: "SimpleMovingAverage_period=20:hl2",
      output: "value",
    });
  });
});

describe("outputStyle", () => {
  const entry = (style: Record<string, Record<string, unknown>>) => ({ name: "X", category: "native", style });

  it("is empty for an entry with no style (a pre-story file): the pane palette default", () => {
    expect(outputStyle({ name: "X", category: "native" }, "value")).toEqual({});
    expect(outputStyle(undefined, "value")).toEqual({});
  });

  it("reads the stored fields of one output", () => {
    expect(outputStyle(entry({ value: { color: "#112233", line_width: 3, line_style: "dashed" } }), "value")).toEqual({
      color: "#112233",
      line_width: 3,
      line_style: "dashed",
    });
  });

  it("drops a value the chart cannot draw rather than passing it on, and clamps a width to 1..4", () => {
    expect(outputStyle(entry({ value: { color: 5, line_width: "9", line_style: "wavy" } }), "value")).toEqual({});
    expect(outputStyle(entry({ value: { line_width: 9 } }), "value")).toEqual({ line_width: 4 });
    expect(outputStyle(entry({ value: { line_width: 0.2 } }), "value")).toEqual({ line_width: 1 });
  });
});
