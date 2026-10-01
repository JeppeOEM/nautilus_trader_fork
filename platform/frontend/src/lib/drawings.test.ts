import { describe, expect, it } from "vitest";

import {
  type DragPoint,
  type FibDrawing,
  type PositionDrawing,
  applyHandleDrag,
  defaultFibLevels,
  fibLabel,
  fibLevelPrices,
  formatPercent,
  importLegacyHlines,
  MAX_WIDTH_BARS,
  newPosition,
  nextDrawingId,
  parseDrawings,
  parsePositionForm,
  positionLabels,
  positionStats,
  positionToForm,
  snapIndex,
  storedTime,
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
