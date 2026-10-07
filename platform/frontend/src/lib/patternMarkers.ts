import type { UTCTimestamp } from "lightweight-charts";

import type { IndicatorConfigEntry } from "../api/schema";
import type { IndicatorDatum } from "../components/chart/legend";
import { type MarkerSpec, markersHiddenAt } from "../components/chart/LiquidationMarkers";

// Story 33.11: `CandlePattern` hits as series markers on the candles (the liquidation markers' one
// plugin), instead of ±100 spikes in a pane. Pure: the caller hands the replay-trimmed values.

/** The catalog name of the candlestick-pattern indicator (`kernel.candle_patterns.CandlePattern`). */
export const PATTERN_INDICATOR = "CandlePattern";

/**
 * The `PatternName` members whose +100 marks that they fired, not a direction: the mirror of
 * `kernel.candle_patterns.NON_DIRECTIONAL` (held equal by a mirror test in `views/tests`, which parses
 * this one literal). Drawn as a neutral circle, never an arrow.
 */
export const NON_DIRECTIONAL_PATTERNS = ["DOJI"] as const;

/** How a `CandlePattern` entry draws: `style.value.display`, absent or unknown meaning markers. */
export type PatternDisplay = "markers" | "pane";

export function patternDisplay(entry: IndicatorConfigEntry | undefined): PatternDisplay {
  return entry?.style?.value?.display === "pane" ? "pane" : "markers";
}

/** Whether `entry` is a `CandlePattern` drawn as markers on the candles. */
export function drawsPatternMarkers(entry: IndicatorConfigEntry | undefined): boolean {
  return entry?.name === PATTERN_INDICATOR && patternDisplay(entry) === "markers";
}

/** "EVENING_STAR" -> "Evening star". */
export function patternLabel(patternName: string): string {
  const words = patternName.toLowerCase().split("_").filter(Boolean).join(" ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export interface PatternMarkerColors {
  /** A bullish hit (+100). */
  up: string;
  /** A bearish hit (-100). */
  down: string;
  /** A non-directional pattern's hit. */
  neutral: string;
}

/** The value of a datum, or null for whitespace (a gap, or a not-yet-initialized slot). */
function valueOf(datum: IndicatorDatum): number | null {
  return "value" in datum && typeof datum.value === "number" && Number.isFinite(datum.value) ? datum.value : null;
}

/**
 * The markers of one `CandlePattern` entry over its served values `data` (chart seconds, already cut
 * at the Bar Replay cursor by the caller). +100 is an `arrowUp` below the bar, -100 an `arrowDown`
 * above it, and any hit of a `NON_DIRECTIONAL_PATTERNS` pattern a circle above it; 0, whitespace and
 * a non-finite value draw nothing. Ids are `pat:<entryId>:<time>`, unique per entry and bar; the
 * tooltip is the pattern's name and its reading. None at a bar spacing the liquidation markers hide
 * at too (`markersHiddenAt`, one zoom rule): zoomed out, the legend readout still names the hit.
 */
export function buildPatternMarkers(
  entryId: string,
  patternName: string,
  data: readonly IndicatorDatum[],
  colors: PatternMarkerColors,
  barSpacing: number,
): MarkerSpec[] {
  if (markersHiddenAt(barSpacing)) return [];
  const neutral = (NON_DIRECTIONAL_PATTERNS as readonly string[]).includes(patternName);
  const label = patternLabel(patternName);
  const markers: MarkerSpec[] = [];
  for (const datum of data) {
    const value = valueOf(datum);
    if (value === null || value === 0) continue;
    const time = datum.time as UTCTimestamp;
    const id = `pat:${entryId}:${datum.time as number}`;
    if (neutral) {
      markers.push({ id, time, shape: "circle", position: "aboveBar", color: colors.neutral, tooltip: [label, "neutral"] });
    } else if (value > 0) {
      markers.push({ id, time, shape: "arrowUp", position: "belowBar", color: colors.up, tooltip: [label, "bullish"] });
    } else {
      markers.push({ id, time, shape: "arrowDown", position: "aboveBar", color: colors.down, tooltip: [label, "bearish"] });
    }
  }
  return markers;
}

/** The legend readout of a markers-mode entry at a slot: the pattern's name on a hit, `—` elsewhere. */
export function patternReadout(patternName: string): (value: number) => string {
  const label = patternLabel(patternName);
  return (value: number): string => (Number.isFinite(value) && value !== 0 ? label : "—");
}
