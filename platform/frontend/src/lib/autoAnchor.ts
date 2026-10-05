import type { ChartDatum } from "../hooks/useCandles";
import { type SessionPeriod, periodStart } from "./sessionProfile";

/**
 * Where an Auto Anchored Volume Profile starts (Story 32.7). One pure module: the preset table, the
 * `auto` rule by bar size and the anchor resolution, so the page, the marker and the tests read one
 * definition. `views/preferences.py`'s `PROFILE_ANCHORS` mirrors `AUTO_ANCHOR_PRESETS`
 * (`test_profile_anchors_mirror_the_frontend` pins the pair).
 */
export const AUTO_ANCHOR_PRESETS = ["session", "week", "month", "highest_high", "lowest_low", "auto"] as const;
export type AutoAnchorPreset = (typeof AUTO_ANCHOR_PRESETS)[number];
/** A preset that names an anchor itself: everything but `auto`, which picks one by bar size. */
export type ResolvedAnchorPreset = Exclude<AutoAnchorPreset, "auto">;

export const DEFAULT_AUTO_ANCHOR: AutoAnchorPreset = "auto";

/**
 * The `auto` preset's rule, by the chart's bar size (named table, first row whose bound holds):
 * a session up to 15 minute bars, a week up to 4 hour bars, a month above. A "session" is the UTC
 * day, the one calendar-day convention of this codebase (`lib/sessionProfile.ts`).
 */
export const AUTO_PRESET_BY_BAR_SIZE: readonly { maxBarSeconds: number; preset: ResolvedAnchorPreset }[] = [
  { maxBarSeconds: 900, preset: "session" },
  { maxBarSeconds: 14_400, preset: "week" },
  { maxBarSeconds: Number.POSITIVE_INFINITY, preset: "month" },
];

export function autoPresetFor(barSeconds: number): ResolvedAnchorPreset {
  const row = AUTO_PRESET_BY_BAR_SIZE.find((r) => barSeconds <= r.maxBarSeconds);
  return (row ?? AUTO_PRESET_BY_BAR_SIZE[AUTO_PRESET_BY_BAR_SIZE.length - 1]).preset;
}

/** The recurring period each calendar preset starts at; the extreme presets have none. */
const PRESET_PERIOD: Partial<Record<ResolvedAnchorPreset, SessionPeriod>> = {
  session: "daily",
  week: "weekly",
  month: "monthly",
};

export interface AnchorBar {
  time: number;
  high: number;
  low: number;
}

/** The real bars of a chart's candle array (whitespace gap slots dropped), as anchor inputs. */
export function anchorBars(candles: readonly ChartDatum[]): AnchorBar[] {
  const bars: AnchorBar[] = [];
  for (const c of candles) {
    if ("open" in c && Number.isFinite(c.high) && Number.isFinite(c.low)) bars.push({ time: c.time as number, high: c.high, low: c.low });
  }
  return bars;
}

export interface ResolvedAnchor {
  /** The preset that was resolved (`auto` replaced by the one the bar size picks). */
  preset: ResolvedAnchorPreset;
  /** UTC seconds: the start of the period, or the time of the extreme bar. */
  time: number;
  /** The period a calendar preset starts, null for the extreme presets. */
  period: SessionPeriod | null;
}

/**
 * The anchor of `preset` over `bars` (ascending), or null with no bars. A calendar preset anchors
 * at the start of the period holding the LATEST bar, so a bar that crosses a session boundary
 * re-anchors it; `highest_high` / `lowest_low` anchor at the first bar holding the extreme of the
 * loaded set. Pure: the same bars and bar size always give the same anchor, which is what makes
 * a timeframe change and a live rollover re-anchor.
 */
export function anchorTime(preset: AutoAnchorPreset, bars: readonly AnchorBar[], barSeconds: number): ResolvedAnchor | null {
  if (bars.length === 0) return null;
  const resolved = preset === "auto" ? autoPresetFor(barSeconds) : preset;
  const period = PRESET_PERIOD[resolved] ?? null;
  if (period !== null) return { preset: resolved, time: periodStart(bars[bars.length - 1].time, period), period };
  let best = bars[0];
  for (const bar of bars) {
    if (resolved === "highest_high" ? bar.high > best.high : bar.low < best.low) best = bar;
  }
  return { preset: resolved, time: best.time, period: null };
}
