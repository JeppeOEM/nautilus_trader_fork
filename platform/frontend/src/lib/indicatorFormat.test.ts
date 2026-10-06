import { describe, expect, it } from "vitest";

import { NO_VALUE } from "./drawings";
import { formatIndicatorValue, isIndicatorUnit } from "./indicatorFormat";

const precision = { price: 2, size: 3 };

describe("formatIndicatorValue (Story 33.6)", () => {
  it("prints a price and a size at the instrument's decimals", () => {
    expect(formatIndicatorValue(1000.05, "price", precision)).toBe("1000.05");
    expect(formatIndicatorValue(4, "size", precision)).toBe("4.000");
    expect(formatIndicatorValue(-4.5, "size", precision)).toBe("-4.500");
  });

  it("prints a mean of sizes finer than the size step, so a small average never reads 0", () => {
    expect(formatIndicatorValue(0.0004, "size_mean", precision)).toBe("0.000400");
    expect(formatIndicatorValue(0.0126, "size_mean", { price: 2, size: 15 })).toBe("0.0126000000000000");
  });

  it("prints a count whole and a ratio as a percent", () => {
    expect(formatIndicatorValue(7, "count", precision)).toBe("7");
    expect(formatIndicatorValue(-3, "count", precision)).toBe("-3");
    expect(formatIndicatorValue(0.15, "ratio", precision)).toBe("15.00%");
    // Never clamped (D-188): a forced share above 1 prints as it is.
    expect(formatIndicatorValue(1.25, "ratio", precision)).toBe("125.00%");
  });

  it("reads n/a for a value the integer path refuses, never throwing", () => {
    expect(formatIndicatorValue(Number.NaN, "price", precision)).toBe(NO_VALUE);
    expect(formatIndicatorValue(1e300, "size", precision)).toBe(NO_VALUE);
  });

  it("knows the five units the catalog serves, nothing else", () => {
    expect(["price", "size", "size_mean", "count", "ratio"].every(isIndicatorUnit)).toBe(true);
    expect(isIndicatorUnit("bps")).toBe(false);
    expect(isIndicatorUnit(undefined)).toBe(false);
  });
});
