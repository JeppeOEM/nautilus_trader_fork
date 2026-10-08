import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import type { RangeDrawing } from "../../../lib/drawings";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { BarGrid } from "./drawingPrimitive";
import { buildMeasurementIndex } from "./MeasurementPrimitive";
import { RangePrimitive } from "./RangePrimitive";

const bar = (time: number) => ({ time: time as Time, open: 1, high: 2, low: 1, close: 1 });
const INDEX = buildMeasurementIndex(
  [bar(100), bar(200), bar(300), bar(400)],
  [100, 200, 300, 400].map((time, i) => ({ time: time as Time, value: [1.5, 2.25, 3, 10][i] })),
);

// A (100, 600) and B (300, 650) at y 400 / 350.
function range(kind: RangeDrawing["kind"], overrides: Partial<RangeDrawing> = {}): RangePrimitive {
  const g = new BarGrid();
  g.set([100, 200, 300, 400]);
  const drawing: RangeDrawing = {
    kind,
    id: `${kind}-1`,
    anchors: [
      { time: 100, price: 600 },
      { time: 300, price: 650 },
    ],
    ...overrides,
  };
  const primitive = new RangePrimitive(drawing, { price: 2, size: 3 }, () => INDEX, g);
  attachTo(primitive);
  return primitive;
}

describe("RangePrimitive (Story 33.10)", () => {
  it("labels a price range with the change and percent, under its box", () => {
    const { texts } = drawPrimitive(range("price_range"));
    expect(texts.map((t) => [t.text, t.x, t.y])).toEqual([["+50.00 (+8.33 %)", 100, 404]]);
  });

  it("labels a date range with the bars it spans and their volume at the size precision", () => {
    expect(drawPrimitive(range("date_range")).texts.map((t) => t.text)).toEqual(["3 bars", "Vol 6.750"]);
  });

  it("draws the arrow vertically for a price range and horizontally for a date range", () => {
    const price = drawPrimitive(range("price_range")).strokes[0];
    expect(price).toMatchObject({ from: [200, 400], to: [200, 350] });
    const date = drawPrimitive(range("date_range")).strokes[0];
    expect(date).toMatchObject({ from: [100, 375], to: [300, 375] });
  });

  it("hits its anchors and box, and draws no handles while locked", () => {
    const primitive = range("price_range");
    expect(primitive.hit(299, 351)).toMatchObject({ handle: "b" });
    expect(primitive.hit(150, 380)).toMatchObject({ handle: null });
    expect(primitive.hit(150, 450)).toBeNull();
    const locked = range("price_range", { locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked).fills).toHaveLength(1); // the box alone
  });

  it("prints the price part and n/a bars and volume with no candle index (Lines mode) or no bars", () => {
    for (const index of [null, buildMeasurementIndex([], [])]) {
      const g = new BarGrid();
      g.set([100, 200, 300]);
      const anchors: RangeDrawing["anchors"] = [
        { time: 100, price: 600 },
        { time: 300, price: 650 },
      ];
      const date = new RangePrimitive({ kind: "date_range", id: "d", anchors }, { price: 2, size: 3 }, () => index, g);
      attachTo(date);
      expect(drawPrimitive(date).texts.map((t) => t.text)).toEqual(["n/a bars", "Vol n/a"]);
      const price = new RangePrimitive({ kind: "price_range", id: "p", anchors }, { price: 2, size: 3 }, () => index, g);
      attachTo(price);
      expect(drawPrimitive(price).texts.map((t) => t.text)).toEqual(["+50.00 (+8.33 %)"]);
    }
  });

  it("prints no label while the precision is unknown", () => {
    const g = new BarGrid();
    g.set([100, 200, 300]);
    const primitive = new RangePrimitive(
      { kind: "price_range", id: "p", anchors: [{ time: 100, price: 600 }, { time: 300, price: 650 }] },
      null,
      () => INDEX,
      g,
    );
    attachTo(primitive);
    expect(drawPrimitive(primitive).texts).toEqual([]);
  });
});
