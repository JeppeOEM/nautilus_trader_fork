/**
 * The chart's drawings as plain data (Story 32.5): the wire/storage types of the four kinds, the
 * pure geometry of the Fibonacci retracement and the Long/Short position, and how a dragged handle
 * changes a drawing. No DOM and no chart library here, so the maths is tested on its own and the
 * primitives, the page and the persisted file all read one definition.
 *
 * Anchors are `{time, price}` in UTC seconds and the instrument's price: never screen coordinates
 * and never a bar index, so a drawing survives a timeframe change and a Candles/Lines switch. A
 * time between two bars is drawn on the earlier bar (`snapIndex`); the stored anchor is untouched.
 *
 * Every price a drawing derives (a level, a stop, a target) is rounded to the instrument's own
 * `price_precision` (`roundToPrecision`) and printed through `formatDecimal`, so no label carries
 * float noise.
 *
 * Known limit: prices snap to the `10^-price_precision` grid, not to the instrument's tick size
 * (the catalog definition's `price_increment`, which the candles response does not carry). An
 * instrument whose tick is coarser than its precision (a 0.5 tick at precision 2) can therefore
 * hold a stop or target between two ticks. Upgrade path: add `price_increment` next to the
 * precision on the candles response and snap to it here.
 */

import { type AnchoredVwapSource, DEFAULT_VWAP_SOURCE } from "./anchoredVwap";
import { LINE_STYLES, type LineStyleName } from "./indicatorStyle";
import type { ChartTool } from "./chartTools";
import { formatDecimal, roundToPrecision } from "./units";

// Story 33.10: a drawing's line style is one of the indicator styles (one closed set, mirrored by
// `views.preferences.LINE_STYLES`; `test_line_styles_mirror_the_frontend`).
export { LINE_STYLES };
export type { LineStyleName };

export interface Anchor {
  /** UTC seconds of a bar (any time inside a bar draws on that bar). */
  time: number;
  price: number;
}

/**
 * A bar time as stored: whole UTC seconds (the server refuses anything else). The Lines view's
 * bars are 1-second snapshots stamped mid-second (`capture/domain/sampler.py`: `second + 0.5 s`),
 * so a pointer time can be fractional. Rounded *up*: every chart bar is at least a second apart,
 * so `ceil(t)` is before the next bar and still snaps (`snapIndex`) to the bar `t` is on, where
 * `floor` would land one bar early.
 */
export function storedTime(time: number): number {
  return Math.ceil(time);
}

/**
 * Story 33.8: the price of the trendline through `anchors` at `t` (UTC seconds), extended beyond
 * both anchors; null for a vertical line (equal anchor times). The one formula an alert's
 * `trendline_cross` and the chart share: `alerting/domain/geometry.py`'s `trendline_price_at` is
 * its port, evaluating the same expression in the same order, and both are tested against one
 * fixture (`alerting/tests/fixtures/trendline_cases.json`).
 */
export function trendlinePriceAt(anchors: readonly [Anchor, Anchor], t: number): number | null {
  const [a, b] = anchors;
  const span = b.time - a.time;
  if (span === 0) return null;
  return a.price + ((b.price - a.price) * (t - a.time)) / span;
}

/**
 * Story 33.10: what every kind may carry, each absent on a drawing saved before it (absent = false):
 * `locked` (no handles, no grab; its menu still opens) and `hidden` (neither drawn nor hit-tested).
 */
export interface DrawingFlags {
  locked?: boolean;
  hidden?: boolean;
}

/** Story 33.10: the look of a line-like kind; absent = 1 px, solid. */
export interface LineLook {
  line_width?: number;
  line_style?: LineStyleName;
}

export interface HlineDrawing extends DrawingFlags, LineLook {
  kind: "hline";
  id: string;
  price: number;
  color?: string;
}

export interface TrendlineDrawing extends DrawingFlags, LineLook {
  kind: "trendline";
  id: string;
  anchors: [Anchor, Anchor];
  color?: string;
}

/** Story 33.10: a trendline extended past B (`ray`) or past both anchors (`extended`), or ending in
 * an arrow head at B (`arrow`). The extension comes from the kind (`extendOf`), never a stored field. */
export interface RayDrawing extends DrawingFlags, LineLook {
  kind: "ray" | "extended" | "arrow";
  id: string;
  anchors: [Anchor, Anchor];
  color?: string;
}

/** Story 33.10: a vertical line at a bar. */
export interface VlineDrawing extends DrawingFlags, LineLook {
  kind: "vline";
  id: string;
  /** UTC seconds of the bar. */
  time: number;
  color?: string;
}

/** Story 33.10: a rectangle between two opposite corners, filled at `fill_opacity` (0..1). */
export interface RectDrawing extends DrawingFlags, LineLook {
  kind: "rect";
  id: string;
  anchors: [Anchor, Anchor];
  fill_opacity: number;
  color?: string;
}

/** Story 33.10: a parallel channel: the A-B line and its parallel `offset` (a price) away. */
export interface ChannelDrawing extends DrawingFlags, LineLook {
  kind: "channel";
  id: string;
  anchors: [Anchor, Anchor];
  offset: number;
  color?: string;
}

/** Story 33.10: a text note, its box's top-left corner at `anchor`. */
export interface TextDrawing extends DrawingFlags {
  kind: "text";
  id: string;
  anchor: Anchor;
  text: string;
  font_size: number;
  color?: string;
}

/** Story 33.10: the measured price range (`price_range`) or date range (`date_range`) between A and B. */
export interface RangeDrawing extends DrawingFlags, LineLook {
  kind: "price_range" | "date_range";
  id: string;
  anchors: [Anchor, Anchor];
  color?: string;
}

export interface FibLevel {
  ratio: number;
  enabled: boolean;
  color: string;
}

export type LabelSide = "left" | "right";

export interface FibDrawing extends DrawingFlags {
  kind: "fib";
  id: string;
  /** A and B of the drag: ratio 1 sits on A, ratio 0 on B. */
  anchors: [Anchor, Anchor];
  levels: FibLevel[];
  extend_right: boolean;
  label_side: LabelSide;
  line_width: number;
  color?: string;
}

export type PositionSide = "long" | "short";

/**
 * Story 33.10: a trend-based Fibonacci extension: A, B and C, each level at `C + (B - A) * ratio`
 * (`fibExtensionLevelPrices`), with the retracement's options.
 */
export interface FibExtensionDrawing extends DrawingFlags {
  kind: "fib_extension";
  id: string;
  anchors: [Anchor, Anchor, Anchor];
  levels: FibLevel[];
  extend_right: boolean;
  label_side: LabelSide;
  line_width: number;
  color?: string;
}

export interface PositionDrawing extends DrawingFlags {
  kind: "position";
  id: string;
  side: PositionSide;
  /** UTC seconds of the bar the position was placed on (its left edge). */
  time: number;
  entry: number;
  stop: number;
  target: number;
  width_bars: number;
  /** Optional sizing: both set or both absent (the server refuses one alone). */
  account?: number;
  risk_pct?: number;
  color?: string;
}

/**
 * An Anchored Volume Profile (Story 32.7): one click at a bar; the one engine's profile
 * (`buildVolumeProfile`) from that bar to the latest, growing rightward from the anchor and
 * following new bars. Nothing but the anchor and the look is stored: the rows are recomputed.
 */
export interface AnchoredVpDrawing extends DrawingFlags {
  kind: "anchored_vp";
  id: string;
  /** UTC seconds of the anchor bar. */
  time: number;
  rows: number;
  value_area_pct: number;
  up_color: string;
  down_color: string;
}

/** An Anchored VWAP (Story 32.7): one click at a bar; the line (and optional bands) from that bar on. */
export interface AnchoredVwapDrawing extends DrawingFlags {
  kind: "anchored_vwap";
  id: string;
  time: number;
  /** A bar price, or (Story 33.6) `stored`: the bars' exact stored `pv`/volume, served by the server. */
  source: AnchoredVwapSource;
  /** The ±1σ and ±2σ bands. */
  bands: boolean;
  color?: string;
  band_color: string;
}

export type Drawing =
  | HlineDrawing
  | TrendlineDrawing
  | FibDrawing
  | PositionDrawing
  | AnchoredVpDrawing
  | AnchoredVwapDrawing
  | RayDrawing
  | VlineDrawing
  | RectDrawing
  | ChannelDrawing
  | TextDrawing
  | FibExtensionDrawing
  | RangeDrawing;
export type DrawingKind = Drawing["kind"];

/** The decimals the catalog's instrument definition prescribes (`GET /api/candles`). */
export interface InstrumentPrecision {
  price: number;
  size: number;
}

export const MAX_LINE_WIDTH = 4;
/** Story 33.10: a line-like kind's width and style when it stores none. */
export const DEFAULT_DRAWING_LINE_WIDTH = 1;
export const DEFAULT_DRAWING_LINE_STYLE: LineStyleName = "solid";

// Story 33.10: the text note's bounds, mirrored by `views.preferences` (`MAX_DRAWING_TEXT_LENGTH`,
// `MIN_DRAWING_FONT_SIZE`, `MAX_DRAWING_FONT_SIZE`; `test_text_and_font_bounds_mirror_the_frontend`
// reads these three lines, so keep each a plain `export const NAME = N;`).
export const MAX_TEXT_LENGTH = 500;
export const MIN_FONT_SIZE = 8;
export const MAX_FONT_SIZE = 72;
export const DEFAULT_FONT_SIZE = 14;
/** What a placed text note says until its dialog (opened on placement) sets it. */
export const DEFAULT_TEXT = "Text";
/** A new rectangle's fill opacity. */
export const DEFAULT_RECT_OPACITY = 0.2;

/** The bounds of an Anchored VP's row count (the layout's `MIN_PROFILE_ROWS`/`MAX_PROFILE_ROWS`). */
export const MIN_AVP_ROWS = 2;
export const MAX_AVP_ROWS = 500;
export const DEFAULT_AVP_ROWS = 24;
export const DEFAULT_AVP_VALUE_AREA_PCT = 70;

/** A new Anchored VP at the clicked bar; the colours are the page's chart tokens. */
export function newAnchoredVp(id: string, time: number, upColor: string, downColor: string): AnchoredVpDrawing {
  return {
    kind: "anchored_vp",
    id,
    time: storedTime(time),
    rows: DEFAULT_AVP_ROWS,
    value_area_pct: DEFAULT_AVP_VALUE_AREA_PCT,
    up_color: upColor,
    down_color: downColor,
  };
}

/** A new Anchored VWAP at the clicked bar: `hlc3`, bands off. */
export function newAnchoredVwap(id: string, time: number, color: string, bandColor: string): AnchoredVwapDrawing {
  return { kind: "anchored_vwap", id, time: storedTime(time), source: DEFAULT_VWAP_SOURCE, bands: false, color, band_color: bandColor };
}

// -- Fibonacci ------------------------------------------------------------------------------------

/** The retracement ratios, with the ones drawn by default first (spec: 0 ... 1 on, extensions off). */
export const FIB_DEFAULT_RATIOS: readonly { ratio: number; enabled: boolean }[] = [
  { ratio: 0, enabled: true },
  { ratio: 0.236, enabled: true },
  { ratio: 0.382, enabled: true },
  { ratio: 0.5, enabled: true },
  { ratio: 0.618, enabled: true },
  { ratio: 0.786, enabled: true },
  { ratio: 1, enabled: true },
  { ratio: 1.272, enabled: false },
  { ratio: 1.618, enabled: false },
  { ratio: 2.618, enabled: false },
  { ratio: 4.236, enabled: false },
];

/** A new Fibonacci's levels; `colorOf` gives each ratio's default colour (the page resolves chart tokens). */
export function defaultFibLevels(colorOf: (ratio: number) => string): FibLevel[] {
  return FIB_DEFAULT_RATIOS.map(({ ratio, enabled }) => ({ ratio, enabled, color: colorOf(ratio) }));
}

/** The price of a ratio between A and B: `B + (A - B) * ratio` (0 on B, 1 on A). */
export function fibPrice(a: number, b: number, ratio: number): number {
  return b + (a - b) * ratio;
}

export interface FibLevelPrice {
  ratio: number;
  price: number;
  color: string;
}

/** The enabled levels of `fib`, ascending by ratio, with their prices at the instrument's grid. */
export function fibLevelPrices(fib: FibDrawing, pricePrecision: number | null): FibLevelPrice[] {
  const [a, b] = fib.anchors;
  return fib.levels
    .filter((level) => level.enabled)
    .sort((x, y) => x.ratio - y.ratio)
    .map((level) => {
      const price = fibPrice(a.price, b.price, level.ratio);
      return { ratio: level.ratio, price: safeRound(price, pricePrecision), color: level.color };
    });
}

/** Story 33.10: a new Fibonacci extension's ratios: the projection targets on, the deep ones off. */
export const FIB_EXTENSION_DEFAULT_RATIOS: readonly { ratio: number; enabled: boolean }[] = [
  { ratio: 0, enabled: true },
  { ratio: 0.236, enabled: false },
  { ratio: 0.382, enabled: true },
  { ratio: 0.5, enabled: true },
  { ratio: 0.618, enabled: true },
  { ratio: 0.786, enabled: false },
  { ratio: 1, enabled: true },
  { ratio: 1.272, enabled: true },
  { ratio: 1.618, enabled: true },
  { ratio: 2.618, enabled: true },
  { ratio: 4.236, enabled: false },
];

export function defaultFibExtensionLevels(colorOf: (ratio: number) => string): FibLevel[] {
  return FIB_EXTENSION_DEFAULT_RATIOS.map(({ ratio, enabled }) => ({ ratio, enabled, color: colorOf(ratio) }));
}

/**
 * Story 33.10: the enabled levels of a Fibonacci extension, ascending by ratio, each at
 * `C + (B - A) * ratio` (ratio 0 on C, 1 a full A-B move projected from C) on the instrument's grid.
 */
export function fibExtensionLevelPrices(d: FibExtensionDrawing, pricePrecision: number | null): FibLevelPrice[] {
  const [a, b, c] = d.anchors;
  return d.levels
    .filter((level) => level.enabled)
    .sort((x, y) => x.ratio - y.ratio)
    .map((level) => {
      const price = c.price + (b.price - a.price) * level.ratio;
      return { ratio: level.ratio, price: safeRound(price, pricePrecision), color: level.color };
    });
}

/** "0.618 (92.36)": the ratio as written (no trailing zeros) and the price at the instrument precision. */
export function fibLabel(ratio: number, price: number, pricePrecision: number): string {
  return `${ratio} (${safeDecimal(price, pricePrecision)})`;
}

// -- Position -------------------------------------------------------------------------------------

/** The stop's distance from the entry, as a percent of the entry. */
export const STOP_PCT = 1;
/** The target's distance in multiples of the stop's distance (the default risk/reward). */
export const TARGET_R = 2;
/** The default width of a placed position, in bars. */
export const DEFAULT_WIDTH_BARS = 40;
/** The widest position the server stores (`views.preferences.MAX_DRAWING_WIDTH_BARS`). */
export const MAX_WIDTH_BARS = 10_000;

const MINUS = "−";

/** One price tick at `pricePrecision` decimals: the smallest step a stop or target may keep from the entry. */
export function tickOf(pricePrecision: number): number {
  return 10 ** -pricePrecision;
}

/**
 * A position placed by one click: the entry at the clicked price (on the precision grid), the stop
 * `STOP_PCT` of the entry away (at least one tick) and the target `TARGET_R` stop-distances the
 * other way. Long: target above, stop below; Short: the mirror.
 */
export function newPosition(
  id: string,
  side: PositionSide,
  time: number,
  clickedPrice: number,
  pricePrecision: number,
  color?: string,
): PositionDrawing {
  const tick = tickOf(pricePrecision);
  // At least two ticks, so the stop (long) or target (short) below it can still be above zero.
  const entry = Math.max(roundToPrecision(clickedPrice, pricePrecision), roundToPrecision(2 * tick, pricePrecision));
  const risk = Math.max(roundToPrecision((entry * STOP_PCT) / 100, pricePrecision), tick);
  const sign = side === "long" ? 1 : -1;
  const lowest = roundToPrecision(tick, pricePrecision);
  const stop = Math.max(roundToPrecision(entry - sign * risk, pricePrecision), lowest);
  const target = Math.max(roundToPrecision(entry + sign * Math.abs(entry - stop) * TARGET_R, pricePrecision), lowest);
  return { kind: "position", id, side, time: storedTime(time), entry, stop, target, width_bars: DEFAULT_WIDTH_BARS, ...(color ? { color } : {}) };
}

export interface PositionStats {
  /** |target - entry| / |entry - stop|; null when the stop sits on the entry. */
  rewardRisk: number | null;
  /** Target distance as a signed percent of the entry (a gain: positive). */
  targetPct: number;
  /** Stop distance as a signed percent of the entry (a loss: negative). */
  stopPct: number;
  /** account * risk% / |entry - stop|, floored to the size precision; null unless both inputs are set. */
  size: number | null;
}

/** Floor to the size grid, absorbing the binary noise of a division (`10000 * 0.01 / 1`). */
function floorToPrecision(value: number, precision: number): number {
  const scaled = value * 10 ** precision;
  return Math.floor(scaled + Math.abs(scaled) * 1e-12 + 1e-9) / 10 ** precision;
}

export function positionStats(p: PositionDrawing, sizePrecision: number): PositionStats {
  const risk = Math.abs(p.entry - p.stop);
  const reward = Math.abs(p.target - p.entry);
  const sized = p.account !== undefined && p.risk_pct !== undefined && risk > 0;
  // A stored position has entry > 0 (the server refuses otherwise); a non-positive or non-finite
  // entry still must not throw inside a paint, so its percents are NaN (printed as a placeholder).
  const entryOk = Number.isFinite(p.entry) && p.entry > 0;
  return {
    rewardRisk: risk > 0 ? reward / risk : null,
    targetPct: entryOk ? (reward / p.entry) * 100 : Number.NaN,
    stopPct: entryOk ? -(risk / p.entry) * 100 : Number.NaN,
    size: sized ? floorToPrecision(((p.account as number) * ((p.risk_pct as number) / 100)) / risk, sizePrecision) : null,
  };
}

/** What a label shows for a number it cannot print (non-finite): a paint must never throw. */
export const NO_VALUE = "n/a";

/** `formatDecimal` for the renderer: a value it refuses (non-finite, bad precision) is `NO_VALUE`. */
export function safeDecimal(value: number, precision: number): string {
  try {
    return formatDecimal(value, precision);
  } catch {
    return NO_VALUE;
  }
}

/** "+2.00 %" / "−1.00 %": the sign always shown, a true minus sign, two decimals; `n/a` if not finite. */
export function formatPercent(pct: number): string {
  if (!Number.isFinite(pct)) return NO_VALUE;
  const text = safeDecimal(Math.abs(pct), 2);
  return `${pct < 0 && Number(text) !== 0 ? MINUS : "+"}${text} %`;
}

export interface PositionLabels {
  target: string;
  entry: string;
  stop: string;
  rewardRisk: string;
  size: string | null;
}

/** The four labels of a position (plus the optional size), every number through `lib/units.ts`. */
export function positionLabels(p: PositionDrawing, precision: InstrumentPrecision): PositionLabels {
  const stats = positionStats(p, precision.size);
  const price = (v: number): string => safeDecimal(v, precision.price);
  return {
    target: `Target: ${price(p.target)} (${formatPercent(stats.targetPct)})`,
    entry: `Entry: ${price(p.entry)}`,
    stop: `Stop: ${price(p.stop)} (${formatPercent(stats.stopPct)})`,
    rewardRisk: `Risk/Reward: ${stats.rewardRisk === null ? NO_VALUE : safeDecimal(stats.rewardRisk, 2)}`,
    size: stats.size === null ? null : `Size: ${safeDecimal(stats.size, precision.size)}`,
  };
}

// -- Story 33.10 tools -----------------------------------------------------------------------------

/** How far a line kind runs past its anchors: a ray past B, an extended line past both. */
export type Extend = "none" | "right" | "both";

/** The trendline and the kinds drawn as one (`TrendlinePrimitive`): ray, extended line, arrow. */
export function isLineDrawing(d: Drawing): d is TrendlineDrawing | RayDrawing {
  return d.kind === "trendline" || d.kind === "ray" || d.kind === "extended" || d.kind === "arrow";
}

export function extendOf(kind: DrawingKind): Extend {
  return kind === "ray" ? "right" : kind === "extended" ? "both" : "none";
}

/** A point in a pane's CSS pixels. */
export interface Px {
  x: number;
  y: number;
}

/**
 * The segment A-B as drawn on a `width` x `height` pane (CSS px): as is for `none`, else carried past
 * B (`right`) or past both anchors (`both`) to the pane's border, so the drawn segment (and its hit
 * test) never runs to infinity. An anchor inside the extension's far side is never cut off: the
 * drawn segment always contains A-B. A = B, or a line that never crosses the pane, is returned
 * unextended (there is nothing to extend along). Pure arithmetic on finite px: it never throws.
 */
export function extendedSegment(a: Px, b: Px, extend: Extend, width: number, height: number): [Px, Px] {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (extend === "none" || (dx === 0 && dy === 0)) return [a, b];
  // Liang-Barsky: the range of t (the point A + t (B - A)) inside [0, width] x [0, height].
  let enter = -Infinity;
  let exit = Infinity;
  const bounds: [number, number][] = [
    [-dx, a.x],
    [dx, width - a.x],
    [-dy, a.y],
    [dy, height - a.y],
  ];
  for (const [p, q] of bounds) {
    if (p === 0) {
      if (q < 0) return [a, b]; // parallel to this border and outside it
      continue;
    }
    if (p < 0) enter = Math.max(enter, q / p);
    else exit = Math.min(exit, q / p);
  }
  if (enter > exit) return [a, b];
  const from = extend === "both" ? Math.min(0, enter) : 0;
  const to = Math.max(1, exit);
  return [
    { x: a.x + from * dx, y: a.y + from * dy },
    { x: a.x + to * dx, y: a.y + to * dy },
  ];
}

/**
 * A channel's offset from its third click C: the price distance from the A-B line at C's time
 * (`trendlinePriceAt`, the one line formula), or from A's price when A-B is vertical (equal times).
 */
export function channelOffsetFor(a: Anchor, b: Anchor, c: Anchor): number {
  const onLine = trendlinePriceAt([a, b], c.time);
  return c.price - (onLine ?? a.price);
}

/** "10.00 (+11.11 %)": a rectangle's price height and that height as a percent of its lower edge. */
export function rectLabel(d: RectDrawing, pricePrecision: number): string {
  const [a, b] = d.anchors;
  const low = Math.min(a.price, b.price);
  const height = Math.abs(b.price - a.price);
  const pct = low > 0 ? (height / low) * 100 : Number.NaN;
  return `${safeDecimal(height, pricePrecision)} (${formatPercent(pct)})`;
}

/** What a price or date range measures between its anchors (`computeMeasurement`'s fields). */
export interface RangeMeasure {
  priceDelta: number;
  /** Null when the start price is 0. */
  priceDeltaPct: number | null;
  /** Null when there are no candle bars to count (Lines mode, nothing loaded): printed `n/a`, never 0. */
  bars: number | null;
  volume: number | null;
}

/** "+1.50" / "−1.50": a signed price at the instrument precision, a true minus sign. */
function signedDecimal(value: number, precision: number): string {
  const text = safeDecimal(Math.abs(value), precision);
  return value < 0 && Number(text) !== 0 ? `${MINUS}${text}` : `+${text}`;
}

/**
 * The label lines of a range, every number through `lib/units.ts`: a price range shows the change
 * from A to B and its percent (TradingView's Price Range), a date range the bars it spans and their
 * volume at the size precision (TradingView's Date Range) -- `n/a` for both when no candle bars back
 * the measurement, never a fabricated 0.
 */
export function rangeLabels(m: RangeMeasure, kind: RangeDrawing["kind"], precision: InstrumentPrecision): string[] {
  if (kind === "price_range") {
    return [`${signedDecimal(m.priceDelta, precision.price)} (${formatPercent(m.priceDeltaPct ?? Number.NaN)})`];
  }
  const bars = m.bars === null ? NO_VALUE : safeDecimal(m.bars, 0);
  const volume = m.volume === null ? NO_VALUE : safeDecimal(m.volume, precision.size);
  return [`${bars} bars`, `Vol ${volume}`];
}

/**
 * How many clicks place a tool's drawing: 1, 2 or 3. 0 = not placed by clicks: the cursor, the
 * horizontal line (placed by a price click, `onPriceClick`), the Fibonacci retracement (a drag) and
 * the measure / FRVP drags.
 */
const PLACEMENT_POINTS: Partial<Record<ChartTool, number>> = {
  trendline: 2,
  ray: 2,
  extended: 2,
  arrow: 2,
  rect: 2,
  price_range: 2,
  date_range: 2,
  channel: 3,
  fib_extension: 3,
  vline: 1,
  text: 1,
  long: 1,
  short: 1,
  avp: 1,
  avwap: 1,
};

export function placementOf(tool: ChartTool): number {
  return PLACEMENT_POINTS[tool] ?? 0;
}

/** The kind of drawing a click-placed tool makes, or null for a tool that places none by clicks. */
export function kindOfTool(tool: ChartTool): DrawingKind | null {
  switch (tool) {
    case "long":
    case "short":
      return "position";
    case "avp":
      return "anchored_vp";
    case "avwap":
      return "anchored_vwap";
    default:
      return placementOf(tool) > 0 ? (tool as DrawingKind) : null;
  }
}

/** What a new drawing is made with: the page resolves the chart tokens, the candles the precision. */
export interface NewDrawingContext {
  precision: InstrumentPrecision | null;
  color: string;
  upColor: string;
  downColor: string;
  bandColor: string;
  fibColor: (ratio: number) => string;
}

const samePoint = (a: Anchor, b: Anchor): boolean => a.time === b.time && a.price === b.price;

/**
 * Whether a channel's points so far can make a channel: A and B on two bars (a vertical A-B has no
 * parallel to measure along), and C, once clicked, an offset that is not 0 on the grid (a channel of
 * no width is a second line on the first).
 */
export function channelPlaceable(points: readonly Anchor[], pricePrecision: number | null): boolean {
  if (points.length >= 2 && storedTime(points[0].time) === storedTime(points[1].time)) return false;
  if (points.length < 3) return true;
  return safeRound(channelOffsetFor(points[0], points[1], points[2]), pricePrecision) !== 0;
}

/**
 * The drawing a finished placement makes: `points` (as many as `placementOf(tool)`) stored as whole
 * UTC seconds (`storedTime`) and prices on the instrument's grid. Null for a degenerate placement --
 * two points on one another (time and price), the trendline rule, or a channel `channelPlaceable`
 * refuses -- for a wrong point count, and for a position before the precision is known.
 */
export function buildDrawing(
  tool: ChartTool,
  id: string,
  points: readonly Anchor[],
  ctx: NewDrawingContext,
): Drawing | null {
  if (points.length === 0 || points.length !== placementOf(tool)) return null;
  const places = ctx.precision?.price ?? null;
  const stored = points.map((p) => ({ time: storedTime(p.time), price: safeRound(p.price, places) }));
  // Any two points on one another (a Fibonacci extension's C on A too), as `dragAnchor` refuses.
  if (stored.some((point, i) => stored.slice(0, i).some((earlier) => samePoint(earlier, point)))) return null;
  if (tool === "channel" && !channelPlaceable(stored, places)) return null;
  if (tool === "long" || tool === "short") {
    // `newPosition` puts the entry on the grid itself, from the clicked price.
    return places === null ? null : newPosition(id, tool, points[0].time, points[0].price, places);
  }
  return shapeOf(tool, id, stored, ctx);
}

/**
 * The shape a placement in progress draws: the points so far plus the pointer (`cursor`) as the
 * next one, repeated up to the tool's count. Null for a single-click tool (nothing to preview) and
 * before the first point. Unrounded: the preview follows the pointer, the click rounds.
 */
export function previewDrawing(
  tool: ChartTool,
  points: readonly Anchor[],
  cursor: Anchor,
  ctx: NewDrawingContext,
): Drawing | null {
  const need = placementOf(tool);
  if (need < 2 || points.length === 0 || points.length >= need) return null;
  const shown = [...points, cursor];
  while (shown.length < need) shown.push(cursor);
  return shapeOf(tool, "preview", shown, ctx);
}

/** The drawing of a two- or three-point tool (or a one-click vline / text / anchored drawing) at `p`. */
function shapeOf(tool: ChartTool, id: string, p: readonly Anchor[], ctx: NewDrawingContext): Drawing | null {
  const color = ctx.color;
  const two: [Anchor, Anchor] = [p[0], p[1] ?? p[0]];
  switch (tool) {
    case "trendline":
      return { kind: "trendline", id, anchors: two, color };
    case "ray":
    case "extended":
    case "arrow":
      return { kind: tool, id, anchors: two, color };
    case "price_range":
    case "date_range":
      return { kind: tool, id, anchors: two, color };
    case "rect":
      return { kind: "rect", id, anchors: two, fill_opacity: DEFAULT_RECT_OPACITY, color };
    case "channel": {
      const offset = p.length > 2 ? safeRound(channelOffsetFor(two[0], two[1], p[2]), ctx.precision?.price ?? null) : 0;
      return { kind: "channel", id, anchors: two, offset, color };
    }
    case "fib_extension":
      return {
        kind: "fib_extension",
        id,
        anchors: [p[0], p[1], p[2]],
        levels: defaultFibExtensionLevels(ctx.fibColor),
        extend_right: true,
        label_side: "left",
        line_width: 1,
      };
    default:
      return oneClickShape(tool, id, p[0], ctx);
  }
}

function oneClickShape(tool: ChartTool, id: string, at: Anchor, ctx: NewDrawingContext): Drawing | null {
  switch (tool) {
    case "vline":
      return { kind: "vline", id, time: at.time, color: ctx.color };
    case "text":
      return { kind: "text", id, anchor: at, text: DEFAULT_TEXT, font_size: DEFAULT_FONT_SIZE, color: ctx.color };
    case "avp":
      return newAnchoredVp(id, at.time, ctx.upColor, ctx.downColor);
    case "avwap":
      return newAnchoredVwap(id, at.time, ctx.color, ctx.bandColor);
    default:
      return null;
  }
}

// -- bars -----------------------------------------------------------------------------------------

/**
 * The index of the latest bar at or before `time` in `times` (ascending), or null when `times` is
 * empty or `time` precedes every bar: a drawing anchored in a gap or between two bars of a coarser
 * timeframe is drawn on the earlier bar, and one older than the loaded data is not drawn at all.
 */
export function snapIndex(times: readonly number[], time: number): number | null {
  let low = 0;
  let high = times.length - 1;
  let found: number | null = null;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (times[mid] <= time) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

// -- dragging -------------------------------------------------------------------------------------

/** Where the pointer is while a handle is dragged, as the chart resolves it. */
export interface DragPoint {
  price: number;
  /** The bar time under the pointer (clamped to the loaded bars), or null with no bars. */
  time: number | null;
  /** Bars from the bar `time` falls on to the pointer's bar (may be negative); null with no bars. */
  barsSince: (time: number) => number | null;
  /** Story 33.10: Shift was held, and the chart constrained the point to 0/45/90 degrees. */
  shift?: boolean;
}

const round = (value: number, precision: number | null): number =>
  precision === null ? value : roundToPrecision(value, precision);

/**
 * `round` for the renderer (a primitive's `updateAllViews` is a paint and must never throw): a value
 * whose units are beyond safe integers (an extension level of a wide retracement at a high
 * precision) is drawn unrounded, and its label, through `safeDecimal`, reads `NO_VALUE`.
 */
function safeRound(value: number, precision: number | null): number {
  try {
    return round(value, precision);
  } catch {
    return value;
  }
}

/**
 * The drawing after its `handle` is dragged to `point`. Pure: the chart reports the pointer, this
 * decides what it means.
 * - anchored VP and VWAP `anchor`, vline `time`: the time moves to the pointer's bar (no price).
 * - hline `price`; the anchors `a`/`b` (`c` of a Fibonacci extension) of every anchored kind -- a
 *   rectangle's two corners included: the anchor moves to the pointer's bar and price.
 * - channel `a`/`b`: the anchor moves and the offset is kept; `offset`: the parallel follows the
 *   pointer (`channelOffsetFor`). text `anchor`: the box's corner moves to the pointer.
 * - position `entry`: the whole box moves (stop and target keep their distance from the entry);
 *   `target`/`stop`: only that price moves, and a drag past the entry is refused -- it stops one
 *   tick on the proper side; `right`: the width in bars follows the pointer (at least 1).
 * A locked drawing, and an unknown handle, return the drawing unchanged.
 */
export function applyHandleDrag(
  drawing: Drawing,
  handle: string,
  point: DragPoint,
  pricePrecision: number | null,
): Drawing {
  if (drawing.locked) return drawing;
  // Throw-safe: a pointer price whose units pass safe integers (a far zoom-out) must not throw inside
  // the drag handler; it is kept unrounded, as a paint keeps it (`safeRound`).
  const price = safeRound(point.price, pricePrecision);
  switch (drawing.kind) {
    case "hline":
      // The server stores only a price above zero: a drag to or below zero stops where it was.
      return handle === "price" && price > 0 ? { ...drawing, price } : drawing;
    case "trendline":
    case "ray":
    case "extended":
    case "arrow":
    case "rect":
    case "price_range":
    case "date_range":
    case "fib":
    case "fib_extension":
      return dragAnchor(drawing, handle, point, price);
    case "channel":
      return dragChannel(drawing, handle, point, price, pricePrecision);
    case "text":
      return handle === "anchor" ? { ...drawing, anchor: movedAnchor(drawing.anchor, point, price) } : drawing;
    case "position":
      return dragPosition(drawing, handle, point, price, pricePrecision);
    case "vline":
      return handle === "time" && point.time !== null ? { ...drawing, time: storedTime(point.time) } : drawing;
    case "anchored_vp":
    case "anchored_vwap":
      return handle === "anchor" && point.time !== null ? { ...drawing, time: storedTime(point.time) } : drawing;
  }
}

const ANCHOR_HANDLES = ["a", "b", "c"];

/** An anchor at the pointer's bar (kept where it was with no bar under the pointer) and price. */
function movedAnchor(anchor: Anchor, point: DragPoint, price: number): Anchor {
  return { time: point.time === null ? anchor.time : storedTime(point.time), price };
}

/**
 * The drawing with anchor `handle` at the pointer, or the drawing itself when the move would put it
 * on another anchor (time and price): a zero-length line, range or Fibonacci, a rectangle of no size,
 * the placement's degenerate case refused on a drag too.
 */
function dragAnchor<T extends { anchors: Anchor[] }>(drawing: T, handle: string, point: DragPoint, price: number): T {
  const index = ANCHOR_HANDLES.indexOf(handle);
  if (index === -1 || index >= drawing.anchors.length) return drawing;
  const moved = [...drawing.anchors];
  moved[index] = movedAnchor(moved[index], point, price);
  if (moved.some((anchor, i) => i !== index && samePoint(anchor, moved[index]))) return drawing;
  return { ...drawing, anchors: moved };
}

function dragChannel(
  d: ChannelDrawing,
  handle: string,
  point: DragPoint,
  price: number,
  pricePrecision: number | null,
): ChannelDrawing {
  if (handle !== "offset") {
    // Its A and B stay on two bars, as a placement requires (`channelPlaceable`).
    const moved = dragAnchor(d, handle, point, price);
    return moved.anchors[0].time === moved.anchors[1].time ? d : moved;
  }
  const [a, b] = d.anchors;
  // With no bar under the pointer the parallel is measured at the channel's middle. The throw-safe
  // rounding: a projected price whose units pass safe integers must not throw inside a drag.
  const time = point.time ?? (a.time + b.time) / 2;
  const offset = safeRound(channelOffsetFor(a, b, { time, price: point.price }), pricePrecision);
  return offset === 0 ? d : { ...d, offset };
}

function dragPosition(
  p: PositionDrawing,
  handle: string,
  point: DragPoint,
  price: number,
  pricePrecision: number | null,
): PositionDrawing {
  const tick = pricePrecision === null ? 0 : tickOf(pricePrecision);
  // Every price of a position stays above zero: the lowest a price may be dragged to.
  const floor = tick > 0 ? tick : Number.MIN_VALUE;
  const long = p.side === "long";
  switch (handle) {
    case "entry": {
      // The whole box moves; the shift is limited so the lowest of entry/stop/target keeps a tick.
      const lowest = Math.min(p.entry, p.stop, p.target);
      const delta = Math.max(price - p.entry, floor - lowest);
      return {
        ...p,
        time: point.time === null ? p.time : storedTime(point.time),
        entry: safeRound(p.entry + delta, pricePrecision),
        stop: safeRound(p.stop + delta, pricePrecision),
        target: safeRound(p.target + delta, pricePrecision),
      };
    }
    case "target": {
      const target = long ? Math.max(price, p.entry + tick) : Math.min(price, p.entry - tick);
      return ordered({ ...p, target: Math.max(safeRound(target, pricePrecision), floor) }) ?? p;
    }
    case "stop": {
      const stop = long ? Math.min(price, p.entry - tick) : Math.max(price, p.entry + tick);
      return ordered({ ...p, stop: Math.max(safeRound(stop, pricePrecision), floor) }) ?? p;
    }
    case "right": {
      const bars = point.barsSince(p.time);
      return bars === null ? p : { ...p, width_bars: Math.min(MAX_WIDTH_BARS, Math.max(1, bars)) };
    }
    default:
      return p;
  }
}

/**
 * `p` when its prices are ordered for its side (a long's `stop < entry < target`, a short's mirror),
 * else null. The tick clamp guarantees this on the grid; without a precision (no tick yet) a drag
 * onto the entry itself is the one case it catches, and the drag stops where it was.
 */
function ordered(p: PositionDrawing): PositionDrawing | null {
  const ok = p.side === "long" ? p.stop < p.entry && p.entry < p.target : p.target < p.entry && p.entry < p.stop;
  return ok ? p : null;
}

// -- ids and wire ---------------------------------------------------------------------------------

/** The next unused `<kind>-<n>` id: one more than the largest numeric suffix of that kind. */
export function nextDrawingId(drawings: readonly Drawing[], kind: DrawingKind): string {
  const prefix = `${kind}-`;
  const max = drawings.reduce((acc, d) => {
    if (!d.id.startsWith(prefix)) return acc;
    const n = Number(d.id.slice(prefix.length));
    return Number.isInteger(n) ? Math.max(acc, n) : acc;
  }, 0);
  return `${prefix}${max + 1}`;
}

// Mirrored by `views.preferences.DRAWING_KINDS`, in its order (`test_the_closed_sets_mirror_the_frontend`).
export const DRAWING_KIND_NAMES: readonly string[] = [
  "hline",
  "trendline",
  "fib",
  "position",
  "anchored_vp",
  "anchored_vwap",
  "ray",
  "extended",
  "vline",
  "rect",
  "channel",
  "text",
  "arrow",
  "fib_extension",
  "price_range",
  "date_range",
];

/**
 * Story 33.10: a stored drawing of a kind this client does not know (a newer server). The load fails
 * loudly and permanently (it is not retried: the same list would fail the same way) and nothing is
 * ever saved, so the item can never be dropped by a save (DATA-07).
 */
export class UnknownDrawingKindError extends Error {
  readonly kind: unknown;

  constructor(kind: unknown) {
    super(`unknown drawing kind ${JSON.stringify(kind)}`);
    this.name = "UnknownDrawingKindError";
    this.kind = kind;
  }
}

/**
 * The drawings a `GET /api/coin/{iid}/drawings` answered. The server validated every item
 * (`views.preferences.validate_drawing`), so this only narrows the type; an item of an unknown
 * kind (a newer server) throws `UnknownDrawingKindError` rather than being dropped, so a save can
 * never erase it.
 */
export function parseDrawings(items: readonly Record<string, unknown>[]): Drawing[] {
  return items.map((item) => {
    if (typeof item.kind !== "string" || !DRAWING_KIND_NAMES.includes(item.kind)) {
      throw new UnknownDrawingKindError(item.kind);
    }
    return item as unknown as Drawing;
  });
}

/** The old browser-local horizontal lines (`chart-hlines:{iid}`), as drawings of this resource. */
export function importLegacyHlines(raw: unknown, existing: readonly Drawing[]): HlineDrawing[] {
  if (!Array.isArray(raw)) return [];
  const taken: Drawing[] = [...existing];
  const out: HlineDrawing[] = [];
  for (const entry of raw) {
    const price = (entry as { price?: unknown } | null)?.price;
    // Not a line the resource can store (the server takes only a finite price above zero): never
    // imported, so it can't make every save of the coin a 422; the key is removed with the import.
    if (typeof price !== "number" || !Number.isFinite(price) || price <= 0) continue;
    const color = (entry as { color?: unknown }).color;
    const line: HlineDrawing = {
      kind: "hline",
      id: nextDrawingId(taken, "hline"),
      price,
      ...(typeof color === "string" ? { color } : {}),
    };
    taken.push(line);
    out.push(line);
  }
  return out;
}

// -- settings form --------------------------------------------------------------------------------

/** The position settings modal's text fields, as typed. */
export interface PositionForm {
  entry: string;
  stop: string;
  target: string;
  widthBars: string;
  /** Empty = no sizing. */
  account: string;
  riskPct: string;
}

/** The form of `p`, its prices written at the instrument precision (never `5e-7`). */
export function positionToForm(p: PositionDrawing, pricePrecision: number): PositionForm {
  const price = (v: number): string => safeDecimal(v, pricePrecision);
  return {
    entry: price(p.entry),
    stop: price(p.stop),
    target: price(p.target),
    widthBars: String(p.width_bars),
    account: p.account === undefined ? "" : String(p.account),
    riskPct: p.risk_pct === undefined ? "" : String(p.risk_pct),
  };
}

function parsePositive(text: string, label: string): number | string {
  const value = Number(text);
  return text.trim() !== "" && Number.isFinite(value) && value > 0 ? value : `${label} must be a number above zero`;
}

/**
 * The position the settings form describes, or the first refusal as text: entry, stop and target
 * numbers on the precision grid and ordered for the side (a long's stop below its entry and target
 * above, a short's the mirror), a width of whole bars, and the account and risk percent set
 * together or both empty.
 */
export function parsePositionForm(
  base: PositionDrawing,
  form: PositionForm,
  pricePrecision: number,
): PositionDrawing | string {
  const prices: Record<"entry" | "stop" | "target", number> = { entry: 0, stop: 0, target: 0 };
  for (const key of ["entry", "stop", "target"] as const) {
    const label = key[0].toUpperCase() + key.slice(1);
    const value = parsePositive(form[key], label);
    if (typeof value === "string") return value;
    try {
      prices[key] = roundToPrecision(value, pricePrecision);
    } catch {
      return `${label} is too large`; // its units are beyond safe integers: not exactly representable
    }
    // Rounded onto the grid, a positive input under half a tick is 0, which the server refuses.
    if (prices[key] <= 0) return `${label} must be at least one tick`;
  }
  const { entry, stop, target } = prices;
  const long = base.side === "long";
  if (long ? !(stop < entry && entry < target) : !(target < entry && entry < stop)) {
    return long ? "A long needs stop < entry < target" : "A short needs target < entry < stop";
  }
  const width = Number(form.widthBars);
  if (!Number.isInteger(width) || width < 1 || width > MAX_WIDTH_BARS) {
    return `Width must be a whole number of bars, 1 to ${MAX_WIDTH_BARS}`;
  }
  const { account: accountText, riskPct: riskText } = form;
  const next: PositionDrawing = { ...base, entry, stop, target, width_bars: width };
  delete next.account;
  delete next.risk_pct;
  if (accountText.trim() === "" && riskText.trim() === "") return next;
  const account = parsePositive(accountText, "Account size");
  if (typeof account === "string") return account;
  const risk = parsePositive(riskText, "Risk %");
  if (typeof risk === "string") return risk;
  return { ...next, account, risk_pct: risk };
}

/** The Anchored VP settings form's text fields, as typed. */
export interface AnchoredVpForm {
  rows: string;
  valueAreaPct: string;
  upColor: string;
  downColor: string;
}

export function anchoredVpToForm(d: AnchoredVpDrawing): AnchoredVpForm {
  return { rows: String(d.rows), valueAreaPct: String(d.value_area_pct), upColor: d.up_color, downColor: d.down_color };
}

/** The Anchored VP the form describes (rows a whole number in range, value area in (0, 100]), or the first refusal. */
export function parseAnchoredVpForm(base: AnchoredVpDrawing, form: AnchoredVpForm): AnchoredVpDrawing | string {
  const rows = Number(form.rows);
  if (form.rows.trim() === "" || !Number.isInteger(rows) || rows < MIN_AVP_ROWS || rows > MAX_AVP_ROWS) {
    return `Rows must be a whole number, ${MIN_AVP_ROWS} to ${MAX_AVP_ROWS}`;
  }
  const area = Number(form.valueAreaPct);
  if (form.valueAreaPct.trim() === "" || !Number.isFinite(area) || area <= 0 || area > 100) {
    return "Value area must be above 0 and at most 100 %";
  }
  return { ...base, rows, value_area_pct: area, up_color: form.upColor, down_color: form.downColor };
}

/**
 * A text's length as the server counts it (Python's `len`: code points), never UTF-16 units, so an
 * emoji counts once on both sides of the 500-character bound.
 */
export function textLength(text: string): number {
  return [...text].length;
}

// The separators Python's `str.strip()` removes that JavaScript's `trim()` keeps (U+001C..U+001F and
// NEL). `trim()` also removes U+FEFF, which Python keeps: the client is then stricter, never looser.
const PYTHON_ONLY_SPACE: ReadonlySet<string> = new Set([0x1c, 0x1d, 0x1e, 0x1f, 0x85].map((code) => String.fromCharCode(code)));

/** Whether the server would refuse `text` as blank (`not text.strip()`), or the client more strictly. */
export function isBlankText(text: string): boolean {
  return [...text].filter((ch) => !PYTHON_ONLY_SPACE.has(ch)).join("").trim() === "";
}

/** A UTF-16 surrogate left unpaired: under the `u` flag a paired one is one code point, never matched. */
const LONE_SURROGATE = /[\uD800-\uDFFF]/u;

/** Story 33.10: the text note's settings form, as typed. */
export interface TextForm {
  text: string;
  fontSize: string;
}

/**
 * The text note the form describes, or the first refusal (the server's rules): text that is not
 * blank and at most `MAX_TEXT_LENGTH` characters, a whole font size in `MIN_FONT_SIZE`..`MAX_FONT_SIZE`;
 * `color` only when the operator changed it.
 */
export function parseTextForm(base: TextDrawing, form: TextForm, color?: string): TextDrawing | string {
  if (isBlankText(form.text)) return "The text must not be empty";
  if (textLength(form.text) > MAX_TEXT_LENGTH) return `The text must be at most ${MAX_TEXT_LENGTH} characters`;
  // A lone surrogate (half an emoji) has no UTF-8: the server would refuse the whole save.
  if (LONE_SURROGATE.test(form.text)) return "The text holds a broken character (half an emoji)";
  const size = Number(form.fontSize);
  if (form.fontSize.trim() === "" || !Number.isInteger(size) || size < MIN_FONT_SIZE || size > MAX_FONT_SIZE) {
    return `Font size must be a whole number, ${MIN_FONT_SIZE} to ${MAX_FONT_SIZE}`;
  }
  // No colour = the one the note has (or its absence: the drawing token).
  return { ...base, text: form.text, font_size: size, ...(color === undefined ? {} : { color }) };
}
