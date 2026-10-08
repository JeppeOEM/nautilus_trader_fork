import { describe, expect, it } from "vitest";

import { MIN_DELTA_ALPHA, volumeBarColor, withAlpha } from "./volumeColor";

const UP = "#25a399";
const DOWN = "#ef5350";
const NEUTRAL = "#2962ff";
const color = (bar: Parameters<typeof volumeBarColor>[0], mode: "direction" | "delta") =>
  volumeBarColor(bar, mode, UP, DOWN, NEUTRAL);

describe("volumeBarColor by direction (Story 33.6)", () => {
  it("is up when the close is at or above the open, else down", () => {
    expect(color({ o: 10, c: 11 }, "direction")).toBe(UP);
    expect(color({ o: 10, c: 10 }, "direction")).toBe(UP);
    expect(color({ o: 10, c: 9 }, "direction")).toBe(DOWN);
  });

  it("is neutral for a bar without an open and a close", () => {
    expect(color({}, "direction")).toBe(NEUTRAL);
    expect(color({ o: null, c: 9 }, "direction")).toBe(NEUTRAL);
  });
});

describe("volumeBarColor by delta (Story 33.6)", () => {
  it("takes the sign of buy - sell, shaded by |delta| / volume", () => {
    // 70 vs 30: delta 40 of 100 -> 0.4.
    expect(color({ o: 10, c: 9, buy_v: 70, sell_v: 30 }, "delta")).toBe("rgba(37, 163, 153, 0.4)");
    expect(color({ o: 9, c: 10, buy_v: 0, sell_v: 50 }, "delta")).toBe("rgba(239, 83, 80, 1)");
  });

  it("never shades a one-sided-enough bar below the floor", () => {
    // 51 vs 49: 0.02, floored.
    expect(color({ buy_v: 51, sell_v: 49 }, "delta")).toBe(`rgba(37, 163, 153, ${MIN_DELTA_ALPHA})`);
  });

  it("is neutral for unknown flow and a bar that traded nothing", () => {
    expect(color({ o: 1, c: 2, buy_v: null, sell_v: null }, "delta")).toBe(NEUTRAL);
    expect(color({ o: 1, c: 2 }, "delta")).toBe(NEUTRAL);
    expect(color({ buy_v: 0, sell_v: 0 }, "delta")).toBe(NEUTRAL);
  });

  it("shades an exactly balanced bar neutral at the floor, the lightest bar, never the boldest", () => {
    expect(color({ buy_v: 5, sell_v: 5 }, "delta")).toBe(`rgba(41, 98, 255, ${MIN_DELTA_ALPHA})`);
  });
});

describe("withAlpha", () => {
  it("reads #rrggbb, #rgb and rgb()", () => {
    expect(withAlpha("#ff0000", 0.5)).toBe("rgba(255, 0, 0, 0.5)");
    expect(withAlpha("#0f0", 0.25)).toBe("rgba(0, 255, 0, 0.25)");
    expect(withAlpha("rgb(1, 2, 3)", 1)).toBe("rgba(1, 2, 3, 1)");
  });

  it("multiplies an rgba() colour's own alpha", () => {
    expect(withAlpha("rgba(38, 166, 154, 0.5)", 0.4)).toBe("rgba(38, 166, 154, 0.2)");
    expect(withAlpha("rgba(38, 166, 154, 0.5)", 1)).toBe("rgba(38, 166, 154, 0.5)");
  });

  it("returns any other colour form unshaded", () => {
    expect(withAlpha("teal", 0.5)).toBe("teal");
  });
});
