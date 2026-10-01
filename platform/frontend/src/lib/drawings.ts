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

import { formatDecimal, roundToPrecision } from "./units";

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

export interface HlineDrawing {
  kind: "hline";
  id: string;
  price: number;
  color?: string;
}

export interface TrendlineDrawing {
  kind: "trendline";
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

export interface FibDrawing {
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

export interface PositionDrawing {
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

export type Drawing = HlineDrawing | TrendlineDrawing | FibDrawing | PositionDrawing;
export type DrawingKind = Drawing["kind"];

/** The decimals the catalog's instrument definition prescribes (`GET /api/candles`). */
export interface InstrumentPrecision {
  price: number;
  size: number;
}

export const MAX_LINE_WIDTH = 4;

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
 * - hline `price`; trendline and fib `a`/`b`: the anchor moves to the pointer's bar and price.
 * - position `entry`: the whole box moves (stop and target keep their distance from the entry);
 *   `target`/`stop`: only that price moves, and a drag past the entry is refused -- it stops one
 *   tick on the proper side; `right`: the width in bars follows the pointer (at least 1).
 * An unknown handle returns the drawing unchanged.
 */
export function applyHandleDrag(
  drawing: Drawing,
  handle: string,
  point: DragPoint,
  pricePrecision: number | null,
): Drawing {
  const price = round(point.price, pricePrecision);
  switch (drawing.kind) {
    case "hline":
      // The server stores only a price above zero: a drag to or below zero stops where it was.
      return handle === "price" && price > 0 ? { ...drawing, price } : drawing;
    case "trendline":
    case "fib": {
      if (handle !== "a" && handle !== "b") return drawing;
      const index = handle === "a" ? 0 : 1;
      const moved: [Anchor, Anchor] = [drawing.anchors[0], drawing.anchors[1]];
      moved[index] = { time: point.time === null ? moved[index].time : storedTime(point.time), price };
      return { ...drawing, anchors: moved };
    }
    case "position":
      return dragPosition(drawing, handle, point, price, pricePrecision);
  }
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
        entry: round(p.entry + delta, pricePrecision),
        stop: round(p.stop + delta, pricePrecision),
        target: round(p.target + delta, pricePrecision),
      };
    }
    case "target": {
      const target = long ? Math.max(price, p.entry + tick) : Math.min(price, p.entry - tick);
      return ordered({ ...p, target: Math.max(round(target, pricePrecision), floor) }) ?? p;
    }
    case "stop": {
      const stop = long ? Math.min(price, p.entry - tick) : Math.max(price, p.entry + tick);
      return ordered({ ...p, stop: Math.max(round(stop, pricePrecision), floor) }) ?? p;
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

const KINDS: readonly string[] = ["hline", "trendline", "fib", "position"];

/**
 * The drawings a `GET /api/coin/{iid}/drawings` answered. The server validated every item
 * (`views.preferences.validate_drawing`), so this only narrows the type; an item of an unknown
 * kind (a newer server) throws rather than being dropped, so a save can never erase it.
 */
export function parseDrawings(items: readonly Record<string, unknown>[]): Drawing[] {
  return items.map((item) => {
    if (typeof item.kind !== "string" || !KINDS.includes(item.kind)) {
      throw new Error(`unknown drawing kind ${JSON.stringify(item.kind)}`);
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
