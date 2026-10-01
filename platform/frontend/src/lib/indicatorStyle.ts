import type { IndicatorConfigEntry } from "../api/schema";

export type LineStyleName = "solid" | "dashed" | "dotted";

export const LINE_STYLES: readonly LineStyleName[] = ["solid", "dashed", "dotted"];
export const LINE_WIDTHS = [1, 2, 3, 4] as const;

/** What lightweight-charts draws a line series with when no width or style is given
 * (`lineStyleDefaults`: `lineWidth: 3`, solid): the modal's seed, and what clearing a stored
 * width or style goes back to. */
export const DEFAULT_LINE_WIDTH = 3;
export const DEFAULT_LINE_STYLE: LineStyleName = "solid";

/** One output's persisted style (`IndicatorConfigEntry.style[output]`), every field optional:
 * a missing field is the pane palette default. Histogram outputs use `up_color`/`down_color`. */
export interface OutputStyle {
  color?: string;
  line_width?: number;
  line_style?: LineStyleName;
  up_color?: string;
  down_color?: string;
}

/** The style the entry stores for `output`, validated: a stray value is dropped, never drawn; a
 * width outside 1..4 is clamped to the nearest (the one clamp: the chart and the modal both read
 * this). */
export function outputStyle(entry: IndicatorConfigEntry | undefined, output: string): OutputStyle {
  const raw = entry?.style?.[output];
  if (!raw) return {};
  const out: OutputStyle = {};
  if (typeof raw.color === "string") out.color = raw.color;
  if (typeof raw.up_color === "string") out.up_color = raw.up_color;
  if (typeof raw.down_color === "string") out.down_color = raw.down_color;
  if (typeof raw.line_width === "number" && Number.isFinite(raw.line_width)) {
    out.line_width = Math.min(4, Math.max(1, Math.round(raw.line_width)));
  }
  if (raw.line_style === "solid" || raw.line_style === "dashed" || raw.line_style === "dotted") {
    out.line_style = raw.line_style;
  }
  return out;
}
