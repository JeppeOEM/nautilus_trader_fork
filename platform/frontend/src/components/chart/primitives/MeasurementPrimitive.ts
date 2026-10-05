import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { ChartDatum, VolumeDatum } from "../../../hooks/useCandles";
import type { TrendlineAnchor } from "./TrendlinePrimitive";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export interface Measurement {
  priceDelta: number;
  /** `null` when the start price is 0 (the percent is undefined, never Infinity). */
  priceDeltaPct: number | null;
  bars: number;
  volume: number;
}

/**
 * DW-144: the candle/volume arrays as sorted times + volume prefix sums, built once per data or
 * volume change (the forming bar is folded in per measurement) so a measurement per mouse-move is
 * two binary searches, not a scan of the whole history. Whitespace/gap entries (no OHLC / no `value`) are left out, so a gap
 * never counts as a bar or contributes volume. Times are UTC seconds (useCandles), ascending as
 * the chart's own series require.
 */
export interface MeasurementIndex {
  barTimes: number[];
  volumeTimes: number[];
  /**
   * `volumePrefix[i] + volumeCompensation[i]` = the volume of the first `i` entries of
   * `volumeTimes` (both length + 1). The compensation carries the low-order bits each running
   * total rounded away (Neumaier), so a short range's difference is not the whole history's
   * rounding error.
   */
  volumePrefix: number[];
  volumeCompensation: number[];
  /** The arrays' newest times, gap slots included: a forming bar counts only when newer. */
  lastCandleTime: number | null;
  lastVolumeTime: number | null;
}

/** The forming bar the chart paints with `update()`: counted only while newer than the arrays. */
export interface FormingBar {
  time: Time;
  volume: number;
}

const lastTime = (entries: readonly { time: Time }[]): number | null =>
  entries.length === 0 ? null : (entries[entries.length - 1].time as number);

// DW-144: built once per history change (never per live tick, never per mouse-move), so a
// measurement drag reads it in O(log n). The forming bar is folded in by `computeMeasurement`.
export function buildMeasurementIndex(candles: readonly ChartDatum[], volume: readonly VolumeDatum[]): MeasurementIndex {
  const barTimes: number[] = [];
  for (const c of candles) if ("open" in c) barTimes.push(c.time as number);
  const volumeTimes: number[] = [];
  const volumePrefix: number[] = [0];
  const volumeCompensation: number[] = [0];
  let sum = 0;
  let compensation = 0;
  for (const v of volume) {
    if (!("value" in v)) continue;
    volumeTimes.push(v.time as number);
    const next = sum + v.value;
    compensation += Math.abs(sum) >= Math.abs(v.value) ? sum - next + v.value : v.value - next + sum;
    sum = next;
    volumePrefix.push(sum);
    volumeCompensation.push(compensation);
  }
  return {
    barTimes,
    volumeTimes,
    volumePrefix,
    volumeCompensation,
    lastCandleTime: lastTime(candles),
    lastVolumeTime: lastTime(volume),
  };
}

// A live bar at (or before) the arrays' last time is the same bar already promoted into them:
// counting it again would double the newest bar and its volume.
const isNewer = (time: number, last: number | null): boolean => last === null || time > last;

/** The first index whose time is >= `time` (`strict`: > `time`). */
function bound(times: readonly number[], time: number, strict: boolean): number {
  let lo = 0;
  let hi = times.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (times[mid] < time || (strict && times[mid] === time)) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

// Story 18.3 (AC #2/#3): everything is derived from the candle/volume arrays the chart
// already holds -- no query.
//
// The volume is a difference of two compensated running totals: its error is relative to the
// range's own volume, not to the whole history's, so a short range at the end of a long history
// prints the same digits a direct sum of its bars would.
export function computeMeasurement(
  start: TrendlineAnchor,
  end: TrendlineAnchor,
  index: MeasurementIndex,
  liveBar?: FormingBar | null,
): Measurement {
  const from = Math.min(start.time as number, end.time as number);
  const to = Math.max(start.time as number, end.time as number);
  const priceDelta = end.price - start.price;
  let bars = bound(index.barTimes, to, true) - bound(index.barTimes, from, false);
  const first = bound(index.volumeTimes, from, false);
  const afterLast = bound(index.volumeTimes, to, true);
  let volume =
    index.volumePrefix[afterLast] -
    index.volumePrefix[first] +
    (index.volumeCompensation[afterLast] - index.volumeCompensation[first]);
  const liveTime = liveBar ? (liveBar.time as number) : Number.NaN;
  if (liveBar && liveTime >= from && liveTime <= to) {
    if (isNewer(liveTime, index.lastCandleTime)) bars++;
    if (isNewer(liveTime, index.lastVolumeTime)) volume += liveBar.volume;
  }
  return {
    priceDelta,
    priceDeltaPct: start.price === 0 ? null : (priceDelta / start.price) * 100,
    bars,
    volume,
  };
}

// Significant digits, not fixed decimals: dYdX lists sub-cent instruments where
// toFixed(2) would print every delta as "0.00".
const fmt = (n: number): string => n.toLocaleString("en-US", { maximumSignificantDigits: 6 });

export function formatMeasurement(m: Measurement): string[] {
  const sign = m.priceDelta > 0 ? "+" : "";
  const pct = m.priceDeltaPct === null ? "n/a" : `${sign}${m.priceDeltaPct.toFixed(2)}%`;
  return [`${sign}${fmt(m.priceDelta)} (${pct})`, `${m.bars} bars`, `vol ${fmt(m.volume)}`];
}

export interface ScreenRect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const FONT_PX = 12;
const LINE_PX = FONT_PX + 2;
const LABEL_PAD_PX = 4;

const clampAxis = (origin: number, size: number, pane: number): number =>
  Math.max(0, Math.min(Math.max(origin, LABEL_PAD_PX), pane - size - LABEL_PAD_PX));

/**
 * DW-144: the label's top-left (media px) -- inside the rectangle's top-left corner, pushed back
 * inside the pane when the rectangle reaches its right/bottom edge (or starts off its left/top),
 * so the readout is never cut off. A pane smaller than the text pins it to the top-left.
 */
export function labelOrigin(
  rect: ScreenRect,
  textWidth: number,
  textHeight: number,
  paneWidth: number,
  paneHeight: number,
): { x: number; y: number } {
  return {
    x: clampAxis(Math.min(rect.x1, rect.x2) + LABEL_PAD_PX, textWidth, paneWidth),
    y: clampAxis(Math.min(rect.y1, rect.y2) + LABEL_PAD_PX, textHeight, paneHeight),
  };
}

// Story 18.3 (AC #4): the transient measurement rectangle + label. All drag state lives
// here (no React re-render per mouse-move); `setSelection` -> `requestUpdate` is the only
// path that repaints.
export class MeasurementPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private start: TrendlineAnchor | null = null;
  private end: TrendlineAnchor | null = null;
  private lines: string[] = [];
  private rect: ScreenRect | null = null;
  private readonly color: string;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(color: string) {
    this.color = color;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<"Candlestick" | "Line">;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.rect = null;
  }

  setSelection(start: TrendlineAnchor, end: TrendlineAnchor, lines: string[]): void {
    this.start = start;
    this.end = end;
    this.lines = lines;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const { chart, series, start, end } = this;
    if (!chart || !series || !start || !end) return;
    const timeScale = chart.timeScale();
    const x1 = timeScale.timeToCoordinate(start.time);
    const x2 = timeScale.timeToCoordinate(end.time);
    const y1 = series.priceToCoordinate(start.price);
    const y2 = series.priceToCoordinate(end.price);
    this.rect = x1 === null || x2 === null || y1 === null || y2 === null ? null : { x1, y1, x2, y2 };
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  screenRect(): ScreenRect | null {
    return this.rect;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const { rect, lines, color } = this;
    if (!rect) return null;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, mediaSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const x = Math.min(rect.x1, rect.x2) * hr;
          const y = Math.min(rect.y1, rect.y2) * vr;
          const w = Math.abs(rect.x2 - rect.x1) * hr;
          const h = Math.abs(rect.y2 - rect.y1) * vr;
          context.globalAlpha = 0.15;
          context.fillStyle = color;
          context.fillRect(x, y, w, h);
          context.globalAlpha = 1;
          context.strokeStyle = color;
          context.lineWidth = hr;
          context.strokeRect(x, y, w, h);
          context.font = `${FONT_PX * vr}px monospace`;
          context.fillStyle = color;
          context.textBaseline = "top";
          const textWidth = Math.max(0, ...lines.map((line) => context.measureText(line).width)) / hr;
          const textHeight = lines.length * LINE_PX - (LINE_PX - FONT_PX);
          const origin = labelOrigin(rect, textWidth, textHeight, mediaSize.width, mediaSize.height);
          lines.forEach((line, i) => context.fillText(line, origin.x * hr, (origin.y + i * LINE_PX) * vr));
        });
      },
    };
  }
}
