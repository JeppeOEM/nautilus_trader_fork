import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { chartVar } from "../chartTheme";
import {
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawingHit,
  type DrawingPrimitive,
  HANDLE_SIZE_PX,
  distanceToSegment,
  nearestHandle,
} from "./drawingPrimitive";

export interface TrendlineAnchor {
  time: Time;
  price: number;
}

/** An anchor as the primitive takes it: the time is UTC seconds, branded `Time` or plain. */
export interface TrendlineAnchorInput {
  time: Time | number;
  price: number;
}

interface ScreenPoint {
  x: number;
  y: number;
}

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

const LINE_WIDTH = 1;

// Story 18.2 (AC #2/#3): a two-anchor line drawn as a series primitive. The anchors stay
// in {time, price} space; screen coordinates are recomputed from them on every
// `updateAllViews` (the library calls it before each redraw -- pan, zoom, resize, price-scale
// change), so the line can never drift from its anchors.
export class TrendlinePrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private points: [ScreenPoint, ScreenPoint] | null = null;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  private anchors: [TrendlineAnchorInput, TrendlineAnchorInput];
  private color: string;
  private readonly grid: BarGrid;
  private handlesVisible = false;

  constructor(anchors: [TrendlineAnchorInput, TrendlineAnchorInput], color: string, grid: BarGrid = new BarGrid()) {
    this.anchors = anchors;
    this.color = color;
    this.grid = grid;
  }

  /** Handle squares are drawn only while the drawings are editable (the Cursor tool). */
  setHandlesVisible(visible: boolean): void {
    if (visible === this.handlesVisible) return;
    this.handlesVisible = visible;
    this.requestUpdate?.();
  }

  refresh(): void {
    this.requestUpdate?.();
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
    this.points = null;
  }

  update(anchors: [TrendlineAnchorInput, TrendlineAnchorInput], color: string): void {
    if (anchors === this.anchors && color === this.color) return;
    this.anchors = anchors;
    this.color = color;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const { chart, series } = this;
    if (!chart || !series) return;
    const timeScale = chart.timeScale();
    const [a, b] = this.anchors;
    // An anchor is drawn on the latest bar at or before it (a time between two bars of a coarser
    // timeframe, or in a gap); the stored anchor is never changed.
    const sa = this.grid.snap(a.time as number);
    const sb = this.grid.snap(b.time as number);
    const ax = sa === null ? null : timeScale.timeToCoordinate(sa as Time);
    const bx = sb === null ? null : timeScale.timeToCoordinate(sb as Time);
    const ay = series.priceToCoordinate(a.price);
    const by = series.priceToCoordinate(b.price);
    // An anchor off the scrolled-out time range has no coordinate; skip the draw rather
    // than fabricate a position for it.
    this.points =
      ax === null || bx === null || ay === null || by === null
        ? null
        : [
            { x: ax, y: ay },
            { x: bx, y: by },
          ];
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** Pixel distance from (x, y) to the drawn segment, or `null` when not drawable. */
  distanceTo(x: number, y: number): number | null {
    if (!this.points) return null;
    const [a, b] = this.points;
    return distanceToSegment(x, y, a.x, a.y, b.x, b.y);
  }

  /** The anchor handle (`a` / `b`) within the grab radius, else the line within its tolerance. */
  hit(x: number, y: number): DrawingHit | null {
    if (!this.points) return null;
    const [a, b] = this.points;
    const handle = nearestHandle(
      [
        { id: "a", ...a },
        { id: "b", ...b },
      ],
      x,
      y,
    );
    if (handle) return handle;
    const distance = this.distanceTo(x, y);
    return distance !== null && distance <= BODY_TOLERANCE_PX ? { handle: null, distance } : null;
  }

  /** The current screen-space endpoints, or `null` when not drawable -- exposed for tests. */
  screenPoints(): [ScreenPoint, ScreenPoint] | null {
    return this.points;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const points = this.points;
    if (!points) return null;
    const color = this.color;
    const handles = this.handlesVisible;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
          context.strokeStyle = color;
          context.lineWidth = LINE_WIDTH * horizontalPixelRatio;
          context.beginPath();
          context.moveTo(points[0].x * horizontalPixelRatio, points[0].y * verticalPixelRatio);
          context.lineTo(points[1].x * horizontalPixelRatio, points[1].y * verticalPixelRatio);
          context.stroke();
          if (!handles) return;
          const half = (HANDLE_SIZE_PX / 2) * horizontalPixelRatio;
          context.fillStyle = chartVar("--chart-bg");
          for (const p of points) {
            const x = p.x * horizontalPixelRatio - half;
            const y = p.y * verticalPixelRatio - half;
            context.fillRect(x, y, half * 2, half * 2);
            context.strokeRect(x, y, half * 2, half * 2);
          }
        });
      },
    };
  }
}
