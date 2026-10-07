import type { Time } from "lightweight-charts";
import { describe, expect, it } from "vitest";

import { MARKER_MIN_BAR_SPACING_PX } from "../components/chart/LiquidationMarkers";
import {
  NON_DIRECTIONAL_PATTERNS,
  buildPatternMarkers,
  drawsPatternMarkers,
  patternDisplay,
  patternLabel,
  patternReadout,
} from "./patternMarkers";

const COLORS = { up: "#0a0", down: "#a00", neutral: "#888" };
const SPACING = 10;
const at = (time: number, value?: number) => (value === undefined ? { time: time as Time } : { time: time as Time, value });

describe("buildPatternMarkers", () => {
  it("draws a bearish hit as an arrow down above the bar, named in its tooltip", () => {
    expect(buildPatternMarkers("E", "EVENING_STAR", [at(60, -100)], COLORS, SPACING)).toEqual([
      { id: "pat:E:60", time: 60, shape: "arrowDown", position: "aboveBar", color: "#a00", tooltip: ["Evening star", "bearish"] },
    ]);
  });

  it("draws a bullish hit as an arrow up below the bar", () => {
    expect(buildPatternMarkers("E", "ENGULFING", [at(120, 100)], COLORS, SPACING)).toEqual([
      { id: "pat:E:120", time: 120, shape: "arrowUp", position: "belowBar", color: "#0a0", tooltip: ["Engulfing", "bullish"] },
    ]);
  });

  it("draws a non-directional pattern's hit as a neutral circle above the bar", () => {
    expect(buildPatternMarkers("D", "DOJI", [at(60, 100)], COLORS, SPACING)).toEqual([
      { id: "pat:D:60", time: 60, shape: "circle", position: "aboveBar", color: "#888", tooltip: ["Doji", "neutral"] },
    ]);
  });

  it("skips no-pattern, gap and non-finite slots", () => {
    const data = [at(60, 0), at(120), at(180, Number.NaN), at(240, -100)];
    expect(buildPatternMarkers("E", "HAMMER", data, COLORS, SPACING).map((m) => m.id)).toEqual(["pat:E:240"]);
  });

  it("draws nothing at or below the liquidation markers' bar spacing (one zoom rule)", () => {
    const data = [at(60, 100), at(120, -100)];
    expect(buildPatternMarkers("E", "ENGULFING", data, COLORS, MARKER_MIN_BAR_SPACING_PX)).toEqual([]);
    expect(buildPatternMarkers("E", "ENGULFING", data, COLORS, MARKER_MIN_BAR_SPACING_PX + 0.5)).toHaveLength(2);
  });

  it("mirrors kernel.candle_patterns.NON_DIRECTIONAL by member name", () => {
    expect(NON_DIRECTIONAL_PATTERNS).toEqual(["DOJI"]);
  });
});

describe("pattern display and readout", () => {
  it("reads style.value.display, absent or unknown meaning markers", () => {
    const entry = (display?: unknown) => ({
      name: "CandlePattern",
      category: "native",
      ...(display === undefined ? {} : { style: { value: { display } } }),
    });
    expect(patternDisplay(entry())).toBe("markers");
    expect(patternDisplay(entry("x"))).toBe("markers");
    expect(patternDisplay(entry("pane"))).toBe("pane");
    expect(drawsPatternMarkers(entry())).toBe(true);
    expect(drawsPatternMarkers(entry("pane"))).toBe(false);
    expect(drawsPatternMarkers({ name: "RelativeStrengthIndex", category: "native" })).toBe(false);
  });

  it("names the pattern at a hit and prints a dash elsewhere", () => {
    expect(patternLabel("THREE_WHITE_SOLDIERS")).toBe("Three white soldiers");
    const readout = patternReadout("MORNING_STAR");
    expect(readout(100)).toBe("Morning star");
    expect(readout(-100)).toBe("Morning star");
    expect(readout(0)).toBe("—");
  });
});
