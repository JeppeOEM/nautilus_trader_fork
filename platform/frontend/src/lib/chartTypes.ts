// Type-only: this module is imported by `chartLayout.ts`, which the app shell loads, and a value import
// would pull lightweight-charts into the entry chunk (the scale mode mapping lives in LightweightChart).
import type { CandlestickData, LineData, Time, WhitespaceData } from "lightweight-charts";

import type { ChartDatum } from "../hooks/useCandles";
import { heikinAshi, heikinAshiNext } from "./heikinAshi";

// Story 33.9: the main price series' chart type and the right price scale's mode. Both sets are
// mirrored by `views/preferences.py` (`CHART_TYPES`, `PRICE_SCALE_MODES`, `MAX_COMPARE_SYMBOLS`),
// pinned by `views/tests/test_chart_layouts.py`'s mirror tests.

export type ChartType = "candles" | "hollow" | "bars" | "line" | "area" | "baseline" | "heikin_ashi";
export type PriceScaleModeName = "normal" | "log" | "percent" | "indexed";

export const CHART_TYPES: readonly ChartType[] = ["candles", "hollow", "bars", "line", "area", "baseline", "heikin_ashi"];
export const PRICE_SCALE_MODES: readonly PriceScaleModeName[] = ["normal", "log", "percent", "indexed"];
export const MAX_COMPARE_SYMBOLS = 3;

/** The header's and the scale menu's name of each type and mode. */
export const CHART_TYPE_LABELS: Record<ChartType, string> = {
  candles: "Candles",
  hollow: "Hollow candles",
  bars: "Bars",
  line: "Line",
  area: "Area",
  baseline: "Baseline",
  heikin_ashi: "Heikin Ashi",
};
export const PRICE_SCALE_LABELS: Record<PriceScaleModeName, string> = {
  normal: "Normal",
  log: "Log",
  percent: "Percent",
  indexed: "Indexed to 100",
};

/** The lightweight-charts series definition each type is drawn with. */
export type MainSeriesKind = "Candlestick" | "Bar" | "Line" | "Area" | "Baseline";

export function seriesKindOf(type: ChartType): MainSeriesKind {
  switch (type) {
    case "candles":
    case "hollow":
    case "heikin_ashi":
      return "Candlestick";
    case "bars":
      return "Bar";
    case "line":
      return "Line";
    case "area":
      return "Area";
    case "baseline":
      return "Baseline";
  }
}

/** A row of the main series: OHLC (candles, bars, HA; hollow with per-bar colours), a close value
 * (line, area, baseline), or the slot's whitespace. */
export type MainRow = CandlestickData<Time> | LineData<Time> | WhitespaceData<Time>;

export interface UpDownColors {
  up: string;
  down: string;
}

type Ohlc = Pick<CandlestickData<Time>, "time" | "open" | "high" | "low" | "close">;

const isCandle = (d: ChartDatum | undefined): d is CandlestickData<Time> => d !== undefined && "open" in d;

/**
 * A hollow candle's colours: close >= open is a hollow body (transparent fill), close < open a filled
 * one; the colour is up when close >= the previous bar's close, else down. `prevClose` null (the first
 * bar, or the first after a gap) compares the close to the bar's own open.
 */
export function hollowColors(
  prevClose: number | null,
  bar: Ohlc,
  colors: UpDownColors,
): { color: string; borderColor: string; wickColor: string } {
  const tone = bar.close >= (prevClose ?? bar.open) ? colors.up : colors.down;
  return { color: bar.close >= bar.open ? "transparent" : tone, borderColor: tone, wickColor: tone };
}

const ohlcOf = (d: CandlestickData<Time>): CandlestickData<Time> => ({
  time: d.time,
  open: d.open,
  high: d.high,
  low: d.low,
  close: d.close,
});

/**
 * The main series' rows for `type`, slot for slot with `data` (whitespace kept): the only place a
 * derived value (HA, hollow colours, a close-only line) is made, and its only consumer is the main
 * series' `setData` (AD-F6). Every other reader keeps `data`.
 */
export function seriesRows(type: ChartType, data: readonly ChartDatum[], colors: UpDownColors): MainRow[] {
  switch (type) {
    case "candles":
    case "bars":
      return data.map((d) => (isCandle(d) ? ohlcOf(d) : d));
    case "heikin_ashi":
      return heikinAshi(data);
    case "hollow":
      return data.map((d, i) => {
        if (!isCandle(d)) return d;
        const prev = data[i - 1];
        return { ...ohlcOf(d), ...hollowColors(isCandle(prev) ? prev.close : null, d, colors) };
      });
    case "line":
    case "area":
    case "baseline":
      return data.map((d) => (isCandle(d) ? { time: d.time, value: d.close } : d));
  }
}

/** The index of the last slot of `data` strictly before `time`, or -1. */
function slotBefore(data: readonly { time: Time }[], time: number): number {
  for (let i = data.length - 1; i >= 0; i--) {
    if ((data[i].time as number) < time) return i;
  }
  return -1;
}

/**
 * The forming bar's main-series row: the slot before it decides what it chains to (HA's previous HA
 * bar, hollow's previous close), and a whitespace slot there restarts both, as in `seriesRows`.
 * `rows` is `seriesRows(type, data)`.
 */
export function liveSeriesRow(
  type: ChartType,
  data: readonly ChartDatum[],
  rows: readonly MainRow[],
  bar: Ohlc,
  colors: UpDownColors,
): MainRow {
  const before = slotBefore(data, bar.time as number);
  const prevReal = before >= 0 ? data[before] : undefined;
  switch (type) {
    case "candles":
    case "bars":
      return ohlcOf(bar as CandlestickData<Time>);
    case "heikin_ashi": {
      const prevHa = before >= 0 ? rows[before] : undefined;
      return heikinAshiNext(prevHa !== undefined && "open" in prevHa ? (prevHa as CandlestickData<Time>) : null, bar);
    }
    case "hollow":
      return { ...ohlcOf(bar as CandlestickData<Time>), ...hollowColors(isCandle(prevReal) ? prevReal.close : null, bar, colors) };
    case "line":
    case "area":
    case "baseline":
      return { time: bar.time, value: bar.close };
  }
}

/** The close of the first real bar at or after the time `from` (the visible range's left edge, the
 * Baseline's base value), or null when none is visible. By time, never by logical index: other series on
 * the shared time scale (a compare, an indicator) can add times the main data lacks, so the chart's
 * logical index is not an index into `data`. `data` is ascending by time (binary search). */
export function firstVisibleClose(data: readonly ChartDatum[], from: number): number | null {
  let lo = 0;
  let hi = data.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if ((data[mid].time as number) < from) lo = mid + 1;
    else hi = mid;
  }
  for (let i = lo; i < data.length; i++) {
    const d = data[i];
    if (isCandle(d)) return d.close;
  }
  return null;
}
