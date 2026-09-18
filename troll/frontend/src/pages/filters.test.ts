import { describe, expect, it } from "vitest";

import { applyFilters, compare } from "./filters";

describe("compare", () => {
  it("applies every operator", () => {
    expect(compare(5, ">", 4)).toBe(true);
    expect(compare(5, ">", 5)).toBe(false);
    expect(compare(5, ">=", 5)).toBe(true);
    expect(compare(5, "<", 6)).toBe(true);
    expect(compare(5, "<=", 4)).toBe(false);
    expect(compare(5, "=", 5)).toBe(true);
  });

  it("never matches a missing or non-numeric value", () => {
    expect(compare(null, "<", 100)).toBe(false);
    expect(compare(undefined, ">", -100)).toBe(false);
    expect(compare("5", "=", 5)).toBe(false);
    expect(compare(Number.NaN, "<=", 1)).toBe(false);
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
    const got = applyFilters(rows, [{ field: "x", op: ">", value: 2 }, { field: "y", op: ">", value: 5 }], read);
    expect(got.map((r) => r.id)).toEqual(["b"]);
  });

  it("returns the input untouched with no conditions", () => {
    expect(applyFilters(rows, [], read)).toBe(rows);
  });
});
