import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { chartVar } from "../chartTheme";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/**
 * Story 33.12: the session breaks -- a dashed vertical line at the first bar of each UTC day
 * (`lib/time.ts`'s `sessionBreakTimes`), full height on the price pane, drawn at the bottom z-order so
 * it sits behind the candles like a grid line. The same template as `VerticalMarkerPrimitive`, for a
 * list of times; the colour is a chart token resolved on every draw.
 */
export class SessionBreaksPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private times: readonly number[];
  private xs: number[] = [];
  private readonly view: IPrimitivePaneView = {
    zOrder: (): PrimitivePaneViewZOrder => "bottom",
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(times: readonly number[]) {
    this.times = times;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
    this.xs = [];
  }

  setTimes(times: readonly number[]): void {
    if (times === this.times) return;
    this.times = times;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    const timeScale = this.chart?.timeScale();
    this.xs = [];
    if (!timeScale) return;
    for (const time of this.times) {
      const x = timeScale.timeToCoordinate(time as Time);
      if (x !== null) this.xs.push(x);
    }
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The x of every break on screen, for the tests. */
  screenXs(): readonly number[] {
    return this.xs;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const xs = this.xs;
    if (xs.length === 0) return null;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        const color = chartVar("--chart-text-dim");
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, bitmapSize }) => {
          context.strokeStyle = color;
          context.lineWidth = horizontalPixelRatio;
          context.setLineDash([2 * horizontalPixelRatio, 4 * horizontalPixelRatio]);
          context.beginPath();
          for (const x of xs) {
            const px = Math.round(x * horizontalPixelRatio) + 0.5;
            context.moveTo(px, 0);
            context.lineTo(px, bitmapSize.height);
          }
          context.stroke();
        });
      },
    };
  }
}
