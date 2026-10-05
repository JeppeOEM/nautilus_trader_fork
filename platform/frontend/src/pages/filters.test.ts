import { describe, expect, it } from "vitest";

import { applyFilters, compare, type DisplayPrecision, type FilterOperator } from "./filters";

const num = (op: FilterOperator, value: number, precision: DisplayPrecision = { decimals: 0 }) => ({
  field: "x",
  op,
  value,
  precision,
});
const text = (value: string) => ({ field: "venue", op: "=" as const, value });

describe("compare", () => {
  it("applies every operator", () => {
    expect(compare(5, num(">", 4))).toBe(true);
    expect(compare(5, num(">", 5))).toBe(false);
    expect(compare(5, num(">=", 5))).toBe(true);
    expect(compare(5, num("<", 6))).toBe(true);
    expect(compare(5, num("<=", 4))).toBe(false);
    expect(compare(5, num("=", 5))).toBe(true);
  });

  it("matches = at the displayed precision", () => {
    // OBI5 0.49996 shows 0.500.
    expect(compare(0.49996, num("=", 0.5, { decimals: 3 }))).toBe(true);
  });

  it("never matches = with more digits than the cell shows", () => {
    expect(compare(0.49996, num("=", 0.49996, { decimals: 3 }))).toBe(false);
  });

  it("treats a shown match as = for every operator", () => {
    const p = { decimals: 3 };
    expect(compare(0.49996, num(">=", 0.5, p))).toBe(true);
    expect(compare(0.49996, num("<=", 0.5, p))).toBe(true);
    expect(compare(0.49996, num(">", 0.5, p))).toBe(false);
    expect(compare(0.49996, num("<", 0.5, p))).toBe(false);
  });

  it("does not round the typed value", () => {
    // 0.1234 shows 0.123, which is above 0.1226 as typed.
    expect(compare(0.1234, num(">", 0.1226, { decimals: 3 }))).toBe(true);
  });

  it("reads a shown negative zero as 0", () => {
    expect(compare(-0.001, num("=", 0, { decimals: 2 }))).toBe(true);
  });

  it("compares a scaled field in display units", () => {
    // volume24h 1234499.9 shows 1.234M; the typed value stays raw USD.
    const p = { scale: 1e6, decimals: 3 };
    expect(compare(1234499.9, num("=", 1234000, p))).toBe(true);
    expect(compare(1234500.1, num("=", 1234000, p))).toBe(false);
  });

  it("matches a Technicals output at 4 decimals", () => {
    expect(compare(29.99996, num("=", 30, { decimals: 4 }))).toBe(true);
  });

  it("orders by the raw value when the threshold is finer than shown", () => {
    // A 0.00001234 price shows 0.0000: it is above 0.00001. The `= 0` match pins today's
    // Known limit (fixed column precision) and flips when per-instrument precision lands.
    const price = { decimals: 4 };
    expect(compare(0.00001234, num(">", 0.00001, price))).toBe(true);
    expect(compare(0.00001234, num("=", 0, price))).toBe(true);
    // 0.123449 shows 0.1234, but its raw value is above 0.12344.
    expect(compare(0.123449, num(">", 0.12344, price))).toBe(true);
    // volume24h 1234450 shows 1.234M; raw USD is above 1234400.
    expect(compare(1234450, num(">", 1234400, { scale: 1e6, decimals: 3 }))).toBe(true);
  });

  it("holds exactly one of <, =, > for a threshold on the display grid, composing >= and <=", () => {
    const cases: [number, number, DisplayPrecision][] = [
      [0.49996, 0.5, { decimals: 3 }],
      [0.4994, 0.5, { decimals: 3 }],
      [0.5006, 0.5, { decimals: 3 }],
      [0.00001234, 0.00001, { decimals: 4 }],
      [0.00001234, 0, { decimals: 4 }],
      [-0.001, 0, { decimals: 2 }],
      [1234450, 1234400, { scale: 1e6, decimals: 3 }],
      [1234499.9, 1234000, { scale: 1e6, decimals: 3 }],
      [5, 5, { decimals: 0 }],
    ];
    for (const [actual, threshold, p] of cases) {
      const lt = compare(actual, num("<", threshold, p));
      const eq = compare(actual, num("=", threshold, p));
      const gt = compare(actual, num(">", threshold, p));
      expect([lt, eq, gt].filter(Boolean)).toHaveLength(1);
      expect(compare(actual, num(">=", threshold, p))).toBe(gt || eq);
      expect(compare(actual, num("<=", threshold, p))).toBe(lt || eq);
    }
  });

  it("keeps >= and <= on an exact raw match finer than the display, which = never matches", () => {
    const p: DisplayPrecision = { decimals: 3 };
    expect(compare(0.49996, num("=", 0.49996, p))).toBe(false);
    expect(compare(0.49996, num(">=", 0.49996, p))).toBe(true);
    expect(compare(0.49996, num("<=", 0.49996, p))).toBe(true);
    expect(compare(0.49996, num(">", 0.49996, p))).toBe(false);
    expect(compare(0.49996, num("<", 0.49996, p))).toBe(false);
  });

  it("matches text values case-insensitively", () => {
    expect(compare("BYBIT", text("bybit"))).toBe(true);
    expect(compare("BYBIT", text("dydx"))).toBe(false);
    expect(compare(5, text("5"))).toBe(false);
    expect(compare(undefined, text("BYBIT"))).toBe(false);
  });

  it("never matches a missing or non-numeric value", () => {
    expect(compare(null, num("<", 100))).toBe(false);
    expect(compare(undefined, num(">", -100))).toBe(false);
    expect(compare("5", num("=", 5))).toBe(false);
    expect(compare(Number.NaN, num("<=", 1))).toBe(false);
  });
});

describe("applyFilters", () => {
  const rows = [
    { id: "a", x: 1, y: 10 },
    { id: "b", x: 5, y: 10 },
    { id: "c", x: 5, y: 1 },
  ];
  const read = (row: (typeof rows)[number], field: string) => (row as Record<string, unknown>)[field];

  it("combines conditions with AND, keeping order", () => {
    const got = applyFilters(rows, [num(">", 2), { ...num(">", 5), field: "y" }], read);
    expect(got.map((r) => r.id)).toEqual(["b"]);
  });

  it("returns the input untouched with no conditions", () => {
    expect(applyFilters(rows, [], read)).toBe(rows);
  });
});
