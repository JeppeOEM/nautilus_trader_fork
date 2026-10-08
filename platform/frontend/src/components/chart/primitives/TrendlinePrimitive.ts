import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { DEFAULT_DRAWING_LINE_WIDTH, type Extend, type LineStyleName, extendedSegment } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawingHit,
  type DrawingPrimitive,
  distanceToSegment,
  drawArrowHead,
  drawHandles,
  lineDash,
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

/** Story 33.10: how a line kind is drawn beyond its anchors. */
export interface LineOptions {
  /** Past B (a ray), past both anchors (an extended line), or not at all. */
  extend: Extend;
  /** An arrow head at B. */
  arrow: boolean;
  lineWidth: number;
  lineStyle: LineStyleName | undefined;
  /** No handles drawn (and no grab: the chart's hit test gives it its body only). */
  locked: boolean;
}

export const PLAIN_LINE: LineOptions = {
  extend: "none",
  arrow: false,
  lineWidth: DEFAULT_DRAWING_LINE_WIDTH,
  lineStyle: undefined,
  locked: false,
};

// Story 18.2 (AC #2/#3): a two-anchor line drawn as a series primitive. The anchors stay
// in {time, price} space; screen coordinates are recomputed from them on every
// `updateAllViews` (the library calls it before each redraw -- pan, zoom, resize, price-scale
// change), so the line can never drift from its anchors. Story 33.10: the one primitive of the
// trendline, ray, extended line and arrow (`LineOptions`), whose extension is clipped to the pane
// (`extendedSegment`) and hit-tested where it is drawn.
export class TrendlinePrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private points: [ScreenPoint, ScreenPoint] | null = null;
  /** The segment as drawn (the extension included), in CSS px; null until drawable. */
  private drawn: [ScreenPoint, ScreenPoint] | null = null;
  /** The pane's CSS size at the last paint: what an extension is clipped to between paints. */
  private pane: { width: number; height: number } | null = null;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  private anchors: [TrendlineAnchorInput, TrendlineAnchorInput];
  private color: string;
  private options: LineOptions;
  private readonly grid: BarGrid;
  private handlesVisible = false;

  constructor(
    anchors: [TrendlineAnchorInput, TrendlineAnchorInput],
    color: string,
    grid: BarGrid = new BarGrid(),
    options: LineOptions = PLAIN_LINE,
  ) {
    this.anchors = anchors;
    this.color = color;
    this.grid = grid;
    this.options = options;
  }

  /** Handle squares are drawn only while the drawings are editable (the Cursor tool), never locked. */
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
    this.drawn = null;
  }

  update(anchors: [TrendlineAnchorInput, TrendlineAnchorInput], color: string, options: LineOptions = PLAIN_LINE): void {
    if (anchors === this.anchors && color === this.color && sameOptions(options, this.options)) return;
    this.anchors = anchors;
    this.color = color;
    this.options = options;
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
    this.drawn = this.points && this.segmentIn(this.points, this.pane);
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** Pixel distance from (x, y) to the drawn segment (its extension included), or `null` when not drawable. */
  distanceTo(x: number, y: number): number | null {
    const segment = this.drawn ?? this.points;
    if (!segment) return null;
    const [a, b] = segment;
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

  /** The segment as drawn (an extension clipped to the pane), or `null` -- exposed for tests. */
  drawnSegment(): [ScreenPoint, ScreenPoint] | null {
    return this.drawn;
  }

  private segmentIn(points: [ScreenPoint, ScreenPoint], pane: { width: number; height: number } | null): [ScreenPoint, ScreenPoint] {
    // Before the first paint the pane's size is unknown: the anchors' own segment until then.
    if (!pane) return points;
    return extendedSegment(points[0], points[1], this.options.extend, pane.width, pane.height);
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const points = this.points;
    if (!points) return null;
    const { color, options } = this;
    const handles = this.handlesVisible && !options.locked;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          this.pane = { width: bitmapSize.width / hr, height: bitmapSize.height / vr };
          const [from, to] = this.segmentIn(points, this.pane);
          this.drawn = [from, to];
          context.strokeStyle = color;
          context.lineWidth = options.lineWidth * hr;
          context.setLineDash(lineDash(options.lineStyle, hr));
          context.beginPath();
          context.moveTo(from.x * hr, from.y * vr);
          context.lineTo(to.x * hr, to.y * vr);
          context.stroke();
          if (options.arrow) drawArrowHead(context, points[0], points[1], options.lineWidth, hr, vr);
          if (handles) drawHandles(context, points, chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}

function sameOptions(a: LineOptions, b: LineOptions): boolean {
  return a.extend === b.extend && a.arrow === b.arrow && a.lineWidth === b.lineWidth && a.lineStyle === b.lineStyle && a.locked === b.locked;
}
