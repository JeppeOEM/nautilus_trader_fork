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
  nearestHandle,
} from "./drawingPrimitive";

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

interface Geometry {
  x: number;
  /** The handle's height (the profile's top), or null while the profile has no rows. */
  y: number | null;
}

/**
 * The editable half of an Anchored Volume Profile (Story 32.7): the anchor's dashed vertical line
 * and its grab handle, on the one drawing hit-test path (`DrawingPrimitive`). The profile itself is
 * drawn by the one `VolumeProfilePrimitive` from the one engine; this primitive only marks where it
 * starts and lets the operator drag that start (handle `"anchor"`) or select the drawing (its line).
 */
export class AnchoredVpPrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private geometry: Geometry | null = null;
  private handlesVisible = false;
  private time: number;
  private anchorPrice: number | null;
  private readonly grid: BarGrid;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  constructor(time: number, anchorPrice: number | null, grid: BarGrid = new BarGrid()) {
    this.time = time;
    this.anchorPrice = anchorPrice;
    this.grid = grid;
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
    this.geometry = null;
  }

  update(time: number, anchorPrice: number | null): void {
    if (time === this.time && anchorPrice === this.anchorPrice) return;
    this.time = time;
    this.anchorPrice = anchorPrice;
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
    const snapped = this.grid.snap(this.time);
    const x = snapped === null ? null : chart.timeScale().timeToCoordinate(snapped as Time);
    const y = this.anchorPrice === null ? null : series.priceToCoordinate(this.anchorPrice);
    this.geometry = x === null ? null : { x, y };
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The anchor handle within the grab radius, else the anchor line within its tolerance. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    if (g.y !== null) {
      const handle = nearestHandle([{ id: "anchor", x: g.x, y: g.y }], x, y);
      if (handle) return handle;
    }
    const distance = Math.abs(x - g.x);
    return distance <= BODY_TOLERANCE_PX ? { handle: null, distance } : null;
  }

  /** The current screen geometry, or `null` when not drawable -- exposed for tests. */
  screen(): Geometry | null {
    return this.geometry;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const g = this.geometry;
    if (!g) return null;
    const handles = this.handlesVisible && g.y !== null;
    const color = chartVar("--chart-drawing");
    const bg = chartVar("--chart-bg");
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr, bitmapSize }) => {
          context.strokeStyle = color;
          context.lineWidth = hr;
          context.setLineDash([4 * hr, 4 * hr]);
          context.beginPath();
          context.moveTo(g.x * hr, 0);
          context.lineTo(g.x * hr, bitmapSize.height);
          context.stroke();
          context.setLineDash([]);
          if (!handles || g.y === null) return;
          const half = (HANDLE_SIZE_PX / 2) * hr;
          context.fillStyle = bg;
          context.fillRect(g.x * hr - half, g.y * vr - half, half * 2, half * 2);
          context.strokeRect(g.x * hr - half, g.y * vr - half, half * 2, half * 2);
        });
      },
    };
  }
}
