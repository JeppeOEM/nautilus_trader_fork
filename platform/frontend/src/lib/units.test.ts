import { describe, expect, it } from "vitest";

import { decodeBookPrices, formatDecimal, formatUnits, MAX_PRECISION, roundToPrecision, unitsToNumber } from "./units";

describe("formatUnits (Story 30.2)", () => {
  it("moves the decimal point in the integer's own digits", () => {
    expect(formatUnits(858919, 1)).toBe("85891.9");
    expect(formatUnits(1005, 1)).toBe("100.5");
    expect(formatUnits(1000, 3)).toBe("1.000");
  });

  it("formats every precision from 0 to 9 exactly", () => {
    const expected = [
      "123456789",
      "12345678.9",
      "1234567.89",
      "123456.789",
      "12345.6789",
      "1234.56789",
      "123.456789",
      "12.3456789",
      "1.23456789",
      "0.123456789",
    ];
    expect(expected.map((_, p) => formatUnits(123456789, p))).toEqual(expected);
  });

  it("pads small values and keeps the sign", () => {
    expect(formatUnits(5, 3)).toBe("0.005");
    expect(formatUnits(0, 2)).toBe("0.00");
    expect(formatUnits(-5, 2)).toBe("-0.05");
    expect(formatUnits(-12345, 2)).toBe("-123.45");
  });

  it("is exact past the float range, from a BigInt or a digit string", () => {
    expect(formatUnits(9223372036854775807n, 9)).toBe("9223372036.854775807");
    expect(formatUnits("9223372036854775807", 16)).toBe("922.3372036854775807");
    expect(formatUnits(1n, MAX_PRECISION)).toBe("0.0000000000000001");
  });

  it("refuses an integer JavaScript cannot hold exactly", () => {
    expect(() => formatUnits(2 ** 53, 2)).toThrow(RangeError);
    expect(() => formatUnits(1.5, 2)).toThrow(RangeError);
    expect(() => formatUnits(Number.NaN, 2)).toThrow(RangeError);
    expect(() => formatUnits("1.5", 2)).toThrow(RangeError);
  });

  it("refuses a precision outside 0..16", () => {
    expect(() => formatUnits(1, -1)).toThrow(RangeError);
    expect(() => formatUnits(1, MAX_PRECISION + 1)).toThrow(RangeError);
    expect(() => formatUnits(1, 1.5)).toThrow(RangeError);
  });
});

describe("unitsToNumber", () => {
  it("is the nearest double to the exact decimal, with no float noise", () => {
    expect(unitsToNumber(858919, 1)).toBe(85891.9);
    expect(unitsToNumber(3, 1)).toBe(0.3);
    expect(unitsToNumber(-5, 2)).toBe(-0.05);
  });

  it("agrees with the literal for every precision 0..9", () => {
    for (let p = 0; p <= 9; p += 1) {
      expect(unitsToNumber(987654321, p)).toBe(Number(formatUnits(987654321, p)));
      expect(unitsToNumber(987654321, p)).toBe(987654321 / 10 ** p);
    }
  });
});

describe("decodeBookPrices", () => {
  it("decodes the gap layout: bids step down, asks up", () => {
    expect(decodeBookPrices([1005, 2, 4], "bid")).toEqual([1005, 1003, 999]);
    expect(decodeBookPrices([1007, 3], "ask")).toEqual([1007, 1010]);
    expect(decodeBookPrices([1005, 2, 4], "bid").map((u) => formatUnits(u, 1))).toEqual([
      "100.5",
      "100.3",
      "99.9",
    ]);
  });

  it("keeps an empty side empty", () => {
    expect(decodeBookPrices([], "bid")).toEqual([]);
  });

  it("refuses a non-positive or non-integer gap", () => {
    expect(() => decodeBookPrices([1005, 0], "bid")).toThrow(RangeError);
    expect(() => decodeBookPrices([1005, -1], "ask")).toThrow(RangeError);
    expect(() => decodeBookPrices([1005, 0.5], "ask")).toThrow(RangeError);
  });

  it("refuses a level beyond the safe integers", () => {
    expect(() => decodeBookPrices([Number.MAX_SAFE_INTEGER, 1], "ask")).toThrow(RangeError);
  });
});

describe("formatDecimal and roundToPrecision (Story 32.5)", () => {
  it("prints a derived float at exactly the instrument's decimals, with no float noise", () => {
    expect(formatDecimal(90 + 10 * 0.236, 2)).toBe("92.36"); // 92.36000000000001
    expect(formatDecimal(0.1 + 0.2, 6)).toBe("0.300000");
    expect(formatDecimal(100, 0)).toBe("100");
    expect(formatDecimal(-0.4, 0)).toBe("0");
    expect(formatDecimal(-1.5, 2)).toBe("-1.50");
  });

  it("rounds to the grid as a number", () => {
    expect(roundToPrecision(61090.598549999, 5)).toBe(61090.59855);
    expect(roundToPrecision(101.005, 2)).toBe(101.01);
  });

  it("rounds a decimal half-tick up as written, and a negative half away from zero", () => {
    // 1.005 * 100 is 100.49999999999999 in binary: Math.round alone would print "1.00".
    expect(formatDecimal(1.005, 2)).toBe("1.01");
    expect(roundToPrecision(1.005, 2)).toBe(1.01);
    expect(formatDecimal(-2.5, 0)).toBe("-3");
    expect(formatDecimal(-0.004, 2)).toBe("0.00");
  });

  it("refuses a non-finite value and a value whose units are not a safe integer", () => {
    expect(() => formatDecimal(Number.NaN, 2)).toThrow(RangeError);
    expect(() => formatDecimal(1e20, 2)).toThrow(RangeError);
  });
});
