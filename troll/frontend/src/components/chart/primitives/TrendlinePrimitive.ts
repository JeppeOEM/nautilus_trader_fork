import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

export interface TrendlineAnchor {
  time: Time;
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
export class TrendlinePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private points: [ScreenPoint, ScreenPoint] | null = null;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  private anchors: [TrendlineAnchor, TrendlineAnchor];
  private color: string;

  constructor(anchors: [TrendlineAnchor, TrendlineAnchor], color: string) {
    this.anchors = anchors;
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
    this.points = null;
  }

  update(anchors: [TrendlineAnchor, TrendlineAnchor], color: string): void {
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
    const ax = timeScale.timeToCoordinate(a.time);
    const bx = timeScale.timeToCoordinate(b.time);
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

  /** The current screen-space endpoints, or `null` when not drawable -- exposed for tests. */
  screenPoints(): [ScreenPoint, ScreenPoint] | null {
    return this.points;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const points = this.points;
    if (!points) return null;
    const color = this.color;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
          context.strokeStyle = color;
          context.lineWidth = LINE_WIDTH * horizontalPixelRatio;
          context.beginPath();
          context.moveTo(points[0].x * horizontalPixelRatio, points[0].y * verticalPixelRatio);
          context.lineTo(points[1].x * horizontalPixelRatio, points[1].y * verticalPixelRatio);
          context.stroke();
        });
      },
    };
  }
}
