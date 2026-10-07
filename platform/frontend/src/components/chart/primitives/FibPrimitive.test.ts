import { describe, expect, it } from "vitest";

import { type FibDrawing, type FibExtensionDrawing, defaultFibLevels } from "../../../lib/drawings";
import { BarGrid } from "./drawingPrimitive";
import { attachTo, drawPrimitive } from "../../../test/drawingKit";
import { FibPrimitive } from "./FibPrimitive";

const grid = (): BarGrid => {
  const g = new BarGrid();
  g.set([100, 200, 300, 400]);
  return g;
};

function fib(a: number, b: number, overrides: Partial<FibDrawing> = {}): FibDrawing {
  return {
    kind: "fib",
    id: "fib-1",
    anchors: [
      { time: 100, price: a },
      { time: 300, price: b },
    ],
    levels: defaultFibLevels(() => "#112233"),
    extend_right: true,
    label_side: "left",
    line_width: 1,
    ...overrides,
  };
}

function attached(drawing: FibDrawing, precision: number | null): FibPrimitive {
  const primitive = new FibPrimitive(drawing, precision, grid());
  attachTo(primitive);
  return primitive;
}

describe("FibPrimitive (Story 32.5)", () => {
  it("draws one level per enabled ratio at B + (A - B) * ratio, labelled 'ratio (price)' at precision 2", () => {
    const { texts } = drawPrimitive(attached(fib(100, 90), 2));
    expect(texts.map((t) => t.text)).toEqual([
      "0 (90.00)",
      "0.236 (92.36)",
      "0.382 (93.82)",
      "0.5 (95.00)",
      "0.618 (96.18)",
      "0.786 (97.86)",
      "1 (100.00)",
    ]);
  });

  it("labels at six decimals for a precision-6 instrument, with no float noise", () => {
    const { texts } = drawPrimitive(attached(fib(0.123456, 0.1), 6));
    expect(texts.map((t) => t.text)).toEqual([
      "0 (0.100000)",
      "0.236 (0.105536)",
      "0.382 (0.108960)",
      "0.5 (0.111728)",
      "0.618 (0.114496)",
      "0.786 (0.118436)",
      "1 (0.123456)",
    ]);
  });

  it("draws the levels but no label while the precision is unknown", () => {
    const { texts, strokes } = drawPrimitive(attached(fib(100, 90), null));
    expect(texts).toEqual([]);
    expect(strokes.length).toBeGreaterThanOrEqual(7);
  });

  it("puts ratio 0 on B and 1 on A, up-drag included", () => {
    const down = attached(fib(100, 90), 2).screen()!;
    expect([down.levels[0].y, down.levels[6].y]).toEqual([1000 - 90, 1000 - 100]);
    const up = attached(fib(90, 100), 2).screen()!;
    expect([up.levels[0].y, up.levels[6].y]).toEqual([1000 - 100, 1000 - 90]);
  });

  it("extends the levels to the pane's right edge by default, else to the right anchor", () => {
    const extended = drawPrimitive(attached(fib(100, 90), 2), 800).strokes.filter((s) => s.from[0] === 100);
    expect(extended.some((s) => s.to[0] === 800)).toBe(true);
    const bounded = drawPrimitive(attached(fib(100, 90, { extend_right: false }), 2), 800).strokes.filter(
      (s) => s.from[1] === s.to[1],
    );
    expect(bounded.every((s) => s.from[0] === 100 && s.to[0] === 300)).toBe(true);
  });

  it("fills a translucent band between each pair of consecutive levels", () => {
    const { fills } = drawPrimitive(attached(fib(100, 90), 2));
    expect(fills).toHaveLength(6);
    expect(fills.every((f) => f.alpha === 0.1)).toBe(true);
  });

  it("draws an anchor on the bar at or before its time and leaves the stored anchor alone", () => {
    const drawing = fib(100, 90, {
      anchors: [
        { time: 137, price: 100 },
        { time: 299, price: 90 },
      ],
    });
    const g = attached(drawing, 2).screen()!;
    expect([g.a.x, g.b.x]).toEqual([100, 200]);
    expect(drawing.anchors[0].time).toBe(137);
  });

  it("draws nothing for an anchor older than every loaded bar", () => {
    const drawing = fib(100, 90, {
      anchors: [
        { time: 50, price: 100 },
        { time: 300, price: 90 },
      ],
    });
    expect(attached(drawing, 2).screen()).toBeNull();
  });

  it("hits an anchor handle, else a level line, else nothing", () => {
    const primitive = attached(fib(100, 90), 2); // A at (100, 900), B at (300, 910); level 0.5 at y 905
    expect(primitive.hit(102, 903)).toMatchObject({ handle: "a" });
    expect(primitive.hit(298, 912)).toMatchObject({ handle: "b" });
    expect(primitive.hit(200, 906)).toMatchObject({ handle: null });
    expect(primitive.hit(200, 700)).toBeNull();
    expect(primitive.hit(50, 905)).toBeNull(); // left of the levels
  });

  it("is hit-testable along an extended level far to the right of its anchors", () => {
    const primitive = attached(fib(100, 90), 2);
    expect(primitive.hit(700, 905)).toMatchObject({ handle: null });
    expect(attached(fib(100, 90, { extend_right: false }), 2).hit(700, 905)).toBeNull();
  });

  it("draws a Fibonacci anchored at 12:37 on the 12:00 bar after the timeframe goes from 1m to 1H, the stored anchor unchanged", () => {
    const at = (h: number, m = 0): number => (h * 60 + m) * 60;
    const drawing = fib(100, 90, {
      anchors: [
        { time: at(12, 37), price: 100 },
        { time: at(14, 5), price: 90 },
      ],
    });
    const minutes = new BarGrid();
    minutes.set(Array.from({ length: 180 }, (_, i) => at(12) + i * 60)); // 12:00 .. 14:59, 1m bars
    const hours = new BarGrid();
    hours.set([at(12), at(13), at(14)]); // the same span on a 1H chart

    const onMinutes = new FibPrimitive(drawing, 2, minutes);
    const onHours = new FibPrimitive(drawing, 2, hours);
    attachTo(onMinutes);
    attachTo(onHours);

    expect([onMinutes.screen()?.a.x, onMinutes.screen()?.b.x]).toEqual([at(12, 37), at(14, 5)]);
    expect([onHours.screen()?.a.x, onHours.screen()?.b.x]).toEqual([at(12), at(14)]);
    expect(drawing.anchors[0].time).toBe(at(12, 37));
  });
});

describe("FibPrimitive as a Fibonacci extension (Story 33.10)", () => {
  // A 100 -> B 90 (a 10 down move), projected from C 95: ratio r at 95 - 10 r.
  function extension(overrides: Partial<FibExtensionDrawing> = {}): FibPrimitive {
    const drawing: FibExtensionDrawing = {
      kind: "fib_extension",
      id: "fib_extension-1",
      anchors: [
        { time: 100, price: 100 },
        { time: 200, price: 90 },
        { time: 300, price: 95 },
      ],
      levels: [
        { ratio: 0, enabled: true, color: "#112233" },
        { ratio: 1, enabled: true, color: "#112233" },
        { ratio: 1.618, enabled: true, color: "#112233" },
      ],
      extend_right: false,
      label_side: "left",
      line_width: 1,
      ...overrides,
    };
    const primitive = new FibPrimitive(drawing, 2, grid());
    attachTo(primitive);
    return primitive;
  }

  it("draws each level at C + (B - A) x ratio, labelled at the instrument precision", () => {
    expect(drawPrimitive(extension()).texts.map((t) => t.text)).toEqual(["0 (95.00)", "1 (85.00)", "1.618 (78.82)"]);
  });

  it("starts the levels at C and runs them as far right as the A-B move", () => {
    const g = extension().screen()!;
    expect([g.left, g.right]).toEqual([300, 400]);
    expect(extension({ extend_right: true }).screen()!.right).toBeNull();
  });

  it("hits three handles and draws the A-B-C path", () => {
    const primitive = extension();
    expect(primitive.hit(301, 906)).toMatchObject({ handle: "c" });
    expect(primitive.hit(250, 907.5)).toMatchObject({ handle: null }); // on the B-C leg
    primitive.setHandlesVisible(true);
    const { strokes } = drawPrimitive(primitive);
    expect(strokes.some((s) => s.from[0] === 200 && s.to[0] === 300)).toBe(true);
    expect(drawPrimitive(primitive).rects).toHaveLength(3);
  });

  it("draws no handles while locked", () => {
    const locked = extension({ locked: true });
    locked.setHandlesVisible(true);
    expect(drawPrimitive(locked).rects).toHaveLength(0);
  });
});
