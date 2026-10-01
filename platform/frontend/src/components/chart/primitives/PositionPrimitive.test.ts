import { describe, expect, it } from "vitest";

import { type PositionDrawing, newPosition } from "../../../lib/drawings";
import { BarGrid } from "./drawingPrimitive";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { PositionPrimitive } from "./PositionPrimitive";

const grid = (): BarGrid => {
  const g = new BarGrid();
  g.set([100, 200, 300, 400]);
  return g;
};

function attached(position: PositionDrawing, precision: { price: number; size: number } | null): PositionPrimitive {
  const primitive = new PositionPrimitive(position, precision, grid());
  attachTo(primitive);
  return primitive;
}

const long = (): PositionDrawing => newPosition("position-1", "long", 200, 100, 2);

describe("PositionPrimitive (Story 32.5)", () => {
  it("draws the profit zone entry -> target and the loss zone entry -> stop at about 20 % alpha, from the bar to width_bars later", () => {
    const primitive = attached(long(), { price: 2, size: 3 });
    // placed on bar index 1 (time 200); right edge 40 slots later, beyond the newest bar
    expect(primitive.screen()).toMatchObject({ left: 10, right: 410, entryY: 900, targetY: 898, stopY: 901 });
    const { fills } = drawPrimitive(primitive);
    expect(fills).toHaveLength(2);
    expect(fills.every((f) => f.alpha === 0.2 && f.x === 10 && f.w === 400)).toBe(true);
    expect(fills.map((f) => [f.y, f.h])).toEqual([
      [898, 2], // profit: entry 900 up to target 898
      [900, 1], // loss: entry 900 down to stop 901
    ]);
    expect(fills[0].style).not.toEqual(fills[1].style);
  });

  it("labels Target / Entry / Stop / Risk/Reward for a Long at 100, precision 2", () => {
    const { texts } = drawPrimitive(attached(long(), { price: 2, size: 3 }));
    expect(texts.map((t) => t.text)).toEqual([
      "Target: 102.00 (+2.00 %)",
      "Stop: 99.00 (−1.00 %)",
      "Entry: 100.00",
      "Risk/Reward: 2.00",
    ]);
  });

  it("labels a precision-6 instrument at six decimals, and adds the Size when sized", () => {
    const p: PositionDrawing = { ...newPosition("position-1", "long", 200, 0.123456, 6), account: 1000, risk_pct: 1 };
    const { texts } = drawPrimitive(attached(p, { price: 6, size: 0 }));
    expect(texts.map((t) => t.text)).toEqual([
      "Target: 0.125926 (+2.00 %)",
      "Stop: 0.122221 (−1.00 %)",
      "Entry: 0.123456",
      "Risk/Reward: 2.00   Size: 8097",
    ]);
  });

  it("recomputes Risk/Reward and the percents when the target moves", () => {
    const p: PositionDrawing = { ...long(), target: 103 };
    const { texts } = drawPrimitive(attached(p, { price: 2, size: 3 }));
    expect(texts[0].text).toBe("Target: 103.00 (+3.00 %)");
    expect(texts[3].text).toBe("Risk/Reward: 3.00");
  });

  it("draws the zones but no label while the precision is unknown", () => {
    const { texts, fills } = drawPrimitive(attached(long(), null));
    expect(texts).toEqual([]);
    expect(fills).toHaveLength(2);
  });

  it("draws a Short with the target below the entry", () => {
    const short = newPosition("position-2", "short", 200, 100, 2);
    expect(attached(short, { price: 2, size: 3 }).screen()).toMatchObject({ entryY: 900, targetY: 902, stopY: 899 });
  });

  it("hits the entry, target, stop and right-edge handles, else the box, else nothing", () => {
    const primitive = attached(long(), { price: 2, size: 3 }); // box x 10..410, y 898..901
    expect(primitive.hit(12, 900)).toMatchObject({ handle: "entry" });
    expect(primitive.hit(408, 898)).toMatchObject({ handle: "target" });
    expect(primitive.hit(408, 901)).toMatchObject({ handle: "stop" });
    expect(primitive.hit(412, 900)).toMatchObject({ handle: "right" });
    expect(primitive.hit(200, 900)).toMatchObject({ handle: null });
    expect(primitive.hit(200, 600)).toBeNull();
    expect(primitive.hit(900, 900)).toBeNull();
  });

  it("is not drawable when its bar is older than every loaded bar", () => {
    expect(attached({ ...long(), time: 50 }, { price: 2, size: 3 }).screen()).toBeNull();
  });

  it("draws on the earlier bar for a time between two bars", () => {
    expect(attached({ ...long(), time: 250 }, { price: 2, size: 3 }).screen()?.left).toBe(10);
  });
});
