import type { Time } from "lightweight-charts";

import type { FundingItem, LiquidationBarItem, MarkIndexItem, OpenInterestItem } from "../../api/schema";
import { DERIVATIVE_LABELS, type DerivativeEntry, type LiquidationMeasure, type LiquidationsEntry } from "../../lib/chartLayout";
import { type FundingBar, type SeriesPoint, bucketPoints, fromTime, fundingPoints, mirroredLiquidations } from "../../lib/derivativeSeries";
import type { OutputStyle } from "../../lib/indicatorStyle";
import {
  formatCountdown,
  formatDecimal,
  formatDecimalText,
  formatPercent,
  formatUnits,
  shiftDecimalText,
} from "../../lib/units";
import { type ChartToken, chartVar } from "./chartTheme";
import type { IndicatorPaneSpec } from "./LightweightChart";

// Story 33.5: the Derivatives group's pane specs. Every value printed is the server's, formatted only
// (`lib/units.ts`): exact text through `formatDecimalText`, integer units through `formatUnits`, the
// server's floats (bps, annualised) through `formatDecimal`/`formatPercent`. A value the server did not
// send reads "—". Each series' `data` is built apart (`*Data`), so the page memoises it by its own
// inputs and a legend-only change (the funding countdown) never hands the chart a new array.

/** The pane group ids, which are also the persisted pane-height keys. */
export const DERIVATIVE_PANE_IDS = ["deriv_oi", "deriv_funding", "deriv_basis", "deriv_liquidations"] as const;
/** The Mark / Index overlays' legend group (on the price pane: no pane height). */
export const MARK_INDEX_GROUP = "deriv_mark_index";

/** The chart token each output draws with while its entry stores no colour: the panes and the
 * settings dialog's seeds read this one table. A histogram output has up and down tokens. */
export const DERIVATIVE_OUTPUT_TOKENS: Record<string, { color: ChartToken; up: ChartToken; down: ChartToken }> = {
  oi: { color: "--chart-pane-1", up: "--chart-up", down: "--chart-down" },
  rate: { color: "--chart-text-dim", up: "--chart-up", down: "--chart-down" },
  mark_index: { color: "--chart-pane-3", up: "--chart-up", down: "--chart-down" },
  mark_last: { color: "--chart-pane-4", up: "--chart-up", down: "--chart-down" },
  mark: { color: "--chart-pane-5", up: "--chart-up", down: "--chart-down" },
  index: { color: "--chart-pane-6", up: "--chart-up", down: "--chart-down" },
  liquidations: { color: "--chart-text-dim", up: "--chart-up", down: "--chart-down" },
};

const NONE = "—";

let formatFailureLogged = false;

/**
 * `format()`'s text, or `—` when the value cannot be printed exactly: an integer past 2^53 (the JSON
 * range, audit D-170), a precision past 16 or text that is not decimal make `lib/units.ts` throw a
 * `RangeError`, which must never crash the chart from a legend, a render or a memo. Logged once
 * (`console.error`, the ErrorBar), never rounded into a wrong number.
 */
export function safeText(format: () => string): string {
  try {
    return format();
  } catch (err) {
    if (!formatFailureLogged) console.error("derivatives: a value could not be printed exactly, shown as —", err);
    formatFailureLogged = true;
    return NONE;
  }
}

/** What every spec shares: where its data stops (the first candle, a replay cutoff) and its state. */
export interface SpecContext {
  /** The oldest candle slot (chart seconds); derivative rows older than it wait for the candles. */
  firstCandle: number | null;
  /** The replay cutoff bar (chart seconds), or null. */
  cutoff: number | null;
  /** The route's load failure ("load failed"), shown in place of the value. */
  error: string | null;
}

/** Points from the first candle on and up to a replay's cutoff. */
export function cutPoints(points: SeriesPoint[], firstCandle: number | null, cutoff: number | null): SeriesPoint[] {
  const shown = fromTime(points, firstCandle);
  return cutoff === null ? shown : shown.filter((p) => (p.time as number) <= cutoff);
}

function byTime<T extends { t: number }>(rows: readonly T[]): Map<number, T> {
  return new Map(rows.map((row) => [row.t / 1000, row] as const));
}

function token(style: OutputStyle | undefined, key: "color" | "up_color" | "down_color", output: string): string {
  const value = style?.[key];
  if (typeof value === "string") return value;
  const tokens = DERIVATIVE_OUTPUT_TOKENS[output];
  return chartVar(key === "color" ? tokens.color : key === "up_color" ? tokens.up : tokens.down);
}

const text = (value: string | null | undefined): string =>
  value === null || value === undefined ? NONE : safeText(() => formatDecimalText(value));

/** Open interest: one line, legend `<oi> · Δ <oi_change>` at the slot, both exact text. */
export function openInterestSpec(
  rows: readonly OpenInterestItem[],
  data: SeriesPoint[],
  entry: DerivativeEntry,
  ctx: SpecContext,
): IndicatorPaneSpec {
  const at = byTime(rows);
  const style = entry.style?.oi;
  return {
    id: "deriv_oi.oi",
    group: "deriv_oi",
    groupLabel: DERIVATIVE_LABELS.oi,
    outputLabel: "oi",
    kind: "Line",
    data,
    color: token(style, "color", "oi"),
    lineWidth: style?.line_width,
    lineStyle: style?.line_style,
    format: (_value, time) => {
      const row = time === null ? undefined : at.get(time);
      return `${text(row?.oi)} · Δ ${text(row?.oi_change)}`;
    },
    text: ctx.error ?? undefined,
  };
}

/** A funding rate (exact text) as a percent at 4 decimals: "0.0001" -> "0.0100%"; `—` if not printable. */
export function ratePercent(rate: string): string {
  return safeText(() => `${formatDecimalText(shiftDecimalText(rate, 2), 4)}%`);
}

/** The countdown to the latest event's next funding time, against the client clock (`nowMs`); `—`
 * without one, and under a replay (`nowMs` null: the wall clock says nothing about a past event). */
export function fundingCountdown(latest: FundingItem | undefined, nowMs: number | null): string {
  const next = latest?.next_funding_ns;
  if (next === null || next === undefined || nowMs === null) return NONE;
  return formatCountdown(next / 1_000_000 - nowMs);
}

/** The funding bars as the pane plots them. */
export function fundingData(bars: readonly FundingBar[], firstCandle: number | null, cutoff: number | null): SeriesPoint[] {
  return cutPoints(fundingPoints(bars), firstCandle, cutoff);
}

/** Funding: the held rate per bar, up/down coloured; legend rate %, `ann.` and the countdown. */
export function fundingSpec(
  bars: readonly FundingBar[],
  data: SeriesPoint[],
  countdown: string,
  entry: DerivativeEntry,
  ctx: SpecContext,
): IndicatorPaneSpec {
  const at = new Map(bars.map((bar) => [bar.time, bar.event] as const));
  const style = entry.style?.rate;
  return {
    id: "deriv_funding.rate",
    group: "deriv_funding",
    groupLabel: DERIVATIVE_LABELS.funding,
    outputLabel: "rate",
    kind: "Histogram",
    data,
    color: token(undefined, "color", "rate"),
    upColor: token(style, "up_color", "rate"),
    downColor: token(style, "down_color", "rate"),
    format: (_value, time) => {
      const event = time === null ? null : (at.get(time) ?? null);
      const annualised = event?.annualised;
      const ann = annualised === null || annualised === undefined ? NONE : safeText(() => `${formatPercent(annualised, 2)}%`);
      return `rate ${event ? ratePercent(event.rate) : NONE} · ann. ${ann} · next ${countdown}`;
    },
    text: ctx.error ?? undefined,
  };
}

const bps = (value: number): string => safeText(() => `${formatDecimal(value, 2)} bps`);

const BASIS_OUTPUTS = [
  { output: "mark_index", field: "basis_mi_bps", label: "mark−index" },
  { output: "mark_last", field: "basis_ml_bps", label: "mark−last" },
] as const;

/** The basis lines' points: mark−index and mark−last. */
export function basisData(rows: readonly MarkIndexItem[], firstCandle: number | null, cutoff: number | null): SeriesPoint[][] {
  return BASIS_OUTPUTS.map(({ field }) => cutPoints(bucketPoints(rows, field), firstCandle, cutoff));
}

/** Basis: mark−index and mark−last in bps (the server's floats), against a dashed zero line. */
export function basisSpecs(data: readonly SeriesPoint[][], entry: DerivativeEntry, ctx: SpecContext): IndicatorPaneSpec[] {
  return BASIS_OUTPUTS.map(({ output, label }, i) => {
    const style = entry.style?.[output];
    return {
      id: `deriv_basis.${output}`,
      group: "deriv_basis",
      groupLabel: DERIVATIVE_LABELS.basis,
      outputLabel: label,
      kind: "Line" as const,
      data: data[i],
      color: token(style, "color", output),
      lineWidth: style?.line_width,
      lineStyle: style?.line_style,
      format: bps,
      text: ctx.error ?? undefined,
      zeroLine: i === 0,
    };
  });
}

const MARK_INDEX_OUTPUTS = ["mark", "index"] as const;

/** The mark and index overlays' points. */
export function markIndexData(rows: readonly MarkIndexItem[], firstCandle: number | null, cutoff: number | null): SeriesPoint[][] {
  return MARK_INDEX_OUTPUTS.map((output) => cutPoints(bucketPoints(rows, output), firstCandle, cutoff));
}

/** Mark and index as two overlays on the price pane, each legend value the exact text at the slot. */
export function markIndexSpecs(
  rows: readonly MarkIndexItem[],
  data: readonly SeriesPoint[][],
  entry: DerivativeEntry,
  ctx: SpecContext,
): IndicatorPaneSpec[] {
  const at = byTime(rows);
  return MARK_INDEX_OUTPUTS.map((output, i) => {
    const style = entry.style?.[output];
    return {
      id: `deriv_mark_index.${output}`,
      group: MARK_INDEX_GROUP,
      groupLabel: DERIVATIVE_LABELS.mark_index,
      outputLabel: output,
      kind: "Line" as const,
      placement: "overlay" as const,
      data: data[i],
      color: token(style, "color", output),
      lineWidth: style?.line_width,
      lineStyle: style?.line_style,
      format: (_value: number, time: number | null) => text(time === null ? undefined : at.get(time)?.[output]),
      text: ctx.error ?? undefined,
    };
  });
}

const units = (value: number | null | undefined, precision: number | null | undefined): string =>
  value === null || value === undefined || precision === null || precision === undefined
    ? NONE
    : safeText(() => formatUnits(value, precision));

function sideText(row: LiquidationBarItem | undefined, side: "long" | "short", measure: LiquidationMeasure): string {
  if (row === undefined) return NONE;
  return measure === "size"
    ? units(side === "long" ? row.long_v : row.short_v, row.size_precision)
    : units(side === "long" ? row.long_notional_units : row.short_notional_units, row.notional_precision);
}

function totalText(row: LiquidationBarItem | undefined): string {
  const count = row?.n;
  return `notional ${units(row?.notional_units, row?.notional_precision)} · n ${count === null || count === undefined ? NONE : count}`;
}

/** The liquidation bars' points, long (below 0) then short; a value that cannot be plotted exactly is
 * whitespace (`mirroredLiquidations`, logged once), never a wrong bar. */
export function liquidationData(
  rows: readonly LiquidationBarItem[],
  measure: LiquidationMeasure,
  firstCandle: number | null,
  cutoff: number | null,
): SeriesPoint[][] {
  const mirrored = mirroredLiquidations(rows, measure);
  return [cutPoints(mirrored.long, firstCandle, cutoff), cutPoints(mirrored.short, firstCandle, cutoff)];
}

/**
 * Liquidations: mirrored histograms (long liquidations below 0 in the down colour, shorts above in the
 * up colour) by size or by the server's per-side notional; legend `long <x>`, `short <x>`, the bar's
 * total notional and `n`. `notes` (markers capped, markers hidden) join the title.
 */
export function liquidationSpecs(
  rows: readonly LiquidationBarItem[],
  data: readonly SeriesPoint[][],
  entry: LiquidationsEntry,
  ctx: SpecContext,
  notes: readonly string[],
): IndicatorPaneSpec[] {
  const at = byTime(rows);
  const style = entry.style?.liquidations;
  const title = [`${DERIVATIVE_LABELS.liquidations} (${entry.measure})`, ...notes].join(" · ");
  const sides = [
    { side: "long", color: token(style, "down_color", "liquidations") },
    { side: "short", color: token(style, "up_color", "liquidations") },
  ] as const;
  return sides.map(({ side, color }, i) => ({
    id: `deriv_liquidations.${side}`,
    group: "deriv_liquidations",
    groupLabel: title,
    outputLabel: side,
    kind: "Histogram" as const,
    data: data[i],
    color,
    format: (_value: number, time: number | null) => {
      const row = time === null ? undefined : at.get(time);
      const own = `${side} ${sideText(row, side, entry.measure)}`;
      return side === "short" ? `${own} · ${totalText(row)}` : own;
    },
    text: ctx.error ?? undefined,
  }));
}

/** The candle slots (chart seconds) of the displayed bars plus the forming bar, and the gap slots. */
export function candleSlots(
  bars: readonly { time: Time }[],
  liveTime: number | null,
): { times: number[]; gaps: Set<number> } {
  const times: number[] = [];
  const gaps = new Set<number>();
  for (const bar of bars) {
    const time = bar.time as number;
    times.push(time);
    if (!("open" in bar)) gaps.add(time);
  }
  if (liveTime !== null && (times.length === 0 || liveTime > times[times.length - 1])) times.push(liveTime);
  return { times, gaps };
}
