import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import type { VwapPoint } from "../../../lib/anchoredVwap";
import type { AnchoredVwapDrawing } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  BODY_TOLERANCE_PX,
  type DrawingHit,
  type DrawingPrimitive,
  HANDLE_SIZE_PX,
  distanceToSegment,
  nearestHandle,
} from "./drawingPrimitive";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

interface Screen {
  x: number;
  vwap: number;
  upper1: number;
  lower1: number;
  upper2: number;
  lower2: number;
}

const VWAP_LINE_PX = 2;
const BAND_ALPHA = 0.7;
const OUTER_BAND_ALPHA = 0.45;

type Line = "vwap" | "upper1" | "lower1" | "upper2" | "lower2";

/**
 * An Anchored VWAP (Story 32.7) as a series primitive: the line from `points` (`lib/anchoredVwap.ts`
 * computed them, with no point for a bar without volume) in the drawing's colour, and with `bands`
 * the ±1σ lines solid and the ±2σ lines dashed in `band_color`. Screen positions are recomputed from
 * time and price on every `updateAllViews`, like every drawing. A point with no coordinate (off the
 * scrolled range) breaks the path rather than being guessed. The anchor handle (`"anchor"`) sits on
 * the first point of the line.
 */
export class AnchoredVwapPrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private screenPoints: (Screen | null)[] = [];
  private handlesVisible = false;
  private drawing: AnchoredVwapDrawing;
  private points: readonly VwapPoint[];
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(drawing: AnchoredVwapDrawing, points: readonly VwapPoint[]) {
    this.drawing = drawing;
    this.points = points;
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
    this.screenPoints = [];
  }

  update(drawing: AnchoredVwapDrawing, points: readonly VwapPoint[]): void {
    if (drawing === this.drawing && points === this.points) return;
    this.drawing = drawing;
    this.points = points;
    this.requestUpdate?.();
  }

  setHandlesVisible(visible: boolean): void {
    if (visible === this.handlesVisible) return;
    this.handlesVisible = visible;
    this.requestUpdate?.();
  }

  refresh(): void {
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const { chart, series } = this;
    if (!chart || !series) return;
    const timeScale = chart.timeScale();
    this.screenPoints = this.points.map((p): Screen | null => {
      const x = timeScale.timeToCoordinate(p.time as Time);
      const ys = [p.vwap, p.upper1, p.lower1, p.upper2, p.lower2].map((v) => series.priceToCoordinate(v));
      if (x === null || ys.some((y) => y === null)) return null;
      const [vwap, upper1, lower1, upper2, lower2] = ys as number[];
      return { x, vwap, upper1, lower1, upper2, lower2 };
    });
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  private firstPoint(): Screen | null {
    return this.screenPoints.find((p) => p !== null) ?? null;
  }

  /** The anchor handle within the grab radius, else the VWAP line within its tolerance. */
  hit(x: number, y: number): DrawingHit | null {
    const first = this.firstPoint();
    if (!first) return null;
    const handle = nearestHandle([{ id: "anchor", x: first.x, y: first.vwap }], x, y);
    if (handle) return handle;
    let best: number | null = null;
    for (let i = 1; i < this.screenPoints.length; i++) {
      const a = this.screenPoints[i - 1];
      const b = this.screenPoints[i];
      if (!a || !b) continue;
      const d = distanceToSegment(x, y, a.x, a.vwap, b.x, b.vwap);
      if (best === null || d < best) best = d;
    }
    return best !== null && best <= BODY_TOLERANCE_PX ? { handle: null, distance: best } : null;
  }

  /** The current screen points (null where a point has no coordinate) -- exposed for tests. */
  screen(): readonly (Screen | null)[] {
    return this.screenPoints;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const points = this.screenPoints;
    if (points.every((p) => p === null)) return null;
    const { bands, color, band_color: bandColor } = this.drawing;
    const lineColor = color ?? chartVar("--chart-drawing");
    const first = this.firstPoint();
    const handles = this.handlesVisible;
    const bg = chartVar("--chart-bg");
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const stroke = (line: Line, strokeColor: string, widthPx: number, dash: number[], alpha: number): void => {
            context.save();
            context.globalAlpha = alpha;
            context.strokeStyle = strokeColor;
            context.lineWidth = widthPx * hr;
            context.setLineDash(dash.map((d) => d * hr));
            context.beginPath();
            let open = false;
            for (const p of points) {
              if (!p) {
                open = false;
                continue;
              }
              if (open) context.lineTo(p.x * hr, p[line] * vr);
              else context.moveTo(p.x * hr, p[line] * vr);
              open = true;
            }
            context.stroke();
            context.restore();
          };
          if (bands) {
            stroke("upper2", bandColor, 1, [4, 3], OUTER_BAND_ALPHA);
            stroke("lower2", bandColor, 1, [4, 3], OUTER_BAND_ALPHA);
            stroke("upper1", bandColor, 1, [], BAND_ALPHA);
            stroke("lower1", bandColor, 1, [], BAND_ALPHA);
          }
          stroke("vwap", lineColor, VWAP_LINE_PX, [], 1);
          if (!handles || !first) return;
          const half = (HANDLE_SIZE_PX / 2) * hr;
          context.fillStyle = bg;
          context.strokeStyle = lineColor;
          context.lineWidth = hr;
          context.fillRect(first.x * hr - half, first.vwap * vr - half, half * 2, half * 2);
          context.strokeRect(first.x * hr - half, first.vwap * vr - half, half * 2, half * 2);
        });
      },
    };
  }
}
