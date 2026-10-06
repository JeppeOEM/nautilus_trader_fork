import type { SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";

import type { LiquidationBarItem } from "../../api/schema";

import { eventSlot } from "../../lib/derivativeSeries";
import { safeText } from "./derivativePanes";
import { formatUnits, unitsToNumber } from "../../lib/units";

// Story 33.5: liquidations as lightweight-charts 5 series markers on the candle series, one circle per
// liquidation at its bankruptcy price, merged per bar and side above a named count. Pure: the caller
// hands the rows, the candle slots, the bar spacing and the two colours.

/** More liquidations than this on one side of one bar are drawn as one merged marker. */
export const MARKER_MERGE_COUNT = 3;
/** At or below this bar spacing (px) no marker is drawn: the legend says to zoom in. */
export const MARKER_MIN_BAR_SPACING_PX = 6;
/** The circle radius (px) of the smallest and of the largest notional on the chart (sqrt scale). */
export const MARKER_MIN_RADIUS_PX = 5;
export const MARKER_MAX_RADIUS_PX = 18;

/** One liquidation as the markers read it: a `/liquidations` item or a live frame's row. */
export interface LiquidationRow {
  side: string;
  size_units: number;
  price_units: number;
  price_precision: number;
  size_precision: number;
  venue_event_id: string;
  ts_event: number;
  /** The server's size x bankruptcy price (`Liquidation.notional_units()`), never computed here. */
  notional_units: number;
  notional_precision: number;
  price_kind?: string;
}

/** A marker plus the lines its hover tooltip shows. */
export type MarkerSpec = SeriesMarker<Time> & { id: string; tooltip: readonly string[] };

export interface MarkerColors {
  /** Short liquidations (forced buys): `--chart-up`. */
  up: string;
  /** Long liquidations (forced sells): `--chart-down`. */
  down: string;
}

/** The radius (px) of `notional` on a sqrt scale from `MARKER_MIN_RADIUS_PX` (0) to `MARKER_MAX_RADIUS_PX`
 * (`maxNotional`): the area grows with the notional. A non-positive maximum is the minimum radius. */
export function sqrtRadius(notional: number, maxNotional: number): number {
  if (!(maxNotional > 0) || !(notional > 0)) return MARKER_MIN_RADIUS_PX;
  const share = Math.min(1, notional / maxNotional);
  return MARKER_MIN_RADIUS_PX + (MARKER_MAX_RADIUS_PX - MARKER_MIN_RADIUS_PX) * Math.sqrt(share);
}

function ceiledEven(x: number): number {
  const ceiled = Math.ceil(x);
  return ceiled % 2 !== 0 ? ceiled - 1 : ceiled;
}

function ceiledOdd(x: number): number {
  const ceiled = Math.ceil(x);
  return ceiled % 2 === 0 ? ceiled - 1 : ceiled;
}

/**
 * The marker `size` multiplier that draws a circle of `radiusPx` at `barSpacing`. lightweight-charts
 * sizes a marker as a multiple of a bar-spacing-derived height (`calculateShapeHeight`, the bar
 * spacing clamped to 12..30 px), and draws a circle 0.8 of that.
 *
 * Known limit: this mirrors lightweight-charts 5.2.1's internals (`size`, `calculateShapeHeight` and
 * `shapeSize` in `dist/lightweight-charts.development.mjs`), not a public API; the circle's floor of
 * 12 x 0.8 px is the library's own. Re-check this inversion whenever the pinned version moves.
 * Upgrade path: a library option taking a size in px.
 */
export function markerSize(radiusPx: number, barSpacing: number): number {
  const height = ceiledEven(ceiledOdd(Math.min(Math.max(barSpacing, 12), 30)));
  return (2 * radiusPx) / (0.8 * height);
}

interface Group {
  slot: number;
  side: "long" | "short";
  rows: LiquidationRow[];
}

/** Rows by (bar slot, side), in slot order; a row outside every slot (before the first, or past the
 * last slot's end) is left out: it draws once its bar exists. */
let unknownSideLogged = false;

function groupBySlot(rows: readonly LiquidationRow[], slots: readonly number[], barSeconds: number): Group[] {
  const groups = new Map<string, Group>();
  for (const row of rows) {
    const slot = eventSlot(row.ts_event, slots, barSeconds);
    if (slot === null) continue;
    const side = row.side;
    if (side !== "long" && side !== "short") {
      // The route serves only `long`/`short` (`kernel.liquidation`); anything else is a contract break
      // to fix at its producer, never drawn as either side. Loud (ErrorBar), once per page.
      if (!unknownSideLogged) console.error(`LiquidationMarkers: liquidation ${row.venue_event_id} has an unknown side`, side);
      unknownSideLogged = true;
      continue;
    }
    const key = `${slot}:${side}`;
    const group = groups.get(key) ?? { slot, side, rows: [] };
    group.rows.push(row);
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => a.slot - b.slot || (a.side < b.side ? -1 : 1));
}

/**
 * A merged group's notional sum, the server's: the `/liquidation-bars` row of its bar, that side's
 * `long_notional_units`/`short_notional_units` at the row's `notional_precision`. Null (`Σ —`, the
 * minimum radius) when the row is missing or the side's notional is null.
 *
 * Known limit: the bar route sums the archived rows only, and serves null when the archive and the
 * candle store disagree (the live-edge lag, audit D-172) or the bucket straddles the feed start, so a
 * merged marker of the forming or a just-closed bar reads `Σ —` until the archive holds its rows;
 * live rows are never summed here (no browser-side notional arithmetic). Upgrade path: the live
 * `liquidations:` channel carries the server's running per-bar, per-side notional.
 */
function groupNotional(group: Group, bars: ReadonlyMap<number, LiquidationBarItem>): { units: number; precision: number } | null {
  const bar = bars.get(group.slot * 1000);
  const units = group.side === "long" ? bar?.long_notional_units : bar?.short_notional_units;
  const precision = bar?.notional_precision;
  if (units === null || units === undefined || precision === null || precision === undefined) return null;
  return { units, precision };
}

function rowTooltip(row: LiquidationRow): string[] {
  return [
    `${row.side} liquidated`,
    `size ${safeText(() => formatUnits(row.size_units, row.size_precision))}`,
    `price ${safeText(() => formatUnits(row.price_units, row.price_precision))} (${row.price_kind ?? "bankruptcy"})`,
    `notional ${safeText(() => formatUnits(row.notional_units, row.notional_precision))}`,
  ];
}

/** A value for the plot (a price, a notional for the radius), or null when it cannot be read exactly
 * (past 2^53, a bad precision): such a row draws no marker, logged once, never a wrong one. */
function plotNumber(units: number, precision: number): number | null {
  let value: number | null = null;
  safeText(() => {
    value = unitsToNumber(units, precision);
    return "";
  });
  return value;
}

interface Draft {
  spec: Omit<MarkerSpec, "size">;
  notional: number;
}

function draftsOf(group: Group, colors: MarkerColors, bars: ReadonlyMap<number, LiquidationBarItem>): Draft[] {
  const color = group.side === "long" ? colors.down : colors.up;
  const time = group.slot as UTCTimestamp;
  if (group.rows.length > MARKER_MERGE_COUNT) {
    const notional = groupNotional(group, bars);
    const sum = notional === null ? "—" : safeText(() => formatUnits(notional.units, notional.precision));
    const count = group.rows.length;
    const spec = {
      id: `liq-merged:${group.slot}:${group.side}`,
      time,
      shape: "circle" as const,
      color,
      position: group.side === "long" ? ("belowBar" as const) : ("aboveBar" as const),
      text: `Σ ${sum} · ${count}`,
      tooltip: [`${count} ${group.side} liquidations`, `notional Σ ${sum}`, "price kind bankruptcy"],
    };
    // An unknown sum draws at the minimum radius: no magnitude is invented.
    return [{ spec, notional: notional === null ? 0 : (plotNumber(notional.units, notional.precision) ?? 0) }];
  }
  return group.rows.flatMap((row): Draft[] => {
    const price = plotNumber(row.price_units, row.price_precision);
    if (price === null) return [];
    return [
      {
        spec: {
          id: `liq:${row.venue_event_id}`,
          time,
          shape: "circle" as const,
          color,
          position: row.side === "long" ? ("atPriceBottom" as const) : ("atPriceTop" as const),
          price,
          tooltip: rowTooltip(row),
        },
        notional: plotNumber(row.notional_units, row.notional_precision) ?? 0,
      },
    ];
  });
}

/**
 * The markers of `rows` on the candle slots `candleTimes` (ascending chart seconds), none at a bar
 * spacing at or below `MARKER_MIN_BAR_SPACING_PX`. Per bar and side: up to `MARKER_MERGE_COUNT`
 * liquidations each get a circle at the decoded bankruptcy price (`atPriceBottom` for a long, below
 * the price; `atPriceTop` for a short), more are one merged marker (`belowBar` / `aboveBar`) reading
 * `Σ <notional> · <count>`, the sum taken from the bar's server row in `bars` (`t` ms -> row; `Σ —`
 * when unknown). The radius is `sqrtRadius` of the notional against the largest drawn.
 * Sorted by time with stable ids (the venue event id; slot and side for a merged one).
 */
export function buildLiquidationMarkers(
  rows: readonly LiquidationRow[],
  candleTimes: readonly number[],
  barSeconds: number,
  barSpacing: number,
  colors: MarkerColors,
  bars: ReadonlyMap<number, LiquidationBarItem> = new Map(),
): MarkerSpec[] {
  if (barSpacing <= MARKER_MIN_BAR_SPACING_PX || rows.length === 0) return [];
  const drafts = groupBySlot(rows, candleTimes, barSeconds).flatMap((group) => draftsOf(group, colors, bars));
  const max = Math.max(0, ...drafts.map((d) => d.notional));
  return drafts.map(({ spec, notional }) => ({ ...spec, size: markerSize(sqrtRadius(notional, max), barSpacing) }) as MarkerSpec);
}
