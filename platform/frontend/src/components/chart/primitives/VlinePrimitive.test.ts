import { describe, expect, it } from "vitest";

import type { VlineDrawing } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BarGrid } from "./drawingPrimitive";
import { VlinePrimitive } from "./VlinePrimitive";

function vline(overrides: Partial<VlineDrawing> = {}): VlinePrimitive {
  const g = new BarGrid();
  g.set([100, 200, 300]);
  const primitive = new VlinePrimitive({ kind: "vline", id: "vline-1", time: 250, ...overrides }, g);
  attachTo(primitive);
  return primitive;
}

describe("VlinePrimitive (Story 33.10)", () => {
  it("draws across the whole pane at the bar the time falls on", () => {
    const primitive = vline();
    expect(primitive.screen()).toEqual({ x: 200 });
    const { strokes } = drawPrimitive(primitive);
    expect(strokes[0]).toMatchObject({ from: [200, 0], to: [200, 500] });
  });

  it("is grabbed anywhere along the line by its one handle, and missed off it", () => {
    const primitive = vline();
    expect(primitive.hit(203)).toMatchObject({ handle: "time", distance: 3 });
    expect(primitive.hit(220)).toBeNull();
  });

  it("draws nothing for a time older than every bar", () => {
    expect(vline({ time: 50 }).screen()).toBeNull();
  });

  it("draws its handle only while editable and not locked", () => {
    const open = vline();
    open.setHandlesVisible(true);
    expect(drawPrimitive(open).fills).toHaveLength(1);
    const locked = vline({ locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked).fills).toHaveLength(0);
  });
});
