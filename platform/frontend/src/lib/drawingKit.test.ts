import { describe, expect, it } from "vitest";

import {
  type DrawingsHistory,
  EMPTY_HISTORY,
  HISTORY_LIMIT,
  MAGNET_RADIUS_PX,
  constrainAngle,
  constrainsAngle,
  drawingsReducer,
  magnetPrice,
  nextDragGesture,
  nextMagnetMode,
  replaceDrawing,
} from "./drawingKit";
import type { Drawing } from "./drawings";

// A price's y is `1000 - price` (higher prices sit higher on screen), like the primitive tests' chart.
const priceToY = (price: number): number => 1000 - price;
const BAR = { open: 100, high: 110, low: 90, close: 105 };

describe("magnet (Story 33.10)", () => {
  it("cycles off, weak, strong", () => {
    expect([nextMagnetMode("off"), nextMagnetMode("weak"), nextMagnetMode("strong")]).toEqual(["weak", "strong", "off"]);
  });

  it("weak: snaps a point 6 px from the high to the high (radius 12 px)", () => {
    expect(MAGNET_RADIUS_PX).toBe(12);
    // The high (110) sits at y 890; the pointer at y 884 (price 116) is 6 px above it.
    expect(magnetPrice(116, 884, BAR, priceToY, "weak")).toBe(110);
  });

  it("weak: leaves a point whose nearest O/H/L/C is 20 px away raw", () => {
    expect(magnetPrice(130, 870, BAR, priceToY, "weak")).toBe(130);
  });

  it("strong: always the nearest of O/H/L/C, however far", () => {
    expect(magnetPrice(130, 870, BAR, priceToY, "strong")).toBe(110);
    expect(magnetPrice(91, 909, BAR, priceToY, "strong")).toBe(90);
    expect(magnetPrice(102, 898, BAR, priceToY, "strong")).toBe(100);
  });

  it("leaves the price raw with no bar under the pointer (a whitespace slot) or the magnet off", () => {
    expect(magnetPrice(116, 884, null, priceToY, "strong")).toBe(116);
    expect(magnetPrice(116, 884, BAR, priceToY, "off")).toBe(116);
  });

  it("skips a candidate the scale cannot place", () => {
    const partial = (price: number): number | null => (price === 110 ? null : 1000 - price);
    expect(magnetPrice(116, 884, BAR, partial, "strong")).toBe(105);
  });
});

describe("Shift angle constraint (Story 33.10)", () => {
  const from = { x: 100, y: 100 };

  it("snaps 30 degrees to the nearer 45, projected onto that direction", () => {
    const to = { x: 100 + 10 * Math.cos(Math.PI / 6), y: 100 + 10 * Math.sin(Math.PI / 6) };
    const at = constrainAngle(from, to);
    expect(at.x - from.x).toBeCloseTo(at.y - from.y, 9);
    expect(Math.hypot(at.x - from.x, at.y - from.y)).toBeCloseTo(10 * Math.cos(Math.PI / 12), 9);
  });

  it("snaps a near-horizontal and a near-vertical drag exactly onto the first point's y and x", () => {
    expect(constrainAngle(from, { x: 200, y: 110 })).toEqual({ x: 200, y: 100 });
    expect(constrainAngle(from, { x: 95, y: 20 })).toEqual({ x: 100, y: 20 });
    expect(constrainAngle(from, { x: 0, y: 103 })).toEqual({ x: 0, y: 100 });
  });

  it("leaves a point on the first one alone, and applies to the two-point line kinds only", () => {
    expect(constrainAngle(from, from)).toEqual(from);
    expect(["trendline", "ray", "extended", "arrow", "channel"].every(constrainsAngle)).toBe(true);
    expect(["rect", "fib", "vline", "text", "price_range"].some(constrainsAngle)).toBe(false);
  });
});

const line = (n: number): Drawing => ({ kind: "hline", id: `hline-${n}`, price: n });
const add = (n: number) => ({ type: "edit" as const, update: (all: Drawing[]) => [...all, line(n)] });
const ids = (state: DrawingsHistory): string[] => state.drawings.map((d) => d.id);

function edits(count: number): DrawingsHistory {
  let state = drawingsReducer(EMPTY_HISTORY, { type: "load", drawings: [] });
  for (let n = 1; n <= count; n++) state = drawingsReducer(state, add(n));
  return state;
}

describe("undo / redo history (Story 33.10)", () => {
  it("steps back two edits, and a new edit clears what could be redone", () => {
    let state = edits(3);
    state = drawingsReducer(state, { type: "undo" });
    state = drawingsReducer(state, { type: "undo" });
    expect(ids(state)).toEqual(["hline-1"]);
    state = drawingsReducer(state, { type: "redo" });
    expect(ids(state)).toEqual(["hline-1", "hline-2"]);
    state = drawingsReducer(state, add(9));
    expect(ids(state)).toEqual(["hline-1", "hline-2", "hline-9"]);
    expect(state.future).toEqual([]);
    expect(drawingsReducer(state, { type: "redo" })).toBe(state);
  });

  it("is a no-op with no history, and an edit that changes nothing records nothing", () => {
    const loaded = drawingsReducer(EMPTY_HISTORY, { type: "load", drawings: [line(1)] });
    expect(drawingsReducer(loaded, { type: "undo" })).toBe(loaded);
    expect(drawingsReducer(loaded, { type: "edit", update: (all) => all })).toBe(loaded);
  });

  it("keeps at most 100 undo steps: of 150 edits the oldest 50 are dropped", () => {
    let state = edits(150);
    expect(state.past).toHaveLength(HISTORY_LIMIT);
    for (let i = 0; i < 150; i++) state = drawingsReducer(state, { type: "undo" });
    expect(state.drawings).toHaveLength(50);
    expect(state.future).toHaveLength(HISTORY_LIMIT);
  });

  it("makes the 40 moves of one drag gesture one undo step", () => {
    let state = edits(1);
    for (let move = 1; move <= 40; move++) {
      state = drawingsReducer(state, {
        type: "edit",
        update: (all) => all.map((d) => (d.kind === "hline" ? { ...d, price: 100 + move } : d)),
        gesture: "drag:1",
      });
    }
    expect(state.drawings[0]).toMatchObject({ price: 140 });
    state = drawingsReducer(state, { type: "undo" });
    expect(state.drawings[0]).toMatchObject({ price: 1 });
    // A second drag is its own step.
    state = drawingsReducer(state, { type: "redo" });
    state = drawingsReducer(state, { type: "edit", update: (all) => [{ ...all[0], price: 7 }], gesture: "drag:2" });
    expect(drawingsReducer(state, { type: "undo" }).drawings[0]).toMatchObject({ price: 140 });
  });

  it("resets the history on a load, which is not undoable", () => {
    const state = drawingsReducer(edits(3), { type: "load", drawings: [line(7)] });
    expect(state).toEqual({ drawings: [line(7)], past: [], future: [], gesture: null });
  });
});

describe("no-op edits and drag gestures (Story 33.10)", () => {
  it("returns the list itself when the change stores what the drawing had, key order aside", () => {
    const fib: Drawing = {
      kind: "fib",
      id: "fib-1",
      anchors: [{ time: 1, price: 1 }, { time: 2, price: 2 }],
      levels: [{ ratio: 0.5, enabled: true, color: "#1" }],
      extend_right: true,
      label_side: "left",
      line_width: 1,
    };
    const all = [line(1), fib];
    expect(replaceDrawing(all, "fib-1", (d) => JSON.parse(JSON.stringify(d)) as Drawing)).toBe(all);
    expect(replaceDrawing(all, "hline-1", (d) => ({ ...d, color: (d as { color?: string }).color }))).toBe(all);
    expect(replaceDrawing(all, "hline-9", () => line(9))).toBe(all); // no such drawing
    const moved = replaceDrawing(all, "hline-1", (d) => ({ ...d, price: 2 }) as Drawing);
    expect(moved).not.toBe(all);
    expect(moved[1]).toBe(fib);
  });

  it("gives every drag a fresh gesture, so two drags are never one undo step", () => {
    expect(nextDragGesture()).not.toBe(nextDragGesture());
  });
});
