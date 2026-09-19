import { describe, expect, it } from "vitest";

import { buildVolumeProfile } from "../../../lib/volumeProfile";
import type { Time } from "lightweight-charts";
import { vi } from "vitest";

import { VolumeProfilePrimitive, layoutProfile, type ResolvedProfileSpec, type VolumeProfileRenderSpec } from "./VolumeProfilePrimitive";

// Range 0..4 in 4 size-1 rows, each candle inside one row: totals 2, 8, 4, 1 -- row 1 is
// the POC (all up), row 2 is all down.
const profile = buildVolumeProfile(
  [
    { open: 0, high: 0.9, low: 0, close: 0.5, volume: 2 },
    { open: 1, high: 1.9, low: 1, close: 1.5, volume: 8 },
    { open: 2.5, high: 2.9, low: 2, close: 2.1, volume: 4 },
    { open: 3.1, high: 4, low: 3.1, close: 3.5, volume: 1 },
  ],
  4,
);
const ys = [0, 1, 2, 3].map((i) => ({ y1: (3 - i) * 10, y2: (4 - i) * 10 }));
const spec = (over: Partial<ResolvedProfileSpec> = {}): ResolvedProfileSpec => ({
  profile,
  xAnchor: 5,
  width: 100,
  upColor: "#0f0",
  downColor: "#f00",
  showPoc: true,
  showValueArea: true,
  ...over,
});

describe("layoutProfile (Story 18.5)", () => {
  it("scales bar length to the fullest row and marks exactly one POC row", () => {
    const { rows } = layoutProfile(spec(), ys, 400);
    const lengths = rows.map((r) => r.upW + r.downW);

    expect(Math.max(...lengths)).toBeCloseTo(100);
    expect(rows.filter((r) => r.isPoc)).toHaveLength(1);
    expect(rows.find((r) => r.isPoc)!.upW + rows.find((r) => r.isPoc)!.downW).toBeCloseTo(100);
  });

  it("grows rightward from a fixed x anchor, up volume against the anchor", () => {
    const { rows } = layoutProfile(spec({ xAnchor: 5 }), ys, 400);
    const poc = rows.find((r) => r.isPoc)!;

    expect(poc.upX).toBe(5);
    expect(poc.downX).toBeCloseTo(5 + poc.upW);
  });

  it("grows leftward from the pane's right edge for a right anchor", () => {
    const { rows, band } = layoutProfile(spec({ xAnchor: "right" }), ys, 400);
    const poc = rows.find((r) => r.isPoc)!;

    expect(poc.upX + poc.upW).toBeCloseTo(400);
    expect(band!.x + band!.w).toBeCloseTo(400);
  });

  it("skips rows that are off the price scale and yields no band when nothing is drawable", () => {
    const { rows } = layoutProfile(spec(), [null, ys[1], null, null], 400);
    expect(rows).toHaveLength(1);

    expect(layoutProfile(spec(), [null, null, null, null], 400).band).toBeNull();
  });

  it("the value-area band spans exactly the rows inside VAL..VAH", () => {
    const { band } = layoutProfile(spec(), ys, 400);

    // POC row 1 (8) of total 15; 70% = 10.5 -> +row 2 (4) = 12 -> rows 1..2 -> y 10..30 in these ys
    expect(band!.y).toBe(ys[2].y1);
    expect(band!.y + band!.h).toBe(ys[1].y2);
  });
});

describe("VolumeProfilePrimitive time anchors (Story 18.6)", () => {
  const timeSpec: VolumeProfileRenderSpec = {
    ...spec(),
    xAnchor: { time: 100 as Time },
    width: { toTime: 300 as Time },
    edges: { startTime: 100 as Time, endTime: 300 as Time },
  };

  const attach = (primitive: VolumeProfilePrimitive, offset: () => number) =>
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => t / 2 + offset() }) },
      series: { priceToCoordinate: (p: number) => p * 10 },
      requestUpdate: vi.fn(),
    } as never);

  it("re-resolves the anchor and range width from times on every redraw (follows pan/zoom)", () => {
    let offset = 0;
    const primitive = new VolumeProfilePrimitive(timeSpec);
    attach(primitive, () => offset);

    primitive.updateAllViews();
    expect(primitive.resolvedAnchor()).toEqual({ xAnchor: 50, width: 100 });

    offset = 20; // panned: same times, new pixels; width (range span) unchanged
    primitive.updateAllViews();
    expect(primitive.resolvedAnchor()).toEqual({ xAnchor: 70, width: 100 });
  });

  it("has nothing drawable while a time has no coordinate", () => {
    const primitive = new VolumeProfilePrimitive(timeSpec);
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => (t === 300 ? null : t) }) },
      series: { priceToCoordinate: (p: number) => p },
      requestUpdate: vi.fn(),
    } as never);

    primitive.updateAllViews();

    expect(primitive.resolvedAnchor()).toBeNull();
  });
});
