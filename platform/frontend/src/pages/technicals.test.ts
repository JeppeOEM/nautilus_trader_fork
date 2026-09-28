import { describe, expect, it } from "vitest";

import { buildGroups, reorder } from "./technicals";

const rsi = { name: "RelativeStrengthIndex", params: {}, category: "native" };
const macd = { name: "MovingAverageConvergenceDivergence", params: {}, category: "native" };

describe("buildGroups", () => {
  it("fans a multi-output entry out into adjacent attrs under one group", () => {
    const groups = buildGroups([rsi, macd], {
      "BTC-USD-PERP.DYDX": { "0.value": 55, "1.value": 1.2, "1.signal": 0.9, "1.histogram": 0.3 },
    });
    expect(groups[0].attrs).toEqual(["value"]);
    expect(groups[1].attrs).toEqual(["value", "signal", "histogram"]);
  });

  it("does not mix entries whose index prefixes share digits", () => {
    const entries = Array.from({ length: 11 }, () => rsi);
    const groups = buildGroups(entries, { X: { "1.value": 1, "10.value": 2 } });
    expect(groups[1].attrs).toEqual(["value"]);
    expect(groups[10].attrs).toEqual(["value"]);
    expect(groups[0].attrs).toEqual([]);
  });

  it("gives a CandlePattern column its one value output, whatever the sign (Story 27.7)", () => {
    const pattern = { name: "CandlePattern", params: { pattern: "HAMMER" }, category: "native", bar_seconds: 60 };
    const groups = buildGroups([rsi, pattern], {
      "BTC-USD-PERP.DYDX": { "0.value": 55, "1.value": 100 },
      "ETH-USD-PERP.DYDX": { "0.value": 40, "1.value": -100 },
      "SOL-USD-PERP.DYDX": { "0.value": 50, "1.value": 0 },
    });
    expect(groups[1]).toEqual({ entryIndex: 1, entry: pattern, attrs: ["value"] });
  });

  it("has no attrs before any values arrive", () => {
    expect(buildGroups([rsi], undefined)[0].attrs).toEqual([]);
  });
});

describe("reorder", () => {
  it("moves an item and leaves the input untouched", () => {
    const input = ["a", "b", "c"];
    expect(reorder(input, 0, 2)).toEqual(["b", "c", "a"]);
    expect(input).toEqual(["a", "b", "c"]);
  });

  it("is a no-op for identical or out-of-range indices", () => {
    const input = ["a", "b"];
    expect(reorder(input, 1, 1)).toBe(input);
    expect(reorder(input, 0, 5)).toBe(input);
  });
});
