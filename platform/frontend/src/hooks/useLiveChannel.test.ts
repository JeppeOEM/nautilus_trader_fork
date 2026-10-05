import { describe, expect, it } from "vitest";
import { reconnectDelayMs } from "./useLiveChannel";

describe("reconnectDelayMs", () => {
  it("scales linearly per attempt, jittered to 50-100%", () => {
    expect(reconnectDelayMs(2, 0)).toBe(1000);
    expect(reconnectDelayMs(2, 0.999)).toBeGreaterThan(1990);
  });
  it("caps the base at 10 s", () => {
    expect(reconnectDelayMs(50, 0.999)).toBeLessThanOrEqual(10_000);
    expect(reconnectDelayMs(50, 0)).toBe(5000);
  });
});
