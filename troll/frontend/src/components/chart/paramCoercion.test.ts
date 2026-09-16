import { describe, expect, it } from "vitest";

import { coerceParamValue } from "./paramCoercion";

describe("coerceParamValue", () => {
  it("keeps a numeric default when the field is cleared, instead of coercing to 0", () => {
    expect(coerceParamValue(14, "")).toBe(14);
    expect(coerceParamValue(14, "  ")).toBe(14);
  });

  it("parses a valid numeric edit", () => {
    expect(coerceParamValue(14, "21")).toBe(21);
  });

  it("keeps the prior number on unparsable input", () => {
    expect(coerceParamValue(14, "abc")).toBe(14);
  });

  it("parses booleans case-insensitively and rejects unrecognized text", () => {
    expect(coerceParamValue(false, "true")).toBe(true);
    expect(coerceParamValue(false, "TRUE")).toBe(true);
    expect(coerceParamValue(true, "false")).toBe(false);
    expect(coerceParamValue(true, "nah")).toBe(true);
  });

  it("passes a string default through as typed", () => {
    expect(coerceParamValue("ema", "sma")).toBe("sma");
  });

  it("rejects an edit to a structured (non-primitive) default", () => {
    const previous = { nested: true };
    expect(coerceParamValue(previous, "not-an-object")).toBe(previous);
    expect(coerceParamValue(null, "x")).toBe(null);
  });
});
