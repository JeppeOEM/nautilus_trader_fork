import { describe, expect, it } from "vitest";

import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BarGrid } from "./drawingPrimitive";
import { type LineOptions, PLAIN_LINE, TrendlinePrimitive } from "./TrendlinePrimitive";

const grid = (): BarGrid => {
  const g = new BarGrid();
  g.set([100, 200, 300, 400]);
  return g;
};

// On the test chart a bar's x is its time and a price's y is 1000 - price: A (100, 400), B (200, 350),
// on a pane 800 x 500 px.
function line(options: Partial<LineOptions> = {}): TrendlinePrimitive {
  const primitive = new TrendlinePrimitive(
    [
      { time: 100, price: 600 },
      { time: 200, price: 650 },
    ],
    "#112233",
    grid(),
    { ...PLAIN_LINE, ...options },
  );
  attachTo(primitive);
  return primitive;
}

describe("TrendlinePrimitive's line kinds (Story 33.10)", () => {
  it("draws a trendline between its anchors only", () => {
    const { strokes } = drawPrimitive(line(), 800);
    expect(strokes[0]).toMatchObject({ from: [100, 400], to: [200, 350] });
  });

  it("carries a ray past B to the pane's border, and hits the drawn extension", () => {
    const ray = line({ extend: "right" });
    expect(ray.hit(600, 150)).toBeNull(); // before the first paint the pane's size is unknown
    const { strokes } = drawPrimitive(ray, 800);
    // Half a pixel up per pixel right: the right border (x 800) is reached at y 50.
    expect(strokes[0]).toMatchObject({ from: [100, 400], to: [800, 50] });
    expect(ray.drawnSegment()).toEqual([
      { x: 100, y: 400 },
      { x: 800, y: 50 },
    ]);
    expect(ray.hit(600, 150)).toMatchObject({ handle: null });
    expect(ray.hit(50, 425)).toBeNull(); // behind A: a ray does not run back
  });

  it("carries an extended line past both anchors", () => {
    const extended = line({ extend: "both" });
    drawPrimitive(extended, 800);
    expect(extended.drawnSegment()?.[0]).toEqual({ x: 0, y: 450 });
    expect(extended.hit(50, 425)).toMatchObject({ handle: null });
  });

  it("draws an arrow head at B: two short sides back along the line", () => {
    const { strokes } = drawPrimitive(line({ arrow: true }), 800);
    const head = strokes.filter((s) => s.from[0] === 200 && s.from[1] === 350);
    expect(head).toHaveLength(2);
    expect(head.every((s) => s.to[0] < 200)).toBe(true); // pointing back towards A
  });

  it("draws its stored width and style", () => {
    const { context, dashes } = drawPrimitive(line({ lineWidth: 3, lineStyle: "dashed" }), 800);
    expect(context.lineWidth).toBe(3);
    expect(dashes[0]).toEqual([6, 4]);
  });

  it("hits a handle, the line, or nothing", () => {
    const primitive = line();
    expect(primitive.hit(102, 401)).toMatchObject({ handle: "a" });
    expect(primitive.hit(150, 376)).toMatchObject({ handle: null });
    expect(primitive.hit(150, 300)).toBeNull();
  });

  it("draws no handles while locked, editable or not", () => {
    const open = line();
    open.setHandlesVisible(true);
    expect(drawPrimitive(open, 800).fills).toHaveLength(2);
    const locked = line({ locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked, 800).fills).toHaveLength(0);
  });
});
