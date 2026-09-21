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

// Story 18.3 (AC #2/#3): everything is derived from the candle/volume arrays the chart
// already holds -- no query. Whitespace/gap entries (no OHLC / no `value`) are skipped, so
// a gap never counts as a bar or contributes volume. Times are UTC seconds (useCandles).
export function computeMeasurement(
  start: TrendlineAnchor,
  end: TrendlineAnchor,
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
): Measurement {
  const from = Math.min(start.time as number, end.time as number);
  const to = Math.max(start.time as number, end.time as number);
  const inRange = (t: Time): boolean => (t as number) >= from && (t as number) <= to;
  const priceDelta = end.price - start.price;
  let bars = 0;
  for (const c of candles) if ("open" in c && inRange(c.time)) bars++;
  let sum = 0;
  for (const v of volume) if ("value" in v && inRange(v.time)) sum += v.value;
  return {
    priceDelta,
    priceDeltaPct: start.price === 0 ? null : (priceDelta / start.price) * 100,
    bars,
    volume: sum,
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

interface ScreenRect {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

const FONT_PX = 12;

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
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
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
          lines.forEach((line, i) => context.fillText(line, x + 4 * hr, y + (4 + i * (FONT_PX + 2)) * vr));
        });
      },
    };
  }
}
