import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import {
  type FibDrawing,
  type FibExtensionDrawing,
  type FibLevelPrice,
  fibExtensionLevelPrices,
  fibLabel,
  fibLevelPrices,
} from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawingHit,
  type DrawingPrimitive,
  distanceToSegment,
  drawHandles,
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
  /** A Fibonacci extension's third anchor (the levels project from it); absent on a retracement. */
  c?: { x: number; y: number };
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
 *
 * Story 33.10: it also draws the trend-based Fibonacci extension: three anchors (A-B-C drawn as
 * the faint path), each level at `C + (B - A) * ratio`, starting at C's bar.
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

  private fib: FibDrawing | FibExtensionDrawing;
  private pricePrecision: number | null;
  private readonly grid: BarGrid;

  constructor(fib: FibDrawing | FibExtensionDrawing, pricePrecision: number | null, grid: BarGrid = new BarGrid()) {
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

  update(fib: FibDrawing | FibExtensionDrawing, pricePrecision: number | null): void {
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
    const anchors = this.fib.anchors.map((anchor) => {
      const snapped = this.grid.snap(anchor.time);
      const x = snapped === null ? null : timeScale.timeToCoordinate(snapped as Time);
      const y = series.priceToCoordinate(anchor.price);
      return x === null || y === null ? null : { x, y };
    });
    // An anchor off the scrolled-out time range has no coordinate; draw nothing rather than
    // fabricate a position for it.
    if (anchors.some((p) => p === null)) {
      this.geometry = null;
      return;
    }
    const [a, b, c] = anchors as { x: number; y: number }[];
    const levels: ScreenLevel[] = [];
    for (const level of this.levelPrices()) {
      const y = series.priceToCoordinate(level.price);
      if (y === null) continue;
      levels.push({
        y,
        label: this.pricePrecision === null ? null : fibLabel(level.ratio, level.price, this.pricePrecision),
        color: level.color,
      });
    }
    // A retracement spans A..B; an extension's levels start at C and reach as far right as the A-B move.
    const left = c === undefined ? Math.min(a.x, b.x) : c.x;
    const reach = c === undefined ? Math.max(a.x, b.x) : Math.max(a.x, b.x, c.x + Math.abs(b.x - a.x));
    this.geometry = { a, b, ...(c === undefined ? {} : { c }), left, right: this.fib.extend_right ? null : reach, levels };
  }

  private levelPrices(): FibLevelPrice[] {
    return this.fib.kind === "fib_extension"
      ? fibExtensionLevelPrices(this.fib, this.pricePrecision)
      : fibLevelPrices(this.fib, this.pricePrecision);
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The anchor handles (A, B and an extension's C) within the grab radius, else a level line or the
   * anchors' path within its tolerance. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const handle = nearestHandle(handlesOf(g), x, y);
    if (handle) return handle;
    let best = distanceToSegment(x, y, g.a.x, g.a.y, g.b.x, g.b.y);
    if (g.c) best = Math.min(best, distanceToSegment(x, y, g.b.x, g.b.y, g.c.x, g.c.y));
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
    const handles = this.handlesVisible && !this.fib.locked;
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
          // The A-B segment (and an extension's B-C), faint and dashed.
          context.save();
          context.globalAlpha = SEGMENT_ALPHA;
          context.strokeStyle = dim;
          context.lineWidth = hr;
          context.setLineDash([4 * hr, 4 * hr]);
          const path = g.c ? [[g.a, g.b], [g.b, g.c]] : [[g.a, g.b]];
          for (const [from, to] of path) {
            context.beginPath();
            context.moveTo(from.x * hr, from.y * vr);
            context.lineTo(to.x * hr, to.y * vr);
            context.stroke();
          }
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
          context.strokeStyle = dim;
          context.lineWidth = hr;
          drawHandles(context, handlesOf(g), bg, hr, vr);
        });
      },
    };
  }
}

function handlesOf(g: Geometry): { id: string; x: number; y: number }[] {
  const handles = [
    { id: "a", ...g.a },
    { id: "b", ...g.b },
  ];
  if (g.c) handles.push({ id: "c", ...g.c });
  return handles;
}
