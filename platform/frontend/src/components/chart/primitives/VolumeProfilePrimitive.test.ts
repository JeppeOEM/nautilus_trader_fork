import { describe, expect, it } from "vitest";

import { buildVolumeProfile } from "../../../lib/volumeProfile";
import type { Time } from "lightweight-charts";
import { vi } from "vitest";

import { CHART_TOKENS } from "../chartTheme";
import {
  VolumeProfilePrimitive,
  layoutProfile,
  snapRow,
  snapSpan,
  type ResolvedProfileSpec,
  type VolumeProfileRenderSpec,
} from "./VolumeProfilePrimitive";

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
    const { rows, bands } = layoutProfile(spec({ xAnchor: "right" }), ys, 400);
    const poc = rows.find((r) => r.isPoc)!;

    expect(poc.upX + poc.upW).toBeCloseTo(400);
    expect(bands[0].x + bands[0].w).toBeCloseTo(400);
  });

  it("skips rows that are off the price scale and yields no band when nothing is drawable", () => {
    const { rows } = layoutProfile(spec(), [null, ys[1], null, null], 400);
    expect(rows).toHaveLength(1);

    expect(layoutProfile(spec(), [null, null, null, null], 400).bands).toEqual([]);
  });

  it("the value-area band spans exactly the rows inside VAL..VAH", () => {
    const { bands } = layoutProfile(spec(), ys, 400);

    // POC row 1 (8) of total 15; 70% = 10.5 -> +row 2 (4) = 12 -> rows 1..2 -> y 10..30 in these ys
    expect(bands).toHaveLength(1);
    expect(bands[0].y).toBe(ys[2].y1);
    expect(bands[0].y + bands[0].h).toBe(ys[1].y2);
  });

  it("splits the value-area band at a row that is off the price scale, never bridging it (DW-149)", () => {
    // 10 rows of equal volume: the 70% value area is 7 rows around the POC (row 0, the first of
    // the equal maxima) -- rows 0..6. Row 3 is off the scale.
    const flat = buildVolumeProfile(
      Array.from({ length: 10 }, (_, i) => ({ open: i, high: i + 0.9, low: i, close: i + 0.5, volume: 1 })),
      10,
    );
    const inValueArea = flat.rows.flatMap((r, i) => (r.priceHigh <= flat.vah && r.priceLow >= flat.val ? [i] : []));
    const rowYs = flat.rows.map((_, i) => (i === inValueArea[2] ? null : { y1: (9 - i) * 10, y2: (10 - i) * 10 }));

    const { bands } = layoutProfile(spec({ profile: flat }), rowYs, 400);

    const split = inValueArea[2];
    const below = inValueArea.filter((i) => i < split);
    const above = inValueArea.filter((i) => i > split);
    expect(bands).toHaveLength(2);
    expect(bands.map((b) => [b.y, b.y + b.h])).toEqual([
      [rowYs[below.at(-1)!]!.y1, rowYs[below[0]]!.y2],
      [rowYs[above.at(-1)!]!.y1, rowYs[above[0]]!.y2],
    ]);
  });

  it("caps a numeric width at maxWidthFraction of the pane (DW-151: a narrow pane)", () => {
    const longest = (paneWidth: number, over: Partial<ResolvedProfileSpec>) =>
      Math.max(...layoutProfile(spec({ xAnchor: "right", width: 150, ...over }), ys, paneWidth).rows.map((r) => r.upW + r.downW));

    expect(longest(300, { maxWidthFraction: 0.3 })).toBeCloseTo(90);
    expect(longest(1000, { maxWidthFraction: 0.3 })).toBeCloseTo(150); // wide pane: the px width stands
    expect(longest(300, {})).toBeCloseTo(150);
  });
});

describe("bitmap pixel snapping (DW-151)", () => {
  it("rounds both edges, so touching spans still touch and every value is a whole pixel", () => {
    const a = snapSpan(10.3, 4.6, 1.5);
    const b = snapSpan(14.9, 3.2, 1.5);

    expect([a.start, a.length]).toEqual([15, 7]); // 15.45 -> 15, 22.35 -> 22
    expect(a.start + a.length).toBe(b.start); // no seam, no overlap
  });

  it("gives a row an integer top and height with a gap of at least one device pixel", () => {
    const row = snapRow(10.3, 4.6, 1.5, true);

    expect(Number.isInteger(row.top) && Number.isInteger(row.height)).toBe(true);
    expect(row.top).toBe(15);
    expect(snapSpan(10.3, 4.6, 1.5).length - row.height).toBeGreaterThanOrEqual(1);
    expect(snapRow(10.3, 4.6, 1.5, false).height).toBe(7); // the zoom-dropped gap
  });

  it("never draws a row under one device pixel tall", () => {
    expect(snapRow(10, 0.4, 1, true).height).toBe(1);
    expect(snapRow(10, 1, 2, true).height).toBe(1);
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

describe("VolumeProfilePrimitive anchor offsets (DW-152)", () => {
  // Bars 60 s apart, 10 px apart: logical index i sits at x = 10 * i, time t at x = t / 6.
  const attach = (primitive: VolumeProfilePrimitive, timeToCoordinate: (t: number) => number | null = (t) => t / 6) =>
    primitive.attached({
      chart: { timeScale: () => ({ timeToCoordinate, logicalToCoordinate: (i: number) => 10 * i }) },
      series: { priceToCoordinate: (p: number) => p * 10 },
      requestUpdate: vi.fn(),
    } as never);
  const offsetSpec = (over: Partial<VolumeProfileRenderSpec> = {}): VolumeProfileRenderSpec => ({
    ...spec(),
    xAnchor: { time: 600 as Time, offsetSeconds: -300 },
    width: { toTime: 600 as Time, offsetSeconds: 120 },
    barSeconds: 60,
    ...over,
  });

  it("shifts an anchor by offsetSeconds / barSeconds bars at the chart's bar spacing", () => {
    const primitive = new VolumeProfilePrimitive(offsetSpec());
    attach(primitive);

    primitive.updateAllViews();

    // anchor 100 - 5 bars * 10 px = 50; end 100 + 2 bars * 10 px = 120 -> width 70
    expect(primitive.resolvedAnchor()).toEqual({ xAnchor: 50, width: 70 });
  });

  it("gives a single-bar range a positive width from its end offset", () => {
    const primitive = new VolumeProfilePrimitive(
      offsetSpec({ xAnchor: { time: 600 as Time, offsetSeconds: 0 }, width: { toTime: 600 as Time, offsetSeconds: 60 }, widthFraction: 0.7 }),
    );
    attach(primitive);

    primitive.updateAllViews();

    expect(primitive.resolvedAnchor()!.width).toBeCloseTo(7); // one bar (10 px) * 0.7
  });

  it("draws nothing for an offset it cannot convert, or a time without a coordinate", () => {
    const noBarSize = new VolumeProfilePrimitive(offsetSpec({ barSeconds: undefined }));
    attach(noBarSize);
    noBarSize.updateAllViews();
    expect(noBarSize.resolvedAnchor()).toBeNull();

    const offScreen = new VolumeProfilePrimitive(offsetSpec());
    attach(offScreen, () => null);
    offScreen.updateAllViews();
    expect(offScreen.resolvedAnchor()).toBeNull();
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

  it("paints the value area in the --chart-value-area token, at whole bitmap pixels (DW-149/151)", () => {
    const fills: { style: string; alpha: number; rect: number[] }[] = [];
    const context = {
      globalAlpha: 1,
      fillStyle: "",
      fillRect(x: number, y: number, w: number, h: number) {
        fills.push({ style: this.fillStyle, alpha: this.globalAlpha, rect: [x, y, w, h] });
      },
      strokeRect: () => {},
      strokeStyle: "",
      lineWidth: 0,
    };
    const primitive = new VolumeProfilePrimitive(timeSpec({ widthFraction: 0.37 }));
    attach(primitive, 3.3);
    primitive.updateAllViews();
    primitive.paneViews()[0].renderer()!.draw({
      useBitmapCoordinateSpace: (cb: (scope: unknown) => void) =>
        cb({ context, bitmapSize: { width: 600, height: 450 }, horizontalPixelRatio: 1.5, verticalPixelRatio: 1.5 }),
    } as never);

    const band = fills.filter((f) => f.alpha < 1);
    expect(band.map((f) => f.style)).toEqual([CHART_TOKENS["--chart-value-area"]]);
    expect(fills.flatMap((f) => f.rect).every(Number.isInteger)).toBe(true);
  });
});
