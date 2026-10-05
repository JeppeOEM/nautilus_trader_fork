import { describe, expect, it } from "vitest";

import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { AnchoredVpPrimitive } from "./AnchoredVpPrimitive";
import { BarGrid } from "./drawingPrimitive";

const grid = (): BarGrid => {
  const g = new BarGrid();
  g.set([100, 200, 300, 400]);
  return g;
};

// The fake chart: x of a bar is its time, y of a price is 1000 - price.
function attached(time: number, anchorPrice: number | null): AnchoredVpPrimitive {
  const primitive = new AnchoredVpPrimitive(time, anchorPrice, grid());
  attachTo(primitive);
  return primitive;
}

describe("AnchoredVpPrimitive (Story 32.7)", () => {
  it("sits on the bar the anchor falls on and at the profile's top price", () => {
    expect(attached(250, 120).screen()).toEqual({ x: 200, y: 880 });
  });

  it("is not drawable when the anchor is older than the loaded bars", () => {
    const primitive = attached(50, 120);

    expect(primitive.screen()).toBeNull();
    expect(primitive.hit(50, 880)).toBeNull();
    expect(drawPrimitive(primitive).strokes).toEqual([]);
  });

  it("hits the anchor handle first, then the line within its tolerance, else nothing", () => {
    const primitive = attached(200, 120);

    expect(primitive.hit(203, 884)).toMatchObject({ handle: "anchor" });
    expect(primitive.hit(203, 400)).toEqual({ handle: null, distance: 3 }); // on the line, far from the handle
    expect(primitive.hit(230, 884)).toBeNull();
  });

  it("only the line is grabbable while the profile has no rows (no handle height)", () => {
    const primitive = attached(200, null);

    expect(primitive.hit(200, 880)).toEqual({ handle: null, distance: 0 });
  });

  it("draws the dashed anchor line, and the handle square only while handles are visible", () => {
    const primitive = attached(200, 120);
    expect(drawPrimitive(primitive).fills).toHaveLength(0);

    primitive.setHandlesVisible(true);
    const { fills, strokes } = drawPrimitive(primitive);
    expect(strokes).toHaveLength(1);
    expect(strokes[0].from[0]).toBe(200);
    expect(fills).toHaveLength(1);
  });

  it("follows an update of the anchor time and price", () => {
    const primitive = attached(200, 120);

    primitive.update(300, 110);
    primitive.updateAllViews();

    expect(primitive.screen()).toEqual({ x: 300, y: 890 });
  });
});
