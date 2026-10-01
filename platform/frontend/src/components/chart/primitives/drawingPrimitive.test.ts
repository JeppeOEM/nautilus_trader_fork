import { describe, expect, it } from "vitest";

import { BarGrid, distanceToSegment, nearestHandle } from "./drawingPrimitive";

describe("BarGrid (Story 32.5)", () => {
  const grid = new BarGrid();
  grid.set([100, 200, 300]);

  it("snaps a time to the latest bar at or before it, and nothing before the first bar", () => {
    expect(grid.snap(250)).toBe(200);
    expect(grid.snap(300)).toBe(300);
    expect(grid.snap(999)).toBe(300);
    expect(grid.snap(99)).toBeNull();
    expect(grid.indexOf(250)).toBe(1);
  });

  it("keeps the time as given while there are no bars (nothing to snap to)", () => {
    const empty = new BarGrid();
    expect(empty.snap(5)).toBe(5);
    expect(empty.indexOf(5)).toBeNull();
    expect(empty.timeAtLogical(1)).toBeNull();
  });

  it("maps a logical index to a bar time, clamped to the loaded bars", () => {
    expect(grid.timeAtLogical(1.4)).toBe(200);
    expect(grid.timeAtLogical(-3)).toBe(100);
    expect(grid.timeAtLogical(99)).toBe(300);
  });
});

describe("hit helpers (Story 32.5)", () => {
  it("measures the distance to a segment, clamped at its ends", () => {
    expect(distanceToSegment(5, 3, 0, 0, 10, 0)).toBe(3);
    expect(distanceToSegment(-4, 3, 0, 0, 10, 0)).toBe(5);
    expect(distanceToSegment(3, 4, 0, 0, 0, 0)).toBe(5);
  });

  it("picks the nearest handle within the grab radius, else none", () => {
    const handles = [
      { id: "a", x: 0, y: 0 },
      { id: "b", x: 6, y: 0 },
    ];
    expect(nearestHandle(handles, 5, 0)).toMatchObject({ handle: "b" });
    expect(nearestHandle(handles, 50, 50)).toBeNull();
  });
});
