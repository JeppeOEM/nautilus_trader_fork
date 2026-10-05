import { describe, expect, it, vi } from "vitest";

import type { FootprintItem } from "../../../api/schema";
import { BUILT_IN_LAYOUT, type FootprintSettings } from "../../../lib/chartLayout";
import { drawPrimitive } from "../../../test/drawingKit";
import { CHART_TOKENS } from "../chartTheme";
import { FOOTPRINT_TEXT_MIN_BAR_SPACING, FOOTPRINT_VEIL_ALPHA, FootprintPrimitive, type FootprintRenderSpec } from "./FootprintPrimitive";
import { GAP_FILL_ALPHA } from "./GapPrimitive";

const ON: FootprintSettings = { ...BUILT_IN_LAYOUT.footprint, on: true };

// Precision 2: rows of 0.50 at 100.00 and 100.50. Buy 9 at 100.50 against sell 2 at 100.00 is a buy
// imbalance; 100.00's sell 2 against 100.50's buy 9 is none. The POC is 100.50 (total 10).
const P2_BAR: FootprintItem = {
  t: 60_000,
  row_ticks: 50,
  rows: [
    { p: 10_000, b: 100, s: 200 },
    { p: 10_050, b: 900, s: 100 },
  ],
  delta: 700,
  total: 1300,
  poc_row: 10_050,
  no_trades: false,
};

/** A fake chart: bar time t s sits at x = t, a price's y is `offset - price * scale`. */
function attach(
  primitive: FootprintPrimitive,
  barSpacing: number,
  scale = 20,
  offset = 3000,
  visible = { from: 0, to: 10_000 },
  priceToY: (p: number) => number | null = (p) => offset - p * scale,
): void {
  primitive.attached({
    chart: {
      timeScale: () => ({
        options: () => ({ barSpacing }),
        getVisibleRange: () => visible,
        timeToCoordinate: (t: number) => (t <= 1_000 ? t : null),
      }),
    },
    series: { priceToCoordinate: priceToY },
    requestUpdate: vi.fn(),
  } as never);
  primitive.updateAllViews();
}

const spec = (items: FootprintItem[], over: Partial<FootprintRenderSpec> = {}): FootprintRenderSpec => ({
  items,
  precision: { price: 2, size: 2 },
  settings: ON,
  ...over,
});

function drawn(items: FootprintItem[], barSpacing = 60, over: Partial<FootprintRenderSpec> = {}) {
  const primitive = new FootprintPrimitive(spec(items, over));
  attach(primitive, barSpacing);
  return { primitive, ...drawPrimitive(primitive) };
}

describe("FootprintPrimitive", () => {
  it("lays out one cell per row, spanning the row's integer price edges", () => {
    const { primitive } = drawn([P2_BAR]);
    const [bar] = primitive.layout();

    expect(bar.x).toBe(60);
    expect(bar.width).toBeCloseTo(54);
    expect(bar.cells.map((c) => [c.top, c.bottom])).toEqual([
      [990, 1000], // 100.00 .. 100.50
      [980, 990], // 100.50 .. 101.00
    ]);
    expect(bar.cells.map((c) => c.heat)).toEqual([0.3, 1]);
  });

  it("veils the candle beneath, then fills each row's sell (left) and buy (right) half", () => {
    const { fills } = drawn([P2_BAR]);

    expect(fills[0]).toMatchObject({ alpha: FOOTPRINT_VEIL_ALPHA, style: CHART_TOKENS["--chart-bg"], y: 980, h: 20 });
    const cells = fills.slice(1);
    expect(cells.map((f) => [f.x, f.style])).toEqual([
      [33, CHART_TOKENS["--chart-down"]],
      [60, CHART_TOKENS["--chart-up"]],
      [33, CHART_TOKENS["--chart-down"]],
      [60, CHART_TOKENS["--chart-up"]],
    ]);
    expect(cells[2].alpha).toBeGreaterThan(cells[0].alpha); // the fuller row is hotter
  });

  it("uses the layout's own buy and sell colours when set", () => {
    const { fills } = drawn([P2_BAR], 60, { settings: { ...ON, buy_color: "#0000ff", sell_color: "#ff00ff" } });

    expect(fills.slice(1, 3).map((f) => f.style)).toEqual(["#ff00ff", "#0000ff"]);
  });

  it("outlines the POC row and the diagonal imbalances in the up / down tokens", () => {
    const { rects } = drawn([P2_BAR]);

    expect(rects.filter((r) => r.style === CHART_TOKENS["--chart-poc"])).toEqual([
      expect.objectContaining({ x: 33, y: 980, w: 54, h: 10 }),
    ]);
    // Buy imbalances: 100.50 (9 >= 3 x 2) and 100.00 (1 against no row below); on the right half.
    expect(rects.filter((r) => r.style === CHART_TOKENS["--chart-up"]).map((r) => [r.x, r.y])).toEqual([
      [60, 990],
      [60, 980],
    ]);
    // Sell imbalance: 100.50's sell 1 against no row above.
    expect(rects.filter((r) => r.style === CHART_TOKENS["--chart-down"]).map((r) => [r.x, r.y])).toEqual([[33, 980]]);
  });

  it("prints sell × buy and the footer at a precision-2 size, the imbalanced buy in the up colour", () => {
    const { texts } = drawn([P2_BAR], 100);

    expect(texts.map((t) => t.text)).toEqual(["2.00 × 1.00", "1.00 × 9.00", "+7.00", "13.00"]);
    expect(texts[1].style).toBe(CHART_TOKENS["--chart-up"]);
    expect(texts[2].style).toBe(CHART_TOKENS["--chart-up"]);
  });

  it("prints a precision-6 instrument's prices and sizes with no float noise", () => {
    const item: FootprintItem = {
      t: 60_000,
      row_ticks: 10,
      rows: [{ p: 1230, b: 1, s: 3 }],
      delta: -2,
      total: 4,
      poc_row: 1230,
      no_trades: false,
    };
    const primitive = new FootprintPrimitive(spec([item], { precision: { price: 6, size: 6 }, settings: { ...ON, mode: "delta" } }));
    attach(primitive, 120, 1_000_000, 2000); // 0.00123 -> 770, 0.00124 -> 760
    const { texts } = drawPrimitive(primitive);

    expect(primitive.layout()[0].cells.map((c) => [c.top, c.bottom])).toEqual([[760, 770]]);
    expect(texts.map((t) => t.text)).toEqual(["-0.000002", "-0.000002", "0.000004"]);
  });

  it("hides every number below the bar-spacing threshold, keeping the cells as heat", () => {
    const zoomedOut = drawn([P2_BAR], 4);

    expect(FOOTPRINT_TEXT_MIN_BAR_SPACING).toBeGreaterThan(4);
    expect(zoomedOut.texts).toEqual([]);
    expect(zoomedOut.fills).toHaveLength(5);
    expect(drawn([P2_BAR], 60, { settings: { ...ON, text: false } }).texts).toEqual([]);
  });

  it("skips a cell text wider than its column", () => {
    const { texts } = drawn([P2_BAR], FOOTPRINT_TEXT_MIN_BAR_SPACING);

    // "2.00 × 1.00" is 66 px in the fake context, the column 36 px; the footer still prints.
    expect(texts.map((t) => t.text)).toEqual(["+7.00", "13.00"]);
  });

  it("paints a no-trades bar in the gap colour, full height, with no cells", () => {
    const empty: FootprintItem = { t: 60_000, row_ticks: null, rows: [], delta: null, total: null, poc_row: null, no_trades: true };
    const { fills, texts } = drawn([empty]);

    expect(fills).toEqual([expect.objectContaining({ alpha: GAP_FILL_ALPHA, style: CHART_TOKENS["--chart-gap"], y: 0, h: 500 })]);
    expect(texts).toEqual([]);
  });

  it("lays out one neighbour beyond each visible edge, whose column can reach onto the pane", () => {
    const at = (t: number): FootprintItem => ({ ...P2_BAR, t: t * 1000 });
    const primitive = new FootprintPrimitive(spec([at(60), at(120), at(180), at(240)]));

    attach(primitive, 60, 20, 3000, { from: 121, to: 179 });

    expect(primitive.layout().map((bar) => bar.x)).toEqual([120, 180]);
  });

  it("draws neither veil nor footer for a bar none of whose rows is on the price scale", () => {
    const primitive = new FootprintPrimitive(spec([P2_BAR]));
    attach(primitive, 60, 20, 3000, { from: 0, to: 10_000 }, () => null);
    const { fills, texts } = drawPrimitive(primitive);

    expect(fills).toEqual([]);
    expect(texts).toEqual([]);
  });

  it("draws nothing for a bar it has no item for, nor before the precision is known", () => {
    const offChart = { ...P2_BAR, t: 2_000_000 }; // a time with no slot (the forming bar's side)

    expect(drawn([]).fills).toEqual([]);
    expect(drawn([offChart]).fills).toEqual([]);
    expect(drawn([P2_BAR], 60, { precision: null }).fills).toEqual([]);
  });
});
