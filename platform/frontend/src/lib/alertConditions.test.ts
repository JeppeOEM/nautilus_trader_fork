import { describe, expect, it } from "vitest";

import { conditionToForm, editedExpiryNs, expiryNs, formToCondition } from "./alertConditions";

const VWAP = {
  kind: "indicator" as const,
  name: "AnchoredStoredVWAP",
  params: { anchor_t: "0", length: 5, on: true },
  source: "close",
  output: "value",
  op: ">",
  value: 1,
};

describe("formToCondition: indicator params (Story 33.8 review)", () => {
  it("refuses an indicator condition while the catalog has not loaded", () => {
    expect(formToCondition(conditionToForm(VWAP))).toEqual({
      error: "The indicator catalog has not loaded; an indicator condition cannot be saved yet.",
    });
  });

  it("keeps each stored param's JSON type when the catalog does not type it", () => {
    expect(formToCondition(conditionToForm(VWAP), {})).toEqual({ condition: VWAP });
  });

  it("types a param by the catalog default first", () => {
    const form = conditionToForm({ ...VWAP, name: "X", params: { anchor_t: "7" } });
    const result = formToCondition(form, { X: { params: { anchor_t: 0 } } });
    expect(result).toEqual({ condition: { ...VWAP, name: "X", params: { anchor_t: 7 } } });
  });
});

describe("editedExpiryNs", () => {
  const original = new Date(2030, 0, 15, 12, 34, 56).getTime() * 1_000_000;

  it("keeps the exact stored expiry while the date is untouched", () => {
    expect(editedExpiryNs("2030-01-15", original)).toBe(original);
    expect(editedExpiryNs("", null)).toBeNull();
  });

  it("takes the end of a newly chosen day, or never", () => {
    expect(editedExpiryNs("2030-01-16", original)).toBe(expiryNs("2030-01-16"));
    expect(editedExpiryNs("", original)).toBeNull();
    expect(editedExpiryNs("2030-01-16", null)).toBe(expiryNs("2030-01-16"));
  });
});
