import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { type FibDrawing, fibLabel, fibLevelPrices } from "../../../lib/drawings";
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

// Transitive fancy-canvas type, derived rather than imported (not a direct dependency).
type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

const FONT_PX = 11;
const BAND_ALPHA = 0.1;
const SEGMENT_ALPHA = 0.5;

interface ScreenLevel {
  y: number;
  label: string | null;
  color: string;
}

interface Geometry {
  a: { x: number; y: number };
  b: { x: number; y: number };
  left: number;
  /** The levels' right end in CSS pixels, or null: they run to the pane's right edge. */
  right: number | null;
  levels: ScreenLevel[];
}

/**
 * A Fibonacci retracement (Story 32.5): one level line per enabled ratio between anchor A (ratio
 * 1) and anchor B (ratio 0), a translucent band between consecutive levels, the A-B segment drawn
 * faintly, and a "ratio (price)" label per level at the instrument's price precision. The
 * anchors stay `{time, price}`; screen coordinates are recomputed on every `updateAllViews`
 * (the library calls it before each redraw), each anchor on the latest bar at or before its time.
 * With no precision known the geometry draws but no label does (never a guessed precision).
 */
export class FibPrimitive implements DrawingPrimitive {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  private requestUpdate: (() => void) | null = null;
  private geometry: Geometry | null = null;
  private handlesVisible = false;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => this.renderer(),
  };

  private fib: FibDrawing;
  private pricePrecision: number | null;
  private readonly grid: BarGrid;

  constructor(fib: FibDrawing, pricePrecision: number | null, grid: BarGrid = new BarGrid()) {
    this.fib = fib;
    this.pricePrecision = pricePrecision;
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

  update(fib: FibDrawing, pricePrecision: number | null): void {
    if (fib === this.fib && pricePrecision === this.pricePrecision) return;
    this.fib = fib;
    this.pricePrecision = pricePrecision;
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
    const [a, b] = this.fib.anchors;
    const sa = this.grid.snap(a.time);
    const sb = this.grid.snap(b.time);
    const ax = sa === null ? null : timeScale.timeToCoordinate(sa as Time);
    const bx = sb === null ? null : timeScale.timeToCoordinate(sb as Time);
    const ay = series.priceToCoordinate(a.price);
    const by = series.priceToCoordinate(b.price);
    if (ax === null || bx === null || ay === null || by === null) {
      // An anchor off the scrolled-out time range has no coordinate; draw nothing rather than
      // fabricate a position for it.
      this.geometry = null;
      return;
    }
    const levels: ScreenLevel[] = [];
    for (const level of fibLevelPrices(this.fib, this.pricePrecision)) {
      const y = series.priceToCoordinate(level.price);
      if (y === null) continue;
      levels.push({
        y,
        label: this.pricePrecision === null ? null : fibLabel(level.ratio, level.price, this.pricePrecision),
        color: level.color,
      });
    }
    const left = Math.min(ax, bx);
    this.geometry = {
      a: { x: ax, y: ay },
      b: { x: bx, y: by },
      left,
      right: this.fib.extend_right ? null : Math.max(ax, bx),
      levels,
    };
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The A / B handle within the grab radius, else a level line or the A-B segment within its tolerance. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const handle = nearestHandle(
      [
        { id: "a", ...g.a },
        { id: "b", ...g.b },
      ],
      x,
      y,
    );
    if (handle) return handle;
    let best = distanceToSegment(x, y, g.a.x, g.a.y, g.b.x, g.b.y);
    if (x >= g.left && (g.right === null || x <= g.right)) {
      for (const level of g.levels) best = Math.min(best, Math.abs(y - level.y));
    }
    return best <= BODY_TOLERANCE_PX ? { handle: null, distance: best } : null;
  }

  /** The current screen geometry, or `null` when not drawable -- exposed for tests. */
  screen(): Geometry | null {
    return this.geometry;
  }

  private renderer(): IPrimitivePaneRenderer | null {
    const g = this.geometry;
    if (!g) return null;
    const { line_width: lineWidth, label_side: labelSide } = this.fib;
    const handles = this.handlesVisible;
    const dim = chartVar("--chart-text-dim");
    const bg = chartVar("--chart-bg");
    return {
      draw: (target: CanvasRenderingTarget2D): void => {
        target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const left = g.left * hr;
          const right = g.right === null ? bitmapSize.width : g.right * hr;
          // Bands first, so the lines and labels sit on top of them.
          context.save();
          context.globalAlpha = BAND_ALPHA;
          for (let i = 1; i < g.levels.length; i++) {
            const upper = g.levels[i - 1];
            const lower = g.levels[i];
            context.fillStyle = lower.color;
            const top = Math.min(upper.y, lower.y) * vr;
            context.fillRect(left, top, right - left, Math.abs(lower.y - upper.y) * vr);
          }
          context.restore();
          // The A-B segment, faint and dashed.
          context.save();
          context.globalAlpha = SEGMENT_ALPHA;
          context.strokeStyle = dim;
          context.lineWidth = hr;
          context.setLineDash([4 * hr, 4 * hr]);
          context.beginPath();
          context.moveTo(g.a.x * hr, g.a.y * vr);
          context.lineTo(g.b.x * hr, g.b.y * vr);
          context.stroke();
          context.restore();
          context.font = `${FONT_PX * vr}px sans-serif`;
          context.textBaseline = "bottom";
          context.textAlign = labelSide === "left" ? "left" : "right";
          for (const level of g.levels) {
            context.strokeStyle = level.color;
            context.lineWidth = lineWidth * hr;
            context.beginPath();
            context.moveTo(left, level.y * vr);
            context.lineTo(right, level.y * vr);
            context.stroke();
            if (level.label === null) continue;
            context.fillStyle = level.color;
            const x = labelSide === "left" ? left + 4 * hr : right - 4 * hr;
            context.fillText(level.label, x, level.y * vr - 2 * vr);
          }
          if (!handles) return;
          const half = (HANDLE_SIZE_PX / 2) * hr;
          context.fillStyle = bg;
          context.strokeStyle = dim;
          context.lineWidth = hr;
          for (const p of [g.a, g.b]) {
            context.fillRect(p.x * hr - half, p.y * vr - half, half * 2, half * 2);
            context.strokeRect(p.x * hr - half, p.y * vr - half, half * 2, half * 2);
          }
        });
      },
    };
  }
}
