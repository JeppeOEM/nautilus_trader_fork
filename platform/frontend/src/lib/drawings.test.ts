/// <reference types="node" />
// Story 33.8 reads the trendline fixture shared with the Python alert engine off disk (the app
// tsconfig types only `vite/client`), hence the node types reference above.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  type DragPoint,
  type FibDrawing,
  type PositionDrawing,
  anchoredVpToForm,
  applyHandleDrag,
  defaultFibLevels,
  fibLabel,
  fibLevelPrices,
  formatPercent,
  importLegacyHlines,
  MAX_WIDTH_BARS,
  newAnchoredVp,
  newAnchoredVwap,
  newPosition,
  nextDrawingId,
  parseAnchoredVpForm,
  parseDrawings,
  parsePositionForm,
  positionLabels,
  positionStats,
  positionToForm,
  snapIndex,
  storedTime,
  trendlinePriceAt,
} from "./drawings";
import {
  DRAWING_KIND_NAMES,
  type Anchor,
  type ChannelDrawing,
  type Drawing,
  type FibExtensionDrawing,
  type NewDrawingContext,
  type RectDrawing,
  type TextDrawing,
  UnknownDrawingKindError,
  buildDrawing,
  channelOffsetFor,
  channelPlaceable,
  defaultFibExtensionLevels,
  extendOf,
  extendedSegment,
  fibExtensionLevelPrices,
  isBlankText,
  kindOfTool,
  parseTextForm,
  placementOf,
  previewDrawing,
  rangeLabels,
  rectLabel,
  textLength,
} from "./drawings";

function fib(a: number, b: number, overrides: Partial<FibDrawing> = {}): FibDrawing {
  return {
    kind: "fib",
    id: "fib-1",
    anchors: [
      { time: 100, price: a },
      { time: 200, price: b },
    ],
    levels: defaultFibLevels(() => "#000000"),
    extend_right: true,
    label_side: "left",
    line_width: 1,
    ...overrides,
  };
}

const drag = (price: number, extra: Partial<DragPoint> = {}): DragPoint => ({
  price,
  time: 300,
  barsSince: () => null,
  ...extra,
});

describe("Fibonacci levels (Story 32.5)", () => {
  it("a down-drag A=100 -> B=90 puts 0 on B and 1 on A", () => {
    const prices = fibLevelPrices(fib(100, 90), 2).map((l) => l.price);
    expect(prices).toEqual([90, 92.36, 93.82, 95, 96.18, 97.86, 100]);
  });

  it("an up-drag A=90 -> B=100 puts 0 on B (100) and 1 on A (90)", () => {
    const prices = fibLevelPrices(fib(90, 100), 2).map((l) => l.price);
    expect(prices).toEqual([100, 97.64, 96.18, 95, 93.82, 92.14, 90]);
  });

  it("draws only the default-on ratios, and an enabled extension joins in ratio order", () => {
    expect(fibLevelPrices(fib(100, 90), 2).map((l) => l.ratio)).toEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
    const levels = defaultFibLevels(() => "#000000").map((l) => (l.ratio === 1.618 ? { ...l, enabled: true } : l));
    expect(fibLevelPrices(fib(100, 90, { levels }), 2).map((l) => l.ratio).at(-1)).toBe(1.618);
  });

  it("rounds every level to the instrument's price grid, with no float noise", () => {
    // 90 + 10 * 0.236 is 92.36000000000001 in binary floating point.
    const level = fibLevelPrices(fib(100, 90), 2).find((l) => l.ratio === 0.236);
    expect(level?.price).toBe(92.36);
  });

  it("labels 'ratio (price)' with the price at a precision-2 and a precision-6 instrument", () => {
    expect(fibLabel(0.618, 96.18, 2)).toBe("0.618 (96.18)");
    expect(fibLabel(0.5, 0.123456, 6)).toBe("0.5 (0.123456)");
    expect(fibLabel(1, 0.1, 6)).toBe("1 (0.100000)");
  });
});

describe("position geometry (Story 32.5)", () => {
  it("a Long clicked at 100 gets stop 99, target 102 and R/R 2.00", () => {
    const p = newPosition("position-1", "long", 100, 100, 2);
    expect([p.entry, p.stop, p.target, p.width_bars]).toEqual([100, 99, 102, 40]);
    expect(positionLabels(p, { price: 2, size: 3 })).toMatchObject({
      target: "Target: 102.00 (+2.00 %)",
      entry: "Entry: 100.00",
      stop: "Stop: 99.00 (−1.00 %)",
      rewardRisk: "Risk/Reward: 2.00",
      size: null,
    });
  });

  it("a Short clicked at 100 mirrors it: stop 101, target 98, R/R 2.00", () => {
    const p = newPosition("position-1", "short", 100, 100, 2);
    expect([p.entry, p.stop, p.target]).toEqual([100, 101, 98]);
    expect(positionLabels(p, { price: 2, size: 3 }).rewardRisk).toBe("Risk/Reward: 2.00");
    expect(positionLabels(p, { price: 2, size: 3 }).target).toBe("Target: 98.00 (+2.00 %)");
  });

  it("a precision-6 instrument prints six decimals and no noise", () => {
    const p = newPosition("position-1", "long", 100, 0.123456, 6);
    const labels = positionLabels(p, { price: 6, size: 0 });
    expect(labels.entry).toBe("Entry: 0.123456");
    expect(labels.stop).toBe("Stop: 0.122221 (−1.00 %)");
    expect(labels.target).toBe("Target: 0.125926 (+2.00 %)");
  });

  it("snaps the clicked price to the grid and keeps a stop at least one tick away", () => {
    const p = newPosition("position-1", "long", 100, 0.0149, 2);
    expect(p.entry).toBe(0.02); // lifted to two ticks so the stop can stay above zero
    expect(p.stop).toBe(0.01);
    expect(p.target).toBeGreaterThan(p.entry);
  });

  it("sizes the position from the account and the risk percent at the size precision", () => {
    const p: PositionDrawing = { ...newPosition("p", "long", 100, 100, 2), account: 10_000, risk_pct: 1 };
    expect(positionStats(p, 3).size).toBe(100);
    expect(positionLabels(p, { price: 2, size: 3 }).size).toBe("Size: 100.000");
    expect(positionLabels(p, { price: 2, size: 0 }).size).toBe("Size: 100");
    // Floored, never rounded up past the risk: 100 / 3 = 33.33...
    const q = { ...p, entry: 100, stop: 97, target: 106 };
    expect(positionStats(q, 2).size).toBe(33.33);
  });

  it("shows no size until both the account and the risk percent are set", () => {
    const p: PositionDrawing = { ...newPosition("p", "long", 100, 100, 2), account: 10_000 };
    expect(positionStats(p, 3).size).toBeNull();
  });

  it("formats percents with a true minus and an explicit plus", () => {
    expect(formatPercent(3)).toBe("+3.00 %");
    expect(formatPercent(-1)).toBe("−1.00 %");
    expect(formatPercent(-0.001)).toBe("+0.00 %");
  });
});

describe("handle drags (Story 32.5)", () => {
  const long = newPosition("p", "long", 100, 100, 2);

  it("dragging the target to 103 gives R/R 3.00 and +3.00 %", () => {
    const moved = applyHandleDrag(long, "target", drag(103), 2) as PositionDrawing;
    const labels = positionLabels(moved, { price: 2, size: 3 });
    expect(labels.rewardRisk).toBe("Risk/Reward: 3.00");
    expect(labels.target).toBe("Target: 103.00 (+3.00 %)");
  });

  it("refuses a target dragged below the entry: it stops one tick above", () => {
    const moved = applyHandleDrag(long, "target", drag(95), 2) as PositionDrawing;
    expect(moved.target).toBe(100.01);
  });

  it("refuses a stop dragged past the entry, for a long and a short", () => {
    expect((applyHandleDrag(long, "stop", drag(105), 2) as PositionDrawing).stop).toBe(99.99);
    const short = newPosition("p", "short", 100, 100, 2);
    expect((applyHandleDrag(short, "stop", drag(95), 2) as PositionDrawing).stop).toBe(100.01);
    expect((applyHandleDrag(short, "target", drag(105), 2) as PositionDrawing).target).toBe(99.99);
  });

  it("moves the whole box with the entry handle, keeping stop and target distances", () => {
    const moved = applyHandleDrag(long, "entry", drag(110, { time: 500 }), 2) as PositionDrawing;
    expect([moved.entry, moved.stop, moved.target, moved.time]).toEqual([110, 109, 112, 500]);
  });

  it("clamps a target/stop to the precision grid with no float noise at precision 6", () => {
    const p = newPosition("p", "long", 100, 0.1, 6);
    const moved = applyHandleDrag(p, "target", drag(0.05), 6) as PositionDrawing;
    expect(moved.target).toBe(0.100001);
    const stop = applyHandleDrag(p, "stop", drag(0.3), 6) as PositionDrawing;
    expect(stop.stop).toBe(0.099999);
    const tiny = newPosition("p", "long", 100, 0.000003, 6);
    expect((applyHandleDrag(tiny, "stop", drag(-5), 6) as PositionDrawing).stop).toBe(0.000001);
    const short = newPosition("p", "short", 100, 0.000003, 6);
    expect((applyHandleDrag(short, "target", drag(-5), 6) as PositionDrawing).target).toBe(0.000001);
  });

  it("keeps entry, stop and target above zero when the entry handle is dragged down", () => {
    const moved = applyHandleDrag(long, "entry", drag(-50), 2) as PositionDrawing;
    expect(moved.stop).toBe(0.01);
    expect(moved.entry).toBeGreaterThan(moved.stop);
    expect(moved.target).toBeGreaterThan(moved.entry);
    const short = newPosition("p", "short", 100, 100, 2);
    const down = applyHandleDrag(short, "entry", drag(0), 2) as PositionDrawing;
    expect(down.target).toBe(0.01);
    expect(down.entry).toBeGreaterThan(down.target);
  });

  it("never throws printing a position with a bad entry or a non-finite price", () => {
    const bad = { ...long, entry: 0 };
    expect(() => positionLabels(bad, { price: 2, size: 3 })).not.toThrow();
    expect(positionLabels(bad, { price: 2, size: 3 }).target).toContain("(n/a)");
    const nan = { ...long, target: Number.NaN, entry: Number.POSITIVE_INFINITY };
    const labels = positionLabels(nan, { price: 2, size: 3 });
    expect(labels.entry).toBe("Entry: n/a");
    expect(formatPercent(Number.NaN)).toBe("n/a");
  });

  it("sets the width in bars from the right handle, at least one", () => {
    const moved = applyHandleDrag(long, "right", drag(100, { barsSince: () => 12 }), 2) as PositionDrawing;
    expect(moved.width_bars).toBe(12);
    const back = applyHandleDrag(long, "right", drag(100, { barsSince: () => -3 }), 2) as PositionDrawing;
    expect(back.width_bars).toBe(1);
  });

  it("moves a trendline or fib anchor to the pointer's bar and rounded price, leaving the other", () => {
    const moved = applyHandleDrag(fib(100, 90), "b", drag(91.234, { time: 250 }), 2) as FibDrawing;
    expect(moved.anchors).toEqual([
      { time: 100, price: 100 },
      { time: 250, price: 91.23 },
    ]);
  });

  it("stores a Lines-view (mid-second) bar time as whole seconds that still snap to that bar", () => {
    // 1-second snapshots are stamped at second + 0.5 s; the server stores integer UTC seconds.
    const bars = [99.5, 100.5, 101.5];
    const moved = applyHandleDrag(fib(100, 90), "b", drag(91, { time: 100.5 }), 2) as FibDrawing;
    expect(moved.anchors[1].time).toBe(101);
    expect(bars[snapIndex(bars, moved.anchors[1].time) ?? -1]).toBe(100.5);
    const shifted = applyHandleDrag(long, "entry", drag(101, { time: 99.5 }), 2) as PositionDrawing;
    expect(shifted.time).toBe(100);
    expect(newPosition("p", "long", 101.5, 100, 2).time).toBe(102);
    expect(storedTime(1_700_000_000)).toBe(1_700_000_000);
  });

  it("caps the width at the server's MAX_WIDTH_BARS", () => {
    const wide = applyHandleDrag(long, "right", drag(100, { barsSince: () => 14_900 }), 2) as PositionDrawing;
    expect(wide.width_bars).toBe(MAX_WIDTH_BARS);
  });

  it("without a precision, refuses a target or stop dragged onto the entry (the server would 422)", () => {
    expect(applyHandleDrag(long, "target", drag(95), null)).toBe(long);
    expect(applyHandleDrag(long, "stop", drag(105), null)).toBe(long);
    expect((applyHandleDrag(long, "target", drag(104.5), null) as PositionDrawing).target).toBe(104.5);
  });

  it("refuses a horizontal line dragged to or below zero", () => {
    const line = { kind: "hline" as const, id: "hline-1", price: 0.05 };
    expect(applyHandleDrag(line, "price", drag(-1), 2)).toBe(line);
    expect(applyHandleDrag(line, "price", drag(0.004), 2)).toBe(line);
  });

  it("moves a horizontal line by price alone and ignores an unknown handle", () => {
    const line = { kind: "hline" as const, id: "hline-1", price: 100 };
    expect(applyHandleDrag(line, "price", drag(101.005), 2)).toMatchObject({ price: 101.01 });
    expect(applyHandleDrag(line, "nope", drag(5), 2)).toBe(line);
  });
});

describe("bars and ids (Story 32.5)", () => {
  it("snaps a time between two bars to the earlier one, and finds none before the first", () => {
    const hours = [0, 3600, 7200];
    expect(snapIndex(hours, 3600 + 37 * 60)).toBe(1); // a 12:37 anchor on a 1H chart draws at 12:00
    expect(snapIndex(hours, 7200)).toBe(2);
    expect(snapIndex(hours, 99_999)).toBe(2);
    expect(snapIndex(hours, -1)).toBeNull();
    expect(snapIndex([], 5)).toBeNull();
  });

  it("numbers a new drawing one past the largest of its kind", () => {
    const hline = { kind: "hline" as const, price: 1 };
    const drawings = [
      { ...hline, id: "hline-3" },
      { ...hline, id: "hline-7" },
      fib(1, 2),
    ];
    expect(nextDrawingId(drawings, "hline")).toBe("hline-8");
    expect(nextDrawingId(drawings, "fib")).toBe("fib-2");
    expect(nextDrawingId(drawings, "position")).toBe("position-1");
  });

  it("refuses to narrow an item of an unknown kind", () => {
    expect(() => parseDrawings([{ kind: "circle", id: "x" }])).toThrow(/unknown drawing kind/);
    expect(parseDrawings([{ kind: "hline", id: "hline-1", price: 1 }])).toHaveLength(1);
  });

  it("imports the old browser horizontal lines under fresh ids and skips what is not a line", () => {
    const existing = [{ kind: "hline" as const, id: "hline-1", price: 5 }];
    const raw = [{ id: "hline-1", price: 100, color: "#112233" }, { price: "x" }, null, { id: "hline-2", price: 101 }];
    expect(importLegacyHlines(raw, existing)).toEqual([
      { kind: "hline", id: "hline-2", price: 100, color: "#112233" },
      { kind: "hline", id: "hline-3", price: 101 },
    ]);
    expect(importLegacyHlines("nope", [])).toEqual([]);
  });
});

describe("position settings form (Story 32.5)", () => {
  const long = newPosition("p", "long", 100, 0.0000005, 8);

  it("writes the prices at the instrument precision, never in exponent form", () => {
    const form = positionToForm(long, 8);
    expect(form.entry).toBe("0.00000050");
    expect(form.entry).not.toContain("e");
  });

  it("refuses a price that rounds to zero on the grid and a width past MAX_WIDTH_BARS", () => {
    const base = newPosition("p", "long", 100, 0.01, 2);
    const form = { entry: "0.01", stop: "0.004", target: "0.02", widthBars: "40", account: "", riskPct: "" };
    expect(parsePositionForm(base, form, 2)).toBe("Stop must be at least one tick");
    const wide = { ...form, stop: "0.005", entry: "0.02", target: "0.03", widthBars: String(MAX_WIDTH_BARS + 1) };
    expect(parsePositionForm(base, wide, 2)).toMatch(/^Width must be/);
  });

  it("refuses a price too large to round exactly instead of throwing", () => {
    const base = newPosition("p", "long", 100, 100, 2);
    const form = { entry: "1e20", stop: "99", target: "1e21", widthBars: "40", account: "", riskPct: "" };
    expect(parsePositionForm(base, form, 2)).toBe("Entry is too large");
  });
});

describe("legacy import (Story 32.5)", () => {
  it("skips a line the resource can't store (price at or below zero)", () => {
    const raw = [{ price: 0 }, { price: -3 }, { price: 5 }];
    expect(importLegacyHlines(raw, []).map((l) => l.price)).toEqual([5]);
  });
});

describe("anchored drawings (Story 32.7)", () => {
  const avp = newAnchoredVp("anchored_vp-1", 600, "#25a399", "#ef5350");
  const avwap = newAnchoredVwap("anchored_vwap-1", 600, "#2962ff", "#b26a00");

  it("a new Anchored VP stores only the anchor and the look; a new Anchored VWAP is hlc3 with bands off", () => {
    expect(avp).toEqual({
      kind: "anchored_vp",
      id: "anchored_vp-1",
      time: 600,
      rows: 24,
      value_area_pct: 70,
      up_color: "#25a399",
      down_color: "#ef5350",
    });
    expect(avwap).toEqual({
      kind: "anchored_vwap",
      id: "anchored_vwap-1",
      time: 600,
      source: "hlc3",
      bands: false,
      color: "#2962ff",
      band_color: "#b26a00",
    });
  });

  it("stores a mid-second pointer time as whole seconds", () => {
    expect(newAnchoredVp("a", 600.4, "#000000", "#000000").time).toBe(601);
  });

  it("the anchor handle moves the anchor to the pointer's bar and nothing else", () => {
    const moved = applyHandleDrag(avp, "anchor", drag(1, { time: 900 }), 2);
    expect(moved).toEqual({ ...avp, time: 900 });
    expect(applyHandleDrag(avwap, "anchor", drag(1, { time: 1200 }), 2)).toEqual({ ...avwap, time: 1200 });
  });

  it("ignores an unknown handle and a drag with no bar under the pointer", () => {
    expect(applyHandleDrag(avp, "price", drag(5), 2)).toBe(avp);
    expect(applyHandleDrag(avwap, "anchor", drag(5, { time: null }), 2)).toBe(avwap);
  });

  it("numbers each kind apart and narrows both kinds from the wire", () => {
    expect(nextDrawingId([avp, avwap], "anchored_vp")).toBe("anchored_vp-2");
    expect(nextDrawingId([avp], "anchored_vwap")).toBe("anchored_vwap-1");
    expect(parseDrawings([avp as never, avwap as never])).toHaveLength(2);
  });

  it("the Anchored VP form refuses rows outside 2..500 and a value area outside (0, 100]", () => {
    const form = anchoredVpToForm(avp);
    expect(parseAnchoredVpForm(avp, { ...form, rows: "48", valueAreaPct: "60" })).toEqual({ ...avp, rows: 48, value_area_pct: 60 });
    for (const rows of ["1", "501", "2.5", "", "abc"]) {
      expect(parseAnchoredVpForm(avp, { ...form, rows })).toMatch(/Rows must be/);
    }
    for (const valueAreaPct of ["0", "100.5", "", "x"]) {
      expect(parseAnchoredVpForm(avp, { ...form, valueAreaPct })).toMatch(/Value area/);
    }
    expect(parseAnchoredVpForm(avp, { ...form, upColor: "#111111", downColor: "#222222" })).toMatchObject({
      up_color: "#111111",
      down_color: "#222222",
    });
  });
});

interface TrendlineCase {
  name: string;
  anchors: [{ time: number; price: number }, { time: number; price: number }];
  t: number;
  expected: number | null;
}

const TRENDLINE_FIXTURE = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "alerting", "tests", "fixtures", "trendline_cases.json",
);

describe("trendlinePriceAt (Story 33.8, the fixture shared with alerting/domain/geometry.py)", () => {
  const { cases } = JSON.parse(readFileSync(TRENDLINE_FIXTURE, "utf8")) as { cases: TrendlineCase[] };

  it("reads a non-empty fixture", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases)("$name", ({ anchors, t, expected }) => {
    expect(trendlinePriceAt(anchors, t)).toBe(expected);
  });
});

// -- Story 33.10 ---------------------------------------------------------------------------------

const A: Anchor = { time: 100, price: 100 };
const B: Anchor = { time: 200, price: 110 };
const CTX: NewDrawingContext = {
  precision: { price: 2, size: 3 },
  color: "#101010",
  upColor: "#202020",
  downColor: "#303030",
  bandColor: "#404040",
  fibColor: () => "#505050",
};

/** One stored item of every kind, each as the server holds it (the optional keys on some). */
const EVERY_KIND: Record<string, unknown>[] = [
  { kind: "hline", id: "hline-1", price: 100.5, color: "#aabbcc", line_width: 2, line_style: "dashed", locked: true },
  { kind: "trendline", id: "trendline-1", anchors: [A, B], color: "#aabbcc", hidden: true },
  {
    kind: "fib",
    id: "fib-1",
    anchors: [A, B],
    levels: [{ ratio: 0.5, enabled: true, color: "#111111" }],
    extend_right: true,
    label_side: "left",
    line_width: 1,
  },
  { kind: "position", id: "position-1", side: "long", time: 100, entry: 100, stop: 99, target: 102, width_bars: 40 },
  { kind: "anchored_vp", id: "anchored_vp-1", time: 100, rows: 24, value_area_pct: 70, up_color: "#1", down_color: "#2" },
  { kind: "anchored_vwap", id: "anchored_vwap-1", time: 100, source: "hlc3", bands: false, band_color: "#3" },
  { kind: "ray", id: "ray-1", anchors: [A, B], color: "#aabbcc", line_style: "dotted" },
  { kind: "extended", id: "extended-1", anchors: [A, B] },
  { kind: "vline", id: "vline-1", time: 100, line_width: 3 },
  { kind: "rect", id: "rect-1", anchors: [A, B], fill_opacity: 0.2, color: "#aabbcc" },
  { kind: "channel", id: "channel-1", anchors: [A, B], offset: -4.5 },
  { kind: "text", id: "text-1", anchor: A, text: "Breakout\nhere", font_size: 14, color: "#aabbcc" },
  { kind: "arrow", id: "arrow-1", anchors: [A, B], locked: false },
  {
    kind: "fib_extension",
    id: "fib_extension-1",
    anchors: [A, B, { time: 300, price: 105 }],
    levels: [{ ratio: 1.618, enabled: true, color: "#111111" }],
    extend_right: false,
    label_side: "right",
    line_width: 2,
  },
  { kind: "price_range", id: "price_range-1", anchors: [A, B] },
  { kind: "date_range", id: "date_range-1", anchors: [A, B], hidden: false },
];

describe("the sixteen kinds on the wire (Story 33.10)", () => {
  it("covers every kind the server stores", () => {
    expect(EVERY_KIND.map((d) => d.kind)).toEqual(DRAWING_KIND_NAMES);
  });

  it.each(EVERY_KIND)("$kind round-trips byte for byte", (item) => {
    const text = JSON.stringify(item);
    expect(JSON.stringify(parseDrawings([JSON.parse(text)])[0])).toBe(text);
  });

  it("refuses an unknown kind with UnknownDrawingKindError, never dropping it", () => {
    let error: unknown = null;
    try {
      parseDrawings([EVERY_KIND[0], { kind: "zigzag", id: "zigzag-1" }]);
    } catch (err) {
      error = err;
    }
    expect(error).toBeInstanceOf(UnknownDrawingKindError);
    expect((error as UnknownDrawingKindError).kind).toBe("zigzag");
    expect((error as Error).message).toBe('unknown drawing kind "zigzag"');
  });
});

describe("line geometry (Story 33.10)", () => {
  it("takes the extension from the kind alone", () => {
    expect([extendOf("ray"), extendOf("extended"), extendOf("trendline"), extendOf("arrow")]).toEqual(["right", "both", "none", "none"]);
  });

  it("carries a ray past B to the pane's border, and an extended line past both anchors", () => {
    const a = { x: 100, y: 100 };
    const b = { x: 200, y: 150 };
    expect(extendedSegment(a, b, "none", 800, 500)).toEqual([a, b]);
    // Slope 1/2: the right border (x 800) is reached at y 450, inside the 500 px pane.
    expect(extendedSegment(a, b, "right", 800, 500)).toEqual([a, { x: 800, y: 450 }]);
    // Backwards the left border (x 0) is reached at y 50, before the top one.
    expect(extendedSegment(a, b, "both", 800, 500)).toEqual([
      { x: 0, y: 50 },
      { x: 800, y: 450 },
    ]);
  });

  it("clips at the bottom when the line leaves the pane there first", () => {
    const [, end] = extendedSegment({ x: 0, y: 0 }, { x: 100, y: 100 }, "right", 800, 500);
    expect(end).toEqual({ x: 500, y: 500 });
  });

  it("extends a vertical or horizontal line, and leaves a point (A = B) or an off-pane line as it is", () => {
    expect(extendedSegment({ x: 50, y: 100 }, { x: 50, y: 200 }, "both", 800, 500)).toEqual([
      { x: 50, y: 0 },
      { x: 50, y: 500 },
    ]);
    expect(extendedSegment({ x: 50, y: 100 }, { x: 60, y: 100 }, "right", 800, 500)[1]).toEqual({ x: 800, y: 100 });
    const p = { x: 10, y: 10 };
    expect(extendedSegment(p, p, "both", 800, 500)).toEqual([p, p]);
    const off = [{ x: -50, y: -10 }, { x: -40, y: -20 }] as const;
    expect(extendedSegment(off[0], off[1], "both", 800, 500)).toEqual([off[0], off[1]]);
  });

  it("never cuts the anchors off: a ray whose B lies past the pane keeps B", () => {
    const [, end] = extendedSegment({ x: 100, y: 100 }, { x: 900, y: 100 }, "right", 800, 500);
    expect(end).toEqual({ x: 900, y: 100 });
  });
});

describe("channel and Fibonacci extension maths (Story 33.10)", () => {
  it("offsets a channel by C's distance from the A-B line at C's time", () => {
    // The A-B line is at 105 at t 150: C at 99 sits 6 below it.
    expect(channelOffsetFor(A, B, { time: 150, price: 99 })).toBe(-6);
    expect(channelOffsetFor(A, B, { time: 300, price: 125 })).toBe(5); // the line is at 120 at t 300
  });

  it("measures a vertical A-B (equal times) from A's price", () => {
    expect(channelOffsetFor(A, { time: 100, price: 120 }, { time: 100, price: 97 })).toBe(-3);
  });

  it("puts each extension level at C + (B - A) x ratio, on the price grid", () => {
    const d: FibExtensionDrawing = {
      kind: "fib_extension",
      id: "fib_extension-1",
      anchors: [A, B, { time: 300, price: 105 }],
      levels: defaultFibExtensionLevels(() => "#000000"),
      extend_right: true,
      label_side: "left",
      line_width: 1,
    };
    const prices = fibExtensionLevelPrices(d, 2);
    expect(prices.map((l) => [l.ratio, l.price])).toEqual([
      [0, 105],
      [0.382, 108.82],
      [0.5, 110],
      [0.618, 111.18],
      [1, 115],
      [1.272, 117.72],
      [1.618, 121.18],
      [2.618, 131.18],
    ]);
  });
});

describe("labels of the rectangle and the ranges (Story 33.10)", () => {
  it("labels a rectangle with its height and that height as a percent of its lower edge", () => {
    const rect: RectDrawing = { kind: "rect", id: "rect-1", anchors: [A, B], fill_opacity: 0.2 };
    expect(rectLabel(rect, 2)).toBe("10.00 (+10.00 %)");
    expect(rectLabel({ ...rect, anchors: [{ time: 1, price: 0 }, B] }, 2)).toBe("110.00 (n/a)");
  });

  it("labels a price range with the signed change and percent, a date range with bars and volume", () => {
    const m = { priceDelta: -1.5, priceDeltaPct: -1.5, bars: 12, volume: 1234.5678 };
    const precision = { price: 2, size: 3 };
    expect(rangeLabels(m, "price_range", precision)).toEqual(["−1.50 (−1.50 %)"]);
    expect(rangeLabels({ ...m, priceDelta: 2, priceDeltaPct: null }, "price_range", precision)).toEqual(["+2.00 (n/a)"]);
    expect(rangeLabels(m, "date_range", precision)).toEqual(["12 bars", "Vol 1234.568"]);
  });

  it("prints n/a, never 0, for the bars and volume of a range no candle bars back", () => {
    const m = { priceDelta: 2, priceDeltaPct: 2, bars: null, volume: null };
    expect(rangeLabels(m, "date_range", { price: 2, size: 3 })).toEqual(["n/a bars", "Vol n/a"]);
    expect(rangeLabels(m, "price_range", { price: 2, size: 3 })).toEqual(["+2.00 (+2.00 %)"]);
  });

  it("never throws on a value it cannot print", () => {
    const m = { priceDelta: Number.NaN, priceDeltaPct: Number.POSITIVE_INFINITY, bars: 0, volume: Number.NaN };
    expect(rangeLabels(m, "price_range", { price: 2, size: 3 })).toEqual(["+n/a (n/a)"]);
    expect(rangeLabels(m, "date_range", { price: 2, size: 3 })).toEqual(["0 bars", "Vol n/a"]);
  });
});

describe("placement (Story 33.10)", () => {
  it("counts the clicks of every click-placed tool, and none for the others", () => {
    expect(["ray", "extended", "arrow", "rect", "price_range", "date_range", "trendline"].map((t) => placementOf(t as never))).toEqual([2, 2, 2, 2, 2, 2, 2]);
    expect([placementOf("channel"), placementOf("fib_extension")]).toEqual([3, 3]);
    expect([placementOf("vline"), placementOf("text"), placementOf("long"), placementOf("avwap")]).toEqual([1, 1, 1, 1]);
    expect([placementOf("cursor"), placementOf("hline"), placementOf("fib"), placementOf("measure"), placementOf("frvp")]).toEqual([0, 0, 0, 0, 0]);
    expect([kindOfTool("long"), kindOfTool("avp"), kindOfTool("ray"), kindOfTool("fib")]).toEqual(["position", "anchored_vp", "ray", null]);
  });

  it("builds a ray from two points, stored as whole seconds and grid prices", () => {
    const ray = buildDrawing("ray", "ray-1", [{ time: 100.5, price: 100.004 }, { time: 200, price: 110.006 }], CTX);
    expect(ray).toEqual({
      kind: "ray",
      id: "ray-1",
      anchors: [
        { time: 101, price: 100 },
        { time: 200, price: 110.01 },
      ],
      color: "#101010",
    });
  });

  it("builds a channel whose offset is C's distance from A-B, and a Fibonacci extension from A, B, C", () => {
    const channel = buildDrawing("channel", "channel-1", [A, B, { time: 150, price: 99 }], CTX) as ChannelDrawing;
    expect(channel.offset).toBe(-6);
    expect(channel.anchors).toEqual([A, B]);
    const ext = buildDrawing("fib_extension", "fib_extension-1", [A, B, { time: 300, price: 105 }], CTX) as FibExtensionDrawing;
    expect(ext.anchors[2]).toEqual({ time: 300, price: 105 });
    expect(ext.levels.every((l) => l.color === "#505050")).toBe(true);
  });

  it("builds the one-click kinds: a vertical line, a text note, a rectangle at the default opacity", () => {
    expect(buildDrawing("vline", "vline-1", [A], CTX)).toEqual({ kind: "vline", id: "vline-1", time: 100, color: "#101010" });
    expect(buildDrawing("text", "text-1", [A], CTX)).toMatchObject({ kind: "text", anchor: A, text: "Text", font_size: 14 });
    expect(buildDrawing("rect", "rect-1", [A, B], CTX)).toMatchObject({ fill_opacity: 0.2 });
    expect(buildDrawing("long", "position-1", [A], { ...CTX, precision: null })).toBeNull(); // no grid yet
  });

  it("refuses a zero-size placement (the second point on the first) and a wrong point count", () => {
    expect(buildDrawing("ray", "ray-1", [A, { time: 99.6, price: 100.001 }], CTX)).toBeNull(); // the same once stored
    expect(buildDrawing("ray", "ray-1", [A], CTX)).toBeNull();
    expect(buildDrawing("hline", "hline-1", [A], CTX)).toBeNull();
    // A Fibonacci extension's C on A: the shape no drag could reach (`dragAnchor` refuses it).
    expect(buildDrawing("fib_extension", "fib_extension-1", [A, B, A], CTX)).toBeNull();
  });

  it("refuses a channel with a vertical A-B or no width, and says so as its points come", () => {
    expect(buildDrawing("channel", "channel-1", [A, { time: 100, price: 120 }, B], CTX)).toBeNull();
    // C on the A-B line at its time (105 at t 150), on the price grid: an offset of 0.
    expect(buildDrawing("channel", "channel-1", [A, B, { time: 150, price: 105.004 }], CTX)).toBeNull();
    expect(channelPlaceable([A, { time: 99.5, price: 120 }], 2)).toBe(false); // one stored second
    expect(channelPlaceable([A, B], 2)).toBe(true);
    expect(channelPlaceable([A, B, { time: 150, price: 105.01 }], 2)).toBe(true);
  });

  it("previews the tool's shape to the pointer, filling the missing points with it", () => {
    const cursor = { time: 150, price: 99 };
    expect(previewDrawing("channel", [A], cursor, CTX)).toMatchObject({ kind: "channel", anchors: [A, cursor], offset: 0 });
    expect(previewDrawing("channel", [A, B], cursor, CTX)).toMatchObject({ offset: -6 });
    expect(previewDrawing("fib_extension", [A], cursor, CTX)).toMatchObject({ anchors: [A, cursor, cursor] });
    expect(previewDrawing("ray", [], cursor, CTX)).toBeNull();
    expect(previewDrawing("vline", [A], cursor, CTX)).toBeNull(); // one click: nothing to preview
    expect(previewDrawing("ray", [A, B], cursor, CTX)).toBeNull(); // already complete
  });
});

describe("handle drags of the new kinds (Story 33.10)", () => {
  const ray: Drawing = { kind: "ray", id: "ray-1", anchors: [A, B] };
  const channel: ChannelDrawing = { kind: "channel", id: "channel-1", anchors: [A, B], offset: -6 };

  it("moves the two-point kinds' a / b anchor (a rectangle's corner too)", () => {
    for (const kind of ["ray", "extended", "arrow", "price_range", "date_range"] as const) {
      const moved = applyHandleDrag({ kind, id: "x", anchors: [A, B] }, "a", drag(95.555, { time: 50 }), 2);
      expect(moved).toMatchObject({ anchors: [{ time: 50, price: 95.56 }, B] });
    }
    const rect: RectDrawing = { kind: "rect", id: "rect-1", anchors: [A, B], fill_opacity: 0.2 };
    expect(applyHandleDrag(rect, "b", drag(120, { time: 250 }), 2)).toMatchObject({ anchors: [A, { time: 250, price: 120 }] });
  });

  it("moves a channel's anchor with the offset kept, and its offset handle alone", () => {
    expect(applyHandleDrag(channel, "b", drag(130, { time: 300 }), 2)).toMatchObject({ anchors: [A, { time: 300, price: 130 }], offset: -6 });
    // The pointer at t 150 is 2 above the A-B line (105 there).
    expect(applyHandleDrag(channel, "offset", drag(107.001, { time: 150 }), 2)).toMatchObject({ anchors: [A, B], offset: 2 });
    // No bar under the pointer: measured at the channel's middle (t 150).
    expect(applyHandleDrag(channel, "offset", drag(101, { time: null }), 2)).toMatchObject({ offset: -4 });
  });

  it("moves a text note's anchor, a vertical line's time and a Fibonacci extension's C", () => {
    const text: TextDrawing = { kind: "text", id: "text-1", anchor: A, text: "x", font_size: 14 };
    expect(applyHandleDrag(text, "anchor", drag(120, { time: 300 }), 2)).toMatchObject({ anchor: { time: 300, price: 120 } });
    expect(applyHandleDrag({ kind: "vline", id: "v", time: 100 }, "time", drag(1, { time: 400.5 }), 2)).toMatchObject({ time: 401 });
    const ext = buildDrawing("fib_extension", "e", [A, B, { time: 300, price: 105 }], CTX)!;
    expect(applyHandleDrag(ext, "c", drag(104, { time: 350 }), 2)).toMatchObject({ anchors: [A, B, { time: 350, price: 104 }] });
  });

  it("refuses a drag that puts an anchor on another one of the same drawing", () => {
    for (const kind of ["ray", "trendline", "price_range"] as const) {
      const d: Drawing = { kind, id: "x", anchors: [A, B] };
      expect(applyHandleDrag(d, "a", drag(110, { time: 200 }), 2)).toBe(d);
    }
    const rect: RectDrawing = { kind: "rect", id: "rect-1", anchors: [A, B], fill_opacity: 0.2 };
    expect(applyHandleDrag(rect, "b", drag(100.001, { time: 100 }), 2)).toBe(rect); // on A once rounded
    const ext = buildDrawing("fib_extension", "e", [A, B, { time: 300, price: 105 }], CTX)!;
    expect(applyHandleDrag(ext, "c", drag(110, { time: 200 }), 2)).toBe(ext); // C onto B
    expect(applyHandleDrag(ext, "c", drag(100, { time: 100 }), 2)).toBe(ext); // C onto A
  });

  it("refuses a channel drag that makes A-B vertical or the channel of no width", () => {
    expect(applyHandleDrag(channel, "b", drag(130, { time: 100 }), 2)).toBe(channel);
    expect(applyHandleDrag(channel, "offset", drag(105.001, { time: 150 }), 2)).toBe(channel);
  });

  it("never throws dragging a channel's offset to a price beyond the safe-integer grid", () => {
    expect(() => applyHandleDrag(channel, "offset", drag(1e300, { time: 150 }), 8)).not.toThrow();
  });

  it("returns a locked drawing, and an unknown handle, unchanged", () => {
    const locked = { ...ray, locked: true };
    expect(applyHandleDrag(locked, "a", drag(50), 2)).toBe(locked);
    expect(applyHandleDrag({ kind: "hline", id: "h", price: 100, locked: true }, "price", drag(50), 2)).toMatchObject({ price: 100 });
    expect(applyHandleDrag(ray, "c", drag(50), 2)).toBe(ray); // a ray has no third anchor
    expect(applyHandleDrag(channel, "zz", drag(50), 2)).toBe(channel);
  });
});

describe("text settings form (Story 33.10)", () => {
  const text: TextDrawing = { kind: "text", id: "text-1", anchor: A, text: "x", font_size: 14 };

  it("refuses blank or too long text and a font size out of 8..72", () => {
    expect(parseTextForm(text, { text: "   ", fontSize: "14" }, "#1")).toBe("The text must not be empty");
    expect(parseTextForm(text, { text: "a".repeat(501), fontSize: "14" }, "#1")).toBe("The text must be at most 500 characters");
    expect(parseTextForm(text, { text: "half \uD83D emoji", fontSize: "14" }, "#1")).toBe("The text holds a broken character (half an emoji)");
    expect(parseTextForm(text, { text: "whole \uD83D\uDE00", fontSize: "14" }, "#1")).not.toBeTypeOf("string");
    expect(parseTextForm(text, { text: "ok", fontSize: "7" }, "#1")).toMatch(/Font size/);
    expect(parseTextForm(text, { text: "ok", fontSize: "12.5" }, "#1")).toMatch(/Font size/);
  });

  it("counts code points like the server, and treats Python's extra separators as blank", () => {
    expect(textLength("😀".repeat(3))).toBe(3);
    // 500 emoji are 1000 UTF-16 units but 500 characters: accepted, as the server does.
    expect(parseTextForm(text, { text: "😀".repeat(500), fontSize: "14" }, "#1")).toMatchObject({ font_size: 14 });
    expect(parseTextForm(text, { text: "😀".repeat(501), fontSize: "14" }, "#1")).toBe("The text must be at most 500 characters");
    for (const blank of ["\u001c\u001d", " \u001e\u001f ", "\u0085", "\t\u00a0"]) {
      expect(isBlankText(blank)).toBe(true);
      expect(parseTextForm(text, { text: blank, fontSize: "14" }, "#1")).toBe("The text must not be empty");
    }
    expect(isBlankText(" a ")).toBe(false);
  });

  it("applies the text, size and colour", () => {
    expect(parseTextForm(text, { text: "a".repeat(500), fontSize: "72" }, "#123456")).toMatchObject({
      text: "a".repeat(500),
      font_size: 72,
      color: "#123456",
    });
  });
});
