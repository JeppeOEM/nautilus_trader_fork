import { describe, expect, it } from "vitest";

import type { FootprintItem } from "../api/schema";
import {
  cellText,
  diagonalImbalances,
  footerText,
  footprintLegendText,
  maxRowTotal,
  mergeFootprintPages,
} from "./footprint";

const bar = (t: number, rows: { p: number; b: number; s: number }[] = []): FootprintItem => {
  const buys = rows.reduce((sum, r) => sum + r.b, 0);
  const sells = rows.reduce((sum, r) => sum + r.s, 0);
  return rows.length === 0
    ? { t, row_ticks: null, rows: [], delta: null, total: null, poc_row: null, no_trades: true }
    : { t, row_ticks: 5, rows, delta: buys - sells, total: buys + sells, poc_row: rows[0].p, no_trades: false };
};

describe("mergeFootprintPages", () => {
  it("upserts by bar time, ascending, the incoming bar winning", () => {
    const older = [bar(60_000, [{ p: 1000, b: 1, s: 0 }]), bar(120_000)];
    const newer = [bar(120_000, [{ p: 1000, b: 2, s: 2 }]), bar(180_000)];

    const merged = mergeFootprintPages(older, newer);

    expect(merged.map((b) => b.t)).toEqual([60_000, 120_000, 180_000]);
    expect(merged[1].no_trades).toBe(false);
  });

  it("prepends an older page in front", () => {
    expect(mergeFootprintPages([bar(180_000)], [bar(60_000)]).map((b) => b.t)).toEqual([60_000, 180_000]);
  });
});

describe("the heat scale", () => {
  it("is the bar's fullest row (buy + sell); 0 for a bar without trades", () => {
    expect(maxRowTotal(bar(0, [{ p: 1000, b: 3, s: 1 }, { p: 1005, b: 2, s: 0 }]))).toBe(4);
    expect(maxRowTotal(bar(0))).toBe(0);
  });
});

describe("diagonalImbalances (ratio 3)", () => {
  it("flags a buy at row n against the sell at row n - 1, and the mirror sell against the buy above", () => {
    const rows = [
      { p: 1000, b: 1, s: 2 }, // n - 1
      { p: 1005, b: 9, s: 1 }, // buy 9 >= 3 x sell 2 below: a buy imbalance
    ];

    expect(diagonalImbalances(rows, 5, 3)).toEqual([
      { buy: true, sell: false }, // buy 1 against nothing below; sell 2 < 3 x buy 9 above
      { buy: true, sell: true }, // sell 1 against nothing above
    ]);
  });

  it("counts a zero opposite side only when the side itself traded", () => {
    const rows = [{ p: 1000, b: 0, s: 0 }, { p: 1005, b: 0, s: 4 }];

    expect(diagonalImbalances(rows, 5, 3)).toEqual([
      { buy: false, sell: false },
      { buy: false, sell: true },
    ]);
  });

  it("compares across a missing row as against nothing, and honours the ratio exactly at its edge", () => {
    expect(diagonalImbalances([{ p: 1000, b: 0, s: 2 }, { p: 1005, b: 6, s: 0 }], 5, 3)[1].buy).toBe(true);
    expect(diagonalImbalances([{ p: 1000, b: 0, s: 2 }, { p: 1005, b: 5, s: 0 }], 5, 3)[1].buy).toBe(false);
    expect(diagonalImbalances([{ p: 1000, b: 0, s: 2 }, { p: 1010, b: 5, s: 0 }], 5, 3)[1].buy).toBe(true);
  });
});

describe("printed text, through lib/units.ts", () => {
  const row = { p: 1000, b: 1250, s: 375 };

  it("prints sell x buy, the delta and the total at a precision-2 size", () => {
    expect(cellText(row, "bid_ask", 2)).toBe("3.75 × 12.50");
    expect(cellText(row, "delta", 2)).toBe("8.75");
    expect(cellText({ ...row, b: 0 }, "delta", 2)).toBe("-3.75");
    expect(cellText(row, "volume", 2)).toBe("16.25");
  });

  it("prints a precision-6 size with exactly six decimals and no float noise", () => {
    const fine = { p: 1, b: 1_000_001, s: 3_000_003 };

    expect(cellText(fine, "bid_ask", 6)).toBe("3.000003 × 1.000001");
    expect(cellText(fine, "delta", 6)).toBe("-2.000002");
    expect(cellText(fine, "volume", 6)).toBe("4.000004");
  });

  it("is exact past what a float sum would hold", () => {
    const big = { p: 1, b: Number.MAX_SAFE_INTEGER, s: Number.MAX_SAFE_INTEGER };

    expect(cellText(big, "volume", 0)).toBe("18014398509481982");
  });

  it("prints the footer's signed delta and total, and none for a bar without trades", () => {
    const item = bar(0, [{ p: 1000, b: 300, s: 100 }, { p: 1005, b: 200, s: 0 }]);

    expect(footerText(item, 2)).toEqual({ delta: "+4.00", total: "6.00" });
    expect(footerText({ ...item, delta: -400 }, 6)).toEqual({ delta: "-0.000400", total: "0.000600" });
    expect(footerText(bar(0), 2)).toBeNull();
  });

  it("names the mode and row size in the legend", () => {
    expect(footprintLegendText("bid_ask", 0)).toBe("bid×ask · auto rows");
    expect(footprintLegendText("delta", 1)).toBe("delta · 1 tick/row");
    expect(footprintLegendText("volume", 5)).toBe("volume · 5 ticks/row");
  });
});
