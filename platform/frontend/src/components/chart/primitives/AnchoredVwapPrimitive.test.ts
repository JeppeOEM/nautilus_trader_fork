import { describe, expect, it } from "vitest";

import { anchoredVwap } from "../../../lib/anchoredVwap";
import { newAnchoredVwap } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { AnchoredVwapPrimitive } from "./AnchoredVwapPrimitive";

const bar = (time: number, price: number, volume: number) => ({
  time,
  open: price,
  high: price + 1,
  low: price - 1,
  close: price,
  volume,
});
// hlc3 = price: 100, 110, (skipped, no volume), 130
const points = anchoredVwap([bar(100, 100, 1), bar(200, 110, 1), bar(300, 120, 0), bar(400, 130, 2)], 100, "hlc3");

function attached(over: Partial<ReturnType<typeof newAnchoredVwap>> = {}): AnchoredVwapPrimitive {
  const primitive = new AnchoredVwapPrimitive({ ...newAnchoredVwap("v", 100, "#2962ff", "#b26a00"), ...over }, points);
  attachTo(primitive);
  return primitive;
}

describe("AnchoredVwapPrimitive (Story 32.7)", () => {
  it("places each point at its bar time and the VWAP's price, with no point for the zero-volume bar", () => {
    const screen = attached().screen();

    expect(screen.map((p) => p && p.x)).toEqual([100, 200, 400]);
    expect(screen[0]!.vwap).toBe(900); // y = 1000 - 100
  });

  it("draws the VWAP line alone with the bands off, in the drawing's colour", () => {
    const { strokes } = drawPrimitive(attached({ bands: false, color: "#123456" }));

    expect(strokes).toHaveLength(1);
    expect(strokes[0].style).toBe("#123456");
    expect(strokes[0].from[0]).toBe(100);
    expect(strokes[0].to[0]).toBe(400);
  });

  it("draws four band lines (the ±1σ solid, the ±2σ dashed) under the VWAP with the bands on", () => {
    const { strokes } = drawPrimitive(attached({ bands: true, band_color: "#abcdef" }));

    expect(strokes).toHaveLength(5);
    expect(strokes.slice(0, 4).every((s) => s.style === "#abcdef")).toBe(true);
    expect(strokes[4].style).toBe("#2962ff"); // the VWAP on top
  });

  it("hits the anchor handle on the first point, then the line, else nothing", () => {
    const primitive = attached();

    expect(primitive.hit(103, 903)).toMatchObject({ handle: "anchor" });
    // between the first two points (100,900) -> (200,890), midway: x 150, y 895
    expect(primitive.hit(150, 897)).toMatchObject({ handle: null });
    expect(primitive.hit(150, 800)).toBeNull();
  });

  it("is not drawable, and not hittable, when no bar has volume yet", () => {
    const primitive = new AnchoredVwapPrimitive(newAnchoredVwap("v", 100, "#2962ff", "#b26a00"), []);
    attachTo(primitive);

    expect(primitive.hit(100, 900)).toBeNull();
    expect(drawPrimitive(primitive).strokes).toEqual([]);
  });

  it("breaks the path at a point with no coordinate instead of guessing one", () => {
    const primitive = new AnchoredVwapPrimitive(newAnchoredVwap("v", 100, "#2962ff", "#b26a00"), points);
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => (t === 200 ? null : t) }) },
      series: { priceToCoordinate: (p: number) => 1000 - p },
      requestUpdate: () => {},
    } as never);
    primitive.updateAllViews();

    expect(primitive.screen().map((p) => p === null)).toEqual([false, true, false]);
    expect(primitive.hit(250, 880)).toBeNull(); // no segment crosses the hole
  });
});
