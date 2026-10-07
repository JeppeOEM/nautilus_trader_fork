import type { Time } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChartDatum } from "../hooks/useCandles";
import { COMPARE_PALETTE, MAX_INSTRUMENT_ID_LENGTH, alignCompare, spreadBps, validateCompareInput, withCompareAdded } from "./compare";

const t = (n: number): Time => n as Time;
const candle = (time: number, close: number): ChartDatum => ({ time: t(time), open: close, high: close, low: close, close });

let errors: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  errors = vi.spyOn(console, "error").mockImplementation(() => {});
});
afterEach(() => {
  errors.mockRestore();
});

describe("alignCompare", () => {
  it("keeps the main times, whitespace where the compare has no bar, and ignores its extra times", () => {
    const compare = [candle(60, 5), candle(180, 7), candle(240, 9)];

    expect(alignCompare([60, 120, 180], compare)).toEqual([{ time: 60, value: 5 }, { time: 120 }, { time: 180, value: 7 }]);
  });

  it("turns a compare gap bar into whitespace, never a carried value", () => {
    expect(alignCompare([60, 120], [candle(60, 5), { time: t(120) }])).toEqual([{ time: 60, value: 5 }, { time: 120 }]);
  });

  it("takes the compare's forming bar at its own time", () => {
    expect(alignCompare([60, 120], [candle(60, 5)], { time: t(120), close: 6 })).toEqual([
      { time: 60, value: 5 },
      { time: 120, value: 6 },
    ]);
  });

  it("skips a held forming bar older than the compare's newest bar, never overwriting a closed close", () => {
    expect(alignCompare([60, 120], [candle(60, 5), candle(120, 7)], { time: t(60), close: 4 })).toEqual([
      { time: 60, value: 5 },
      { time: 120, value: 7 },
    ]);
    expect(alignCompare([60, 120], [candle(60, 5), candle(120, 7)], { time: t(120), close: 8 })).toEqual([
      { time: 60, value: 5 },
      { time: 120, value: 8 },
    ]);
  });
});

describe("spreadBps", () => {
  it("is (a / b - 1) * 1e4: main 101 over compare 100 is 100 bps (the shared basis_bps fixture)", () => {
    const [point] = spreadBps([{ time: t(60), value: 101 }], [{ time: t(60), value: 100 }]);
    expect("value" in point && point.value).toBeCloseTo(100, 9);
  });

  it("is whitespace where either side is", () => {
    expect(spreadBps([{ time: t(60) }, { time: t(120), value: 1 }], [{ time: t(60), value: 1 }, { time: t(120) }])).toEqual([
      { time: 60 },
      { time: 120 },
    ]);
  });

  it("refuses b <= 0 or a non-finite value as whitespace and a console error naming the bar", () => {
    const out = spreadBps(
      [
        { time: t(60), value: 1 },
        { time: t(120), value: Number.NaN },
      ],
      [
        { time: t(60), value: 0 },
        { time: t(120), value: 1 },
      ],
    );

    expect(out).toEqual([{ time: 60 }, { time: 120 }]);
    expect(errors).toHaveBeenCalledTimes(2);
    expect(String(errors.mock.calls[0][0])).toContain("bar 60");
  });
});

describe("validateCompareInput", () => {
  const MAIN = "BTCUSDT-LINEAR.BYBIT";

  it("accepts a trimmed id with a venue suffix", () => {
    expect(validateCompareInput("  BTC-USD-PERP.HYPERLIQUID ", MAIN, [])).toEqual({ ok: true, iid: "BTC-USD-PERP.HYPERLIQUID" });
  });

  it.each([
    ["BTCUSDT", [], "suffix"],
    ["BTC.", [], "suffix"],
    [".BYBIT", [], "suffix"],
    [MAIN, [], "own instrument"],
    ["A.BYBIT", ["A.BYBIT"], "already compared"],
    ["D.BYBIT", ["A.BYBIT", "B.BYBIT", "C.BYBIT"], "At most 3"],
    [`${"A".repeat(MAX_INSTRUMENT_ID_LENGTH - 1)}.X`, [], `at most ${MAX_INSTRUMENT_ID_LENGTH} characters`],
  ])("refuses %s naming why", (text, current, reason) => {
    const result = validateCompareInput(text, MAIN, current);
    expect(result.ok).toBe(false);
    expect(!result.ok && result.reason).toContain(reason);
  });

  it("accepts an id of exactly the maximum length", () => {
    const iid = `${"A".repeat(MAX_INSTRUMENT_ID_LENGTH - 2)}.X`;
    expect(validateCompareInput(iid, MAIN, [])).toEqual({ ok: true, iid });
  });

  it("has one palette colour per compare slot", () => {
    expect(COMPARE_PALETTE).toEqual(["--chart-compare-1", "--chart-compare-2", "--chart-compare-3"]);
  });
});

describe("withCompareAdded", () => {
  const MAIN = "BTCUSDT-LINEAR.BYBIT";

  it("appends a valid id", () => {
    expect(withCompareAdded({ symbols: ["A.BYBIT"], spread: true }, "B.BYBIT", MAIN)).toEqual({
      symbols: ["A.BYBIT", "B.BYBIT"],
      spread: true,
    });
  });

  it("re-checks the latest state: a repeated add or a fourth one leaves it unchanged", () => {
    const once = withCompareAdded({ symbols: [], spread: false }, "A.BYBIT", MAIN);
    expect(withCompareAdded(once, "A.BYBIT", MAIN)).toBe(once);
    const full = { symbols: ["A.BYBIT", "B.BYBIT", "C.BYBIT"], spread: false };
    expect(withCompareAdded(full, "D.BYBIT", MAIN)).toBe(full);
    expect(withCompareAdded(full, MAIN, MAIN)).toBe(full);
  });
});
