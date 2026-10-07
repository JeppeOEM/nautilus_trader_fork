import { describe, expect, it } from "vitest";

import { parseToast } from "./useAlertToasts";

describe("parseToast", () => {
  it("reads the pre-33.8 frame unchanged", () => {
    expect(parseToast(JSON.stringify({ channel: "alerts", alert: { id: "a", message: "hit" } }))).toEqual({
      id: "a",
      message: "hit",
    });
  });

  it("carries Story 33.8's optional condition text", () => {
    const frame = { channel: "alerts", alert: { id: "a", message: "hit", condition: "close > 100 on 60s bars" } };
    expect(parseToast(JSON.stringify(frame))).toEqual({ id: "a", message: "hit", condition: "close > 100 on 60s bars" });
  });

  it("ignores a non-string condition, another channel and a malformed frame", () => {
    expect(parseToast(JSON.stringify({ channel: "alerts", alert: { id: "a", message: "m", condition: 5 } }))).toEqual({
      id: "a",
      message: "m",
    });
    expect(parseToast(JSON.stringify({ channel: "bars", alert: { id: "a", message: "m" } }))).toBeNull();
    expect(parseToast("{not json")).toBeNull();
  });
});
