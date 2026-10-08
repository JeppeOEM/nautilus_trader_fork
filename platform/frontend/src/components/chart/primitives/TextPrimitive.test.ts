import { describe, expect, it } from "vitest";

import type { TextDrawing } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BODY_TOLERANCE_PX, BarGrid } from "./drawingPrimitive";
import { TextPrimitive } from "./TextPrimitive";

// The anchor (200, 700) sits at (200, 300); the fake context measures 6 px per character.
function note(overrides: Partial<TextDrawing> = {}): TextPrimitive {
  const g = new BarGrid();
  g.set([100, 200, 300]);
  const drawing: TextDrawing = {
    kind: "text",
    id: "text-1",
    anchor: { time: 200, price: 700 },
    text: "Breakout\nretest",
    font_size: 10,
    color: "#112233",
    ...overrides,
  };
  const primitive = new TextPrimitive(drawing, g);
  attachTo(primitive);
  return primitive;
}

describe("TextPrimitive (Story 33.10)", () => {
  it("writes each line in its colour from the anchor corner down", () => {
    const { texts } = drawPrimitive(note());
    expect(texts.map((t) => [t.text, t.x, t.y, t.style])).toEqual([
      ["Breakout", 203, 303, "#112233"],
      ["retest", 203, 315.5, "#112233"],
    ]);
  });

  it("sizes its box from the measured text once painted (an estimate before)", () => {
    const primitive = note();
    expect(primitive.screen()?.width).toBe(8 * 10 * 0.6 + 6); // the estimate: 8 characters
    drawPrimitive(primitive);
    primitive.updateAllViews();
    expect(primitive.screen()).toEqual({ x: 200, y: 300, width: 8 * 6 + 6, height: 2 * 10 * 1.25 + 6 });
  });

  it("hits its corner handle, else anywhere on the box, else nothing", () => {
    const primitive = note();
    expect(primitive.hit(201, 301)).toMatchObject({ handle: "anchor" });
    // At the body tolerance, so a line passing closer to the pointer is still the one selected.
    expect(primitive.hit(240, 320)).toMatchObject({ handle: null, distance: BODY_TOLERANCE_PX });
    expect(primitive.hit(240, 340)).toBeNull();
  });

  it("draws no handle (and no edit outline) while locked", () => {
    const open = note();
    open.setHandlesVisible(true);
    expect(drawPrimitive(open).fills).toHaveLength(1);
    const locked = note({ locked: true });
    locked.setHandlesVisible(true);
    const drawn = drawPrimitive(locked);
    expect([drawn.fills.length, drawn.rects.length]).toEqual([0, 0]);
  });
});
