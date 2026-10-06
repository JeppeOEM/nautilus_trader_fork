import { describe, expect, it, vi } from "vitest";

import {
  MARKER_MAX_RADIUS_PX,
  MARKER_MERGE_COUNT,
  MARKER_MIN_BAR_SPACING_PX,
  MARKER_MIN_RADIUS_PX,
  type LiquidationRow,
  buildLiquidationMarkers,
  markerSize,
  sqrtRadius,
} from "./LiquidationMarkers";

const NS = 1_000_000_000;
const COLORS = { up: "#0a0", down: "#a00" };
const SLOTS = [600, 660, 720];

function liq(id: string, side: "long" | "short", tSec: number, over: Partial<LiquidationRow> = {}): LiquidationRow {
  // 0.004 at 100000.0: 4 size units (precision 3) x 1 000 000 price units (precision 1) = 4 000 000 at 10^-4.
  return {
    side,
    size_units: 4,
    price_units: 1_000_000,
    price_precision: 1,
    size_precision: 3,
    venue_event_id: id,
    ts_event: tSec * NS,
    notional_units: 4_000_000,
    notional_precision: 4,
    price_kind: "bankruptcy",
    ...over,
  };
}

describe("sqrtRadius", () => {
  it("runs from the minimum radius at 0 to the maximum at the largest notional", () => {
    expect(sqrtRadius(0, 100)).toBe(MARKER_MIN_RADIUS_PX);
    expect(sqrtRadius(100, 100)).toBe(MARKER_MAX_RADIUS_PX);
    expect(sqrtRadius(25, 100)).toBe(MARKER_MIN_RADIUS_PX + (MARKER_MAX_RADIUS_PX - MARKER_MIN_RADIUS_PX) * 0.5);
    expect(sqrtRadius(5, 0)).toBe(MARKER_MIN_RADIUS_PX);
  });

  it("is monotonic in the notional", () => {
    const radii = [0, 1, 4, 9, 50, 99, 100].map((n) => sqrtRadius(n, 100));
    for (let i = 1; i < radii.length; i++) expect(radii[i]).toBeGreaterThanOrEqual(radii[i - 1]);
  });
});

describe("markerSize", () => {
  it("inverts the library's circle diameter: ceiledEven(ceiledOdd(clamp(bs, 12, 30))) x size x 0.8", () => {
    // bs 21: ceiledOdd 21 -> ceiledEven 20; a radius of 8 px is a 16 px circle = 20 x size x 0.8.
    expect(markerSize(8, 21)).toBeCloseTo(1, 10);
    // bs 20: ceiledOdd 19 -> ceiledEven 18.
    expect(markerSize(9, 20)).toBeCloseTo(18 / (0.8 * 18), 10);
    // bs 6 clamps to 12 (ceiledOdd 11 -> ceiledEven 10); bs 50 clamps to 30 (29 -> 28).
    expect(markerSize(4, 6)).toBeCloseTo(1, 10);
    expect(markerSize(MARKER_MAX_RADIUS_PX, 50)).toBeCloseTo((2 * MARKER_MAX_RADIUS_PX) / (0.8 * 28), 10);
  });
});

describe("buildLiquidationMarkers", () => {
  it("places each liquidation at its bankruptcy price, a long below it and a short above it", () => {
    const markers = buildLiquidationMarkers(
      [liq("a", "long", 610), liq("b", "long", 650), liq("c", "short", 700)],
      SLOTS,
      60,
      20,
      COLORS,
    );
    expect(markers.map((m) => [m.id, m.time, m.position, m.price, m.color])).toEqual([
      ["liq:a", 600, "atPriceBottom", 100000, "#a00"],
      ["liq:b", 600, "atPriceBottom", 100000, "#a00"],
      ["liq:c", 660, "atPriceTop", 100000, "#0a0"],
    ]);
    expect(markers[0].tooltip).toEqual(["long liquidated", "size 0.004", "price 100000.0 (bankruptcy)", "notional 400.0000"]);
    // Equal notionals: every circle is the largest.
    expect(markers[0].size).toBeCloseTo(markerSize(MARKER_MAX_RADIUS_PX, 20), 10);
  });

  it("draws a row with an unknown side as neither side, and logs it", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const odd = { ...liq("x", "long", 610), side: "sideways" };
    const markers = buildLiquidationMarkers([odd, liq("a", "short", 620)], SLOTS, 60, 20, COLORS);
    expect(markers.map((m) => m.id)).toEqual(["liq:a"]);
    expect(error).toHaveBeenCalledTimes(1);
    error.mockRestore();
  });

  it(`merges more than ${MARKER_MERGE_COUNT} liquidations of one side in one bar, the sum from the bar row`, () => {
    const shorts = Array.from({ length: 7 }, (_, i) => liq(`s${i}`, "short", 720 + i));
    // The server's per-side notional of bar 720: 2800.0000 short at 10^-4.
    const bars = new Map([[720_000, { t: 720_000, notional_precision: 4, long_notional_units: 0, short_notional_units: 28_000_000 }]]);
    const merged = buildLiquidationMarkers([...shorts, liq("l", "long", 721)], SLOTS, 60, 20, COLORS, bars);
    expect(merged.map((m) => [m.id, m.position])).toEqual([
      ["liq:l", "atPriceBottom"],
      ["liq-merged:720:short", "aboveBar"],
    ]);
    expect(merged[1].text).toBe("Σ 2800.0000 · 7");
    expect(merged[1].tooltip).toEqual(["7 short liquidations", "notional Σ 2800.0000", "price kind bankruptcy"]);
    // 2800 is the largest notional drawn: the merged circle takes the maximum radius.
    expect(merged[1].size).toBeCloseTo(markerSize(MARKER_MAX_RADIUS_PX, 20), 10);
  });

  it("reads Σ — at the minimum radius when the bar row is missing or its side's notional is null", () => {
    const longs = Array.from({ length: 4 }, (_, i) => liq(`l${i}`, "long", 600 + i));
    const nullSide = new Map([[600_000, { t: 600_000, notional_precision: 4, long_notional_units: null, short_notional_units: 0 }]]);
    for (const bars of [new Map(), nullSide]) {
      const [merged] = buildLiquidationMarkers(longs, SLOTS, 60, 20, COLORS, bars);
      expect(merged.text).toBe("Σ — · 4");
      expect(merged.tooltip[1]).toBe("notional Σ —");
      expect(merged.size).toBeCloseTo(markerSize(MARKER_MIN_RADIUS_PX, 20), 10);
    }
  });

  it("keeps exactly the merge count as single markers", () => {
    const three = Array.from({ length: MARKER_MERGE_COUNT }, (_, i) => liq(`l${i}`, "long", 600 + i));
    expect(buildLiquidationMarkers(three, SLOTS, 60, 20, COLORS)).toHaveLength(MARKER_MERGE_COUNT);
  });

  it("leaves out a row past the last slot's end: it draws once its bar exists", () => {
    expect(buildLiquidationMarkers([liq("late", "long", 785)], SLOTS, 60, 20, COLORS)).toEqual([]);
    expect(buildLiquidationMarkers([liq("in", "long", 779)], SLOTS, 60, 20, COLORS).map((m) => m.time)).toEqual([720]);
  });

  it("draws no marker for a row whose price cannot be read exactly, and logs it", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const bad = liq("bad", "long", 610, { price_units: 2 ** 60 });
    expect(buildLiquidationMarkers([bad, liq("ok", "long", 611)], SLOTS, 60, 20, COLORS).map((m) => m.id)).toEqual(["liq:ok"]);
    errors.mockRestore();
  });

  it("draws nothing at a bar spacing at or below the threshold, or before the first slot", () => {
    expect(buildLiquidationMarkers([liq("a", "long", 610)], SLOTS, 60, MARKER_MIN_BAR_SPACING_PX, COLORS)).toEqual([]);
    expect(buildLiquidationMarkers([liq("a", "long", 500)], SLOTS, 60, 20, COLORS)).toEqual([]);
  });

  it("scales the radius by the notional against the largest marker", () => {
    const markers = buildLiquidationMarkers(
      [liq("big", "long", 600, { notional_units: 400 }), liq("small", "short", 600, { notional_units: 100 })],
      SLOTS,
      60,
      20,
      COLORS,
    );
    const half = MARKER_MIN_RADIUS_PX + (MARKER_MAX_RADIUS_PX - MARKER_MIN_RADIUS_PX) * 0.5;
    expect(markers.map((m) => m.size)).toEqual([markerSize(MARKER_MAX_RADIUS_PX, 20), markerSize(half, 20)]);
  });
});
