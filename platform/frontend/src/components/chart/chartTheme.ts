import type { InstrumentPrecision, NewDrawingContext } from "../../lib/drawings";

// Story 32.4 (quick-dev, 2026-10-01): the classic light chart inside the dark terminal app.
//
// Everything the chart draws takes its colour from the `--chart-*` tokens declared on
// `.chart-workspace` in theme.css, and from nothing else -- not the page's `--color-*` semantic
// layer, not a `--vga-*` tone, not a literal in chart code (chartTheme.test.ts greps for all
// three). This table is the one place the token names and their fallbacks live: the fallbacks are
// what the chart draws under jsdom (no stylesheet) and the test asserts they equal theme.css, so
// the two can never drift apart.
export const CHART_TOKENS = {
  "--chart-bg": "#ffffff", // the canvas: TradingView's classic white
  "--chart-grid": "#f0f3fa", // grid lines
  "--chart-text": "#131722", // scale labels, legend titles, gap labels
  "--chart-text-dim": "#787b86", // secondary text, the candle series' vestigial border/wick fallbacks
  "--chart-border": "#e0e3eb", // price- and time-scale borders
  "--chart-crosshair": "#9598a1", // crosshair lines
  "--chart-crosshair-label-bg": "#131722", // crosshair axis labels (lightweight-charts picks the label text colour for contrast)
  "--chart-up": "#25a399", // up candles, bars and profile fills (TradingView's #26a69a reads 2.998:1 on white, one step darker clears the 3:1 floor)
  "--chart-down": "#ef5350", // down candles, bars and profile fills
  "--chart-gap": "#d84315", // Story 32.1's gap placeholder bars and their labels: used by nothing else, so a gap can never be mistaken for any other mark (was #ff9100 on black)
  "--chart-marker": "#455a64", // the replay vertical marker
  "--chart-drawing": "#2962ff", // new trendlines, horizontal lines, measurements and the drawing menu's default
  "--chart-poc": "#1b5e20", // the volume profile's point-of-control line
  "--chart-value-area": "#2196f3", // the volume profile's Value Area band: a fill drawn at low alpha behind the bars, never a mark
  "--chart-pane-1": "#2962ff", // indicator slot 1 (TradingView blue)
  "--chart-pane-2": "#f23645", // indicator slot 2
  "--chart-pane-3": "#089981", // indicator slot 3
  "--chart-pane-4": "#b26a00", // indicator slot 4 (amber: TradingView's #ff9800 reads 2.2:1 on white, under the 3:1 floor)
  "--chart-pane-5": "#9c27b0", // indicator slot 5
  "--chart-pane-6": "#00838f", // indicator slot 6 (teal: TradingView's #00bcd4 reads 2.3:1 on white)
  "--chart-pane-7": "#795548", // indicator slot 7
  "--chart-pane-8": "#131722", // indicator slot 8
  "--chart-line": "#2962ff", // Story 33.9: the Line and Area main series (TradingView's line blue)
  "--chart-compare-1": "#ad1457", // Story 33.9: compare symbol 1 (pink), apart from the candle and indicator slots
  "--chart-compare-2": "#4527a0", // Story 33.9: compare symbol 2 (deep purple)
  "--chart-compare-3": "#827717", // Story 33.9: compare symbol 3 (olive)
} as const;

export type ChartToken = keyof typeof CHART_TOKENS;

/** The eight indicator pane colours, in slot order (paneColors.ts assigns the slots). */
export const CHART_PANE_TOKENS = [
  "--chart-pane-1",
  "--chart-pane-2",
  "--chart-pane-3",
  "--chart-pane-4",
  "--chart-pane-5",
  "--chart-pane-6",
  "--chart-pane-7",
  "--chart-pane-8",
] as const satisfies readonly ChartToken[];

/** The element the chart tokens are scoped to: the chart page's `.chart-workspace` container.
 * Falls back to the root element (where the tokens are not declared, so `chartVar` then returns
 * the table's fallback) when no chart is mounted, e.g. a unit test rendering one primitive. */
export function chartScope(): Element {
  return document.querySelector(".chart-workspace") ?? document.documentElement;
}

/** Resolve a chart token into a literal colour string a canvas API can use, read at call time
 * from the chart container so it reflects whatever stylesheet is applied; the table's fallback
 * when the token is unset (jsdom, or a stylesheet that has not loaded yet). */
export function chartVar(name: ChartToken): string {
  if (typeof document === "undefined") return CHART_TOKENS[name];
  const value = getComputedStyle(chartScope()).getPropertyValue(name).trim();
  return value || CHART_TOKENS[name];
}

/** The `[r, g, b]` channels of a `#rgb`, `#rrggbb`, `rgb(...)` or `rgba(...)` colour (the notations
 * a computed custom property holds for a hex or rgb literal), else null. */
function rgbChannels(value: string): [number, number, number] | null {
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((d) => d + d) : (hex[1].match(/../g) ?? []);
    const [r, g, b] = digits.map((pair) => parseInt(pair, 16));
    return [r, g, b];
  }
  const rgb = /^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+%?\s*)?\)$/i.exec(value);
  if (!rgb) return null;
  const channels = [rgb[1], rgb[2], rgb[3]].map(Number);
  if (channels.some((c) => c > 255)) return null;
  return [channels[0], channels[1], channels[2]];
}

/** Story 33.9: a token at `alpha` (0..1) for a fill under a line (the Area and Baseline series), as
 * `rgba(r, g, b, alpha)` from the resolved `#rgb`, `#rrggbb`, `rgb(...)` or `rgba(...)` value (its own
 * alpha replaced). A value in any other notation is a stylesheet defect: a console error names the
 * token and the fill is transparent, never an opaque fill over the pane. */
export function chartVarAlpha(name: ChartToken, alpha: number): string {
  const value = chartVar(name);
  const channels = rgbChannels(value);
  if (channels === null) {
    console.error(`chart token ${name}: unparseable colour ${JSON.stringify(value)}, its fill is transparent`);
    return "rgba(0, 0, 0, 0)";
  }
  const a = Math.min(1, Math.max(0, alpha));
  return `rgba(${channels[0]}, ${channels[1]}, ${channels[2]}, ${a})`;
}

/** The pane palette, resolved through `chartVar`. */
export function chartPalette(): string[] {
  return CHART_PANE_TOKENS.map((token) => chartVar(token));
}

// Story 32.5: each Fibonacci ratio's default colour, a chart token resolved when the drawing is made.
const FIB_LEVEL_TOKENS: Record<number, ChartToken> = {
  0: "--chart-text-dim",
  0.236: "--chart-pane-2",
  0.382: "--chart-pane-4",
  0.5: "--chart-pane-3",
  0.618: "--chart-pane-6",
  0.786: "--chart-pane-5",
  1: "--chart-text-dim",
  1.272: "--chart-pane-7",
  1.618: "--chart-pane-1",
  2.618: "--chart-pane-8",
  4.236: "--chart-pane-2",
};

/** A Fibonacci ratio's default colour, for the placed drawing and its drag preview alike. */
export function fibLevelColor(ratio: number): string {
  return chartVar(FIB_LEVEL_TOKENS[ratio] ?? "--chart-drawing");
}

/**
 * Story 33.10: the chart tokens a new drawing is made with (`buildDrawing`), resolved now: the page's
 * placement and the chart's preview of it read this one function, so the preview is the drawing.
 */
export function newDrawingContext(precision: InstrumentPrecision | null): NewDrawingContext {
  return {
    precision,
    color: chartVar("--chart-drawing"),
    upColor: chartVar("--chart-up"),
    downColor: chartVar("--chart-down"),
    bandColor: chartVar("--chart-pane-4"),
    fibColor: fibLevelColor,
  };
}
