import { describe, expect, it } from "vitest";

import {
  AUTO_ANCHOR_PRESETS,
  AUTO_PRESET_BY_BAR_SIZE,
  type AnchorBar,
  anchorBars,
  anchorTime,
  autoPresetFor,
} from "./autoAnchor";

const DAY = 86_400;
// Wednesday 2024-01-03 00:00 UTC; its week starts Monday 2024-01-01, its month 2024-01-01.
const WED = Date.UTC(2024, 0, 3) / 1000;
const MON = Date.UTC(2024, 0, 1) / 1000;
const bar = (time: number, high = 10, low = 5): AnchorBar => ({ time, high, low });
// Three sessions of 5-minute bars, a few per day.
const threeSessions = [
  bar(WED - DAY), bar(WED - DAY + 300),
  bar(WED), bar(WED + 300),
  bar(WED + DAY), bar(WED + DAY + 300),
];

describe("autoPresetFor (Story 32.7: the `auto` table)", () => {
  it("is a session up to 15m, a week up to 4H and a month above, at the boundaries", () => {
    expect([60, 300, 900].map(autoPresetFor)).toEqual(["session", "session", "session"]);
    expect([901, 3600, 14_400].map(autoPresetFor)).toEqual(["week", "week", "week"]);
    expect([14_401, 86_400, 604_800].map(autoPresetFor)).toEqual(["month", "month", "month"]);
  });

  it("is a named table, ascending, ending in an unbounded row", () => {
    const bounds = AUTO_PRESET_BY_BAR_SIZE.map((r) => r.maxBarSeconds);
    expect([...bounds].sort((a, b) => a - b)).toEqual(bounds);
    expect(bounds.at(-1)).toBe(Number.POSITIVE_INFINITY);
  });

  it("never resolves to `auto` itself", () => {
    expect(AUTO_ANCHOR_PRESETS).toContain("auto");
    expect(AUTO_PRESET_BY_BAR_SIZE.map((r) => r.preset)).not.toContain("auto");
  });
});

describe("anchorTime (Story 32.7)", () => {
  it("auto at 5m: the start of the CURRENT session (UTC day of the latest bar)", () => {
    const a = anchorTime("auto", threeSessions, 300);

    expect(a).toEqual({ preset: "session", time: WED + DAY, period: "daily" });
  });

  it("auto at 1D: the month start", () => {
    const a = anchorTime("auto", [bar(WED), bar(WED + DAY)], 86_400);

    expect(a).toEqual({ preset: "month", time: MON, period: "monthly" });
  });

  it("week: Monday 00:00 UTC of the latest bar's week", () => {
    expect(anchorTime("week", threeSessions, 300)).toMatchObject({ preset: "week", time: MON, period: "weekly" });
  });

  it("highest high: the bar with the highest high of the loaded set (the first on a tie)", () => {
    const bars = [bar(1, 10, 5), bar(2, 30, 5), bar(3, 30, 1), bar(4, 12, 5)];

    expect(anchorTime("highest_high", bars, 60)).toEqual({ preset: "highest_high", time: 2, period: null });
  });

  it("lowest low: the bar with the lowest low of the loaded set", () => {
    const bars = [bar(1, 10, 5), bar(2, 30, 4), bar(3, 30, 1), bar(4, 12, 5)];

    expect(anchorTime("lowest_low", bars, 60)).toEqual({ preset: "lowest_low", time: 3, period: null });
  });

  it("has no anchor without bars", () => {
    expect(anchorTime("auto", [], 60)).toBeNull();
    expect(anchorTime("highest_high", [], 60)).toBeNull();
  });

  it("re-anchors when a live bar crosses the session boundary", () => {
    const before = [bar(WED + DAY - 600), bar(WED + DAY - 300)];
    const after = [...before, bar(WED + DAY)];

    expect(anchorTime("auto", before, 300)!.time).toBe(WED);
    expect(anchorTime("auto", after, 300)!.time).toBe(WED + DAY);
  });

  it("re-anchors by the new bar size's rule on a timeframe change", () => {
    expect(anchorTime("auto", threeSessions, 300)!.preset).toBe("session");
    expect(anchorTime("auto", threeSessions, 3600)!.preset).toBe("week");
    expect(anchorTime("auto", threeSessions, 86_400)!.preset).toBe("month");
  });
});

describe("anchorBars", () => {
  it("keeps real bars and drops whitespace gap slots", () => {
    const candles = [
      { time: 1, open: 1, high: 3, low: 0, close: 2 },
      { time: 2 },
      { time: 3, open: 1, high: 4, low: 1, close: 2 },
    ];

    expect(anchorBars(candles as never)).toEqual([
      { time: 1, high: 3, low: 0 },
      { time: 3, high: 4, low: 1 },
    ]);
  });

  it("skips a bar with a non-finite high or low", () => {
    const candles = [
      { time: 1, open: 1, high: Number.NaN, low: 0, close: 2 },
      { time: 2, open: 1, high: 4, low: Number.POSITIVE_INFINITY, close: 2 },
      { time: 3, open: 1, high: 4, low: 1, close: 2 },
    ];

    expect(anchorBars(candles as never)).toEqual([{ time: 3, high: 4, low: 1 }]);
  });
});
