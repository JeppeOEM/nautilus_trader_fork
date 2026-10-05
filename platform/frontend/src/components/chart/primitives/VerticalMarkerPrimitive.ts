import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { chartVar } from "../chartTheme";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

// Story 18.4 (AC #2): the replay start-bar marker -- a full-height vertical line at a
// time. lightweight-charts has no native vertical time line, so this is a primitive
// (same family as the trendline/measurement ones); price lines are horizontal only.
// DW-146: the colour is the `--chart-marker` token, resolved on every draw (like
// PositionPrimitive) so a theme change reaches a marker that already exists.
export class VerticalMarkerPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private x: number | null = null;
  private time: Time;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(time: Time) {
    this.time = time;
  }

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
    this.x = null;
  }

  setTime(time: Time): void {
    if (time === this.time) return;
    this.time = time;
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    this.x = this.chart ? this.chart.timeScale().timeToCoordinate(this.time) : null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  screenX(): number | null {
    return this.x;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const x = this.x;
    if (x === null) return null;
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        const color = chartVar("--chart-marker");
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, bitmapSize }) => {
          context.strokeStyle = color;
          context.lineWidth = horizontalPixelRatio;
          context.setLineDash([4 * horizontalPixelRatio, 4 * horizontalPixelRatio]);
          context.beginPath();
          context.moveTo(x * horizontalPixelRatio, 0);
          context.lineTo(x * horizontalPixelRatio, bitmapSize.height);
          context.stroke();
        });
      },
    };
  }
}
