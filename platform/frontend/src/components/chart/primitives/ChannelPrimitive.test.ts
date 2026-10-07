import { describe, expect, it } from "vitest";

import type { ChannelDrawing } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BarGrid } from "./drawingPrimitive";
import { ChannelPrimitive } from "./ChannelPrimitive";

// A (100, 600) and B (300, 640) at y 400 / 360; the parallel 50 below at y 450 / 410.
function channel(overrides: Partial<ChannelDrawing> = {}): ChannelPrimitive {
  const g = new BarGrid();
  g.set([100, 200, 300]);
  const drawing: ChannelDrawing = {
    kind: "channel",
    id: "channel-1",
    anchors: [
      { time: 100, price: 600 },
      { time: 300, price: 640 },
    ],
    offset: -50,
    ...overrides,
  };
  const primitive = new ChannelPrimitive(drawing, g);
  attachTo(primitive);
  return primitive;
}

describe("ChannelPrimitive (Story 33.10)", () => {
  it("draws the A-B line and its parallel at the offset, the band between them filled", () => {
    const { strokes, polygons } = drawPrimitive(channel());
    expect(strokes.map((s) => [s.from, s.to])).toEqual([
      [
        [100, 400],
        [300, 360],
      ],
      [
        [100, 450],
        [300, 410],
      ],
    ]);
    expect(polygons[0]).toMatchObject({ alpha: 0.1, points: [[100, 400], [300, 360], [300, 410], [100, 450]] });
  });

  it("puts the offset handle at the middle of the parallel", () => {
    expect(channel().screen()?.offset).toEqual({ x: 200, y: 430 });
  });

  it("hits a, b and offset, either line, the band, and nothing outside", () => {
    const primitive = channel();
    expect(primitive.hit(101, 401)).toMatchObject({ handle: "a" });
    expect(primitive.hit(199, 431)).toMatchObject({ handle: "offset" });
    expect(primitive.hit(250, 371)).toMatchObject({ handle: null });
    expect(primitive.hit(150, 415)).toMatchObject({ handle: null, distance: 5 }); // inside the band
    expect(primitive.hit(150, 300)).toBeNull();
  });

  it("draws no handles while locked", () => {
    const open = channel();
    open.setHandlesVisible(true);
    expect(drawPrimitive(open).fills).toHaveLength(3);
    const locked = channel({ locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked).fills).toHaveLength(0);
  });
});
