import { describe, expect, it } from "vitest";

import { tpoRows } from "../../../lib/tpo";
import { buildVolumeProfile } from "../../../lib/volumeProfile";
import { attachTo, drawPrimitive, fakeContext } from "../../../test/drawingKit";
import type { Time } from "lightweight-charts";
import { vi } from "vitest";

import { VolumeProfilePrimitive, layoutProfile, layoutTpo, type ResolvedProfileSpec, type VolumeProfileRenderSpec } from "./VolumeProfilePrimitive";

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

describe("VolumeProfilePrimitive session options (Story 18.8)", () => {
  const timeSpec = (over: Partial<VolumeProfileRenderSpec> = {}): VolumeProfileRenderSpec => ({
    ...spec(),
    xAnchor: { time: 100 as Time },
    width: { toTime: 300 as Time },
    ...over,
  });
  const attach = (primitive: VolumeProfilePrimitive, rowHeightPx: number) =>
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate: (t: number) => t / 2 }) },
      // Row i spans rowHeightPx pixels: price p -> p * rowHeightPx (rows are size 1 apart).
      series: { priceToCoordinate: (p: number) => p * rowHeightPx },
      requestUpdate: vi.fn(),
    } as never);

  const drawnRowHeights = (primitive: VolumeProfilePrimitive): number[] => {
    const heights: number[] = [];
    const context = {
      globalAlpha: 1,
      fillRect: (_x: number, _y: number, _w: number, h: number) => heights.push(h),
      strokeRect: () => {},
      fillStyle: "",
      strokeStyle: "",
      lineWidth: 0,
      setLineDash: () => {},
      beginPath: () => {},
      moveTo: () => {},
      lineTo: () => {},
      stroke: () => {},
    };
    primitive.updateAllViews();
    primitive.paneViews()[0].renderer()!.draw({
      useBitmapCoordinateSpace: (cb: (scope: unknown) => void) =>
        cb({ context, bitmapSize: { width: 400, height: 300 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 }),
    } as never);
    return heights;
  };

  it("scales a time-range width by widthFraction", () => {
    const primitive = new VolumeProfilePrimitive(timeSpec({ widthFraction: 0.5 }));
    attach(primitive, 10);

    primitive.updateAllViews();

    expect(primitive.resolvedAnchor()).toEqual({ xAnchor: 50, width: 50 }); // range 100px * 0.5
  });

  it("respondsToZoom drops the inter-row gap only once rows get too short, and never otherwise", () => {
    const tall = new VolumeProfilePrimitive(timeSpec({ respondsToZoom: true, showValueArea: false }));
    attach(tall, 10);
    expect(Math.min(...drawnRowHeights(tall))).toBe(9); // 10px rows keep their 1px gap

    const shortZoom = new VolumeProfilePrimitive(timeSpec({ respondsToZoom: true, showValueArea: false }));
    attach(shortZoom, 2);
    expect(Math.min(...drawnRowHeights(shortZoom))).toBe(2); // 2px rows: gap dropped

    const shortFixed = new VolumeProfilePrimitive(timeSpec({ respondsToZoom: false, showValueArea: false }));
    attach(shortFixed, 2);
    expect(Math.min(...drawnRowHeights(shortFixed))).toBe(1); // gap kept, row squeezed to 1px
  });
});

describe("TPO rendering (Story 32.7)", () => {
  // Four rows of 20 over 0..80: row 0 touched once, row 1 three times (two up, one down), row 2 never,
  // row 3 41 times (40 candles and the last one).
  const touching = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ open: 70, high: 79, low: 61, close: i % 2 === 0 ? 75 : 65, volume: 1 }));
  const candles = [
    { open: 1, high: 10, low: 0, close: 5, volume: 1 },
    ...[1, 2, 3].map((i) => ({ open: 30, high: 39, low: 21, close: i === 2 ? 25 : 35, volume: 1 })),
    ...touching(40),
    { open: 79, high: 80, low: 79.5, close: 79.8, volume: 1 },
  ];
  const timeProfile = buildVolumeProfile(candles, 4, 0.7, "time");
  const rows = tpoRows(timeProfile, candles);

  const tpoSpec = (over: Partial<VolumeProfileRenderSpec> = {}): VolumeProfileRenderSpec => ({
    profile: timeProfile,
    xAnchor: { time: 100 as Time },
    width: { toTime: 700 as Time },
    upColor: "#0f0",
    downColor: "#f00",
    showPoc: true,
    showValueArea: false,
    tpo: { rows, letters: false },
    ...over,
  });
  // y of price p is 1000 - p (the drawing kit), so each 20-wide row is 20 px tall.
  const draw = (spec: VolumeProfileRenderSpec) => {
    const primitive = new VolumeProfilePrimitive(spec);
    attachTo(primitive);
    return drawPrimitive(primitive, 1000);
  };

  it("counts the rows from the one engine: the 41-touch row dominates and overflows by 11", () => {
    expect(rows.map((r) => r.count)).toEqual([1, 3, 0, 41]);
    expect(rows[3]).toMatchObject({ blocks: 30, overflow: 11 });
  });

  it("lays blocks side by side from the anchor, the fullest row spanning the width, with one longer overflow bar", () => {
    const resolved = { ...spec(), profile: timeProfile, xAnchor: 100, width: 600, tpo: { rows, letters: false } };
    const laid = layoutTpo(resolved, [0, 1, 2, 3].map((i) => ({ y1: (3 - i) * 20, y2: (4 - i) * 20 })), 1000);
    const fullest = Math.max(...rows.map((r) => r.count));
    const unit = 600 / fullest;
    const overflowing = laid.find((r) => r.overflow !== null)!;

    expect(overflowing.blocks).toHaveLength(30);
    expect(overflowing.blocks[0]).toMatchObject({ x: 100, w: unit });
    expect(overflowing.blocks[1].x).toBeCloseTo(100 + unit);
    expect(overflowing.overflow!.x).toBeCloseTo(100 + 30 * unit);
    expect(overflowing.overflow!.w).toBeCloseTo(11 * unit);
    // a row's whole length is proportional to its touches
    const lengthOf = (r: (typeof laid)[number]) => r.blocks.length * unit + (r.overflow?.w ?? 0);
    expect(Math.max(...laid.map(lengthOf))).toBeCloseTo(600);
  });

  it("marks exactly one row as the POC (the fullest)", () => {
    const resolved = { ...spec(), profile: timeProfile, xAnchor: 0, width: 600, tpo: { rows, letters: false } };
    // row 2 has no touches and no span: it is skipped, as a row off the price scale is
    const laid = layoutTpo(resolved, [{ y1: 3, y2: 4 }, { y1: 2, y2: 3 }, null, { y1: 0, y2: 1 }], 1000);

    expect(laid.filter((r) => r.isPoc)).toHaveLength(1);
    expect(laid.find((r) => r.isPoc)!.overflow).not.toBeNull(); // the 41-touch row
  });

  it("draws one filled block per touch (capped) and one bar per overflowing row, in the candle's side colour", () => {
    const { fills } = draw(tpoSpec());
    const expectedBlocks = rows.reduce((n, r) => n + r.blocks, 0);
    const expectedOverflow = rows.filter((r) => r.overflow > 0).length;

    expect(fills).toHaveLength(expectedBlocks + expectedOverflow);
    expect(new Set(fills.map((f) => f.style))).toEqual(new Set(["#0f0", "#f00"]));
  });

  it("draws letters instead of nothing extra only when asked, and only where a block is wide enough", () => {
    expect(draw(tpoSpec()).texts).toHaveLength(0);

    const lettered = draw(tpoSpec({ tpo: { rows, letters: true } }));
    expect(lettered.texts.length).toBeGreaterThan(0);
    expect(lettered.texts.every((t) => /^[A-Za-z]$/.test(t.text))).toBe(true);
    // A very narrow profile makes blocks too thin to hold a letter.
    const narrow = draw(tpoSpec({ width: { toTime: 101 as Time }, tpo: { rows, letters: true } }));
    expect(narrow.texts).toHaveLength(0);
  });

  it("outlines the initial balance between its prices across the profile, and only for a TPO", () => {
    const primitive = new VolumeProfilePrimitive(tpoSpec({ initialBalance: { high: 60, low: 20 } }));
    attachTo(primitive);
    const strokeRect = vi.fn();
    const recorded = drawWith(primitive, strokeRect);

    // x from the anchor (100) across the width (600), y from the high (940) to the low (980)
    expect(recorded).toContainEqual([100, 940, 600, 40]);

    const plain = new VolumeProfilePrimitive({ ...tpoSpec(), tpo: undefined, initialBalance: { high: 60, low: 20 } });
    attachTo(plain);
    const plainRects: number[][] = [];
    drawWith(plain, (...args: number[]) => plainRects.push(args));
    expect(plainRects).not.toContainEqual([100, 940, 600, 40]);
  });
});

function drawWith(primitive: VolumeProfilePrimitive, strokeRect: (...args: number[]) => void): number[][] {
  const calls: number[][] = [];
  const recorded = fakeContext();
  recorded.context.strokeRect = ((...args: number[]) => {
    calls.push(args);
    strokeRect(...args);
  }) as never;
  primitive.paneViews()[0].renderer()!.draw({
    useBitmapCoordinateSpace: (cb: (scope: unknown) => void) =>
      cb({ context: recorded.context, bitmapSize: { width: 1000, height: 500 }, horizontalPixelRatio: 1, verticalPixelRatio: 1 }),
  } as never);
  return calls;
}
