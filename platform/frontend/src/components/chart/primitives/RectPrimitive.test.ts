import { describe, expect, it } from "vitest";

import type { RectDrawing } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BarGrid } from "./drawingPrimitive";
import { RectPrimitive } from "./RectPrimitive";

// Corners A (100, 600) and B (300, 660) sit at x 100 / 300 and y 400 / 340 on the test chart.
function rect(overrides: Partial<RectDrawing> = {}, precision: number | null = 2): RectPrimitive {
  const g = new BarGrid();
  g.set([100, 200, 300]);
  const drawing: RectDrawing = {
    kind: "rect",
    id: "rect-1",
    anchors: [
      { time: 100, price: 600 },
      { time: 300, price: 660 },
    ],
    fill_opacity: 0.25,
    color: "#112233",
    ...overrides,
  };
  const primitive = new RectPrimitive(drawing, precision, g);
  attachTo(primitive);
  return primitive;
}

describe("RectPrimitive (Story 33.10)", () => {
  it("fills the box between its corners at the stored opacity and outlines it", () => {
    const { fills, rects } = drawPrimitive(rect());
    expect(fills[0]).toMatchObject({ x: 100, y: 340, w: 200, h: 60, alpha: 0.25, style: "#112233" });
    expect(rects[0]).toMatchObject({ x: 100, y: 340, w: 200, h: 60 });
  });

  it("labels its height and percent in the corner, at the instrument precision, and not without one", () => {
    expect(drawPrimitive(rect()).texts.map((t) => t.text)).toEqual(["60.00 (+10.00 %)"]);
    expect(drawPrimitive(rect({}, null)).texts).toEqual([]);
  });

  it("hits a corner handle, else its border or inside, else nothing", () => {
    const primitive = rect();
    expect(primitive.hit(298, 342)).toMatchObject({ handle: "b" });
    expect(primitive.hit(200, 342)).toMatchObject({ handle: null, distance: 2 });
    expect(primitive.hit(200, 370)).toMatchObject({ handle: null, distance: 5 });
    expect(primitive.hit(200, 420)).toBeNull();
  });

  it("clamps a hand-edited opacity instead of painting it opaque, and draws no handles while locked", () => {
    expect(drawPrimitive(rect({ fill_opacity: 7 })).fills[0].alpha).toBe(1);
    const locked = rect({ locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked).fills).toHaveLength(1); // the fill alone, no handle squares
  });
});
