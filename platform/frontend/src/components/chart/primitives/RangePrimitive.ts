import type { IPrimitivePaneRenderer, Time } from "lightweight-charts";

import {
  DEFAULT_DRAWING_LINE_WIDTH,
  type InstrumentPrecision,
  type RangeDrawing,
  rangeLabels,
} from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  AnchoredDrawingPrimitive,
  BarGrid,
  type DrawTarget,
  type DrawingHit,
  boxDistance,
  drawArrowHead,
  drawHandles,
  lineDash,
  nearestHandle,
} from "./drawingPrimitive";
import { type MeasurementIndex, buildMeasurementIndex, computeMeasurement } from "./MeasurementPrimitive";

const EMPTY_INDEX = buildMeasurementIndex([], []);

const FILL_ALPHA = 0.12;
const FONT_PX = 11;
const LINE_PX = FONT_PX + 3;
const LABEL_GAP_PX = 4;

interface Point {
  x: number;
  y: number;
}

interface Geometry {
  a: Point;
  b: Point;
  /** The label lines, empty while the precision is unknown (never a guessed one). */
  lines: string[];
}

/**
 * Story 33.10: a saved Price Range or Date Range between A and B (handles `a`, `b`): a translucent
 * box, an arrow from A towards B -- vertical for a price range, horizontal for a date range -- and
 * the label under the box. The numbers are the Measure tool's (`computeMeasurement` over the chart's
 * candles and volume, read through `measure` at each repaint, so they follow new pages and bars);
 * `measure` answers null where the axis is not the candles' (Lines mode).
 *
 * Known limit: the forming live bar is not counted (the Measure tool's index holds the closed
 * bars), so a range reaching the newest bar reads one bar and its volume short until it closes.
 * Upgrade path: pass the chart's live bar to `computeMeasurement`, as the Measure drag does.
 */
export class RangePrimitive extends AnchoredDrawingPrimitive<Geometry> {
  private drawing: RangeDrawing;
  private precision: InstrumentPrecision | null;
  private readonly measure: () => MeasurementIndex | null;

  constructor(
    drawing: RangeDrawing,
    precision: InstrumentPrecision | null,
    measure: () => MeasurementIndex | null,
    grid: BarGrid = new BarGrid(),
  ) {
    super(grid);
    this.drawing = drawing;
    this.precision = precision;
    this.measure = measure;
  }

  update(drawing: RangeDrawing, precision: InstrumentPrecision | null): void {
    if (drawing === this.drawing && precision === this.precision) return;
    this.drawing = drawing;
    this.precision = precision;
    this.changed();
  }

  protected compute(): Geometry | null {
    const [anchorA, anchorB] = this.drawing.anchors;
    const a = this.pointOf(anchorA);
    const b = this.pointOf(anchorB);
    if (!a || !b) return null;
    if (this.precision === null) return { a, b, lines: [] };
    const index = this.measure();
    const start = { time: anchorA.time as Time, price: anchorA.price };
    const end = { time: anchorB.time as Time, price: anchorB.price };
    // No candle bars behind the axis (Lines mode's snapshot seconds, or nothing loaded yet): the
    // price part is still the anchors', the bars and volume are unknown -- `n/a`, never a 0 count.
    const m =
      index === null || index.barTimes.length === 0
        ? { ...computeMeasurement(start, end, EMPTY_INDEX), bars: null, volume: null }
        : computeMeasurement(start, end, index);
    return { a, b, lines: rangeLabels(m, this.drawing.kind, this.precision) };
  }

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
    const distance = boxDistance(x, y, g.a, g.b);
    return distance === null ? null : { handle: null, distance };
  }

  /** The arrow's ends: across the box from A's side to B's, through its middle. */
  private arrow(g: Geometry): [Point, Point] {
    if (this.drawing.kind === "price_range") {
      const x = (g.a.x + g.b.x) / 2;
      return [
        { x, y: g.a.y },
        { x, y: g.b.y },
      ];
    }
    const y = (g.a.y + g.b.y) / 2;
    return [
      { x: g.a.x, y },
      { x: g.b.x, y },
    ];
  }

  protected renderer(g: Geometry): IPrimitivePaneRenderer {
    const d = this.drawing;
    const color = d.color ?? chartVar("--chart-drawing");
    const handles = this.handlesVisible && !d.locked;
    const width = d.line_width ?? DEFAULT_DRAWING_LINE_WIDTH;
    const [from, to] = this.arrow(g);
    return {
      draw: (target: DrawTarget): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const left = Math.min(g.a.x, g.b.x);
          const top = Math.min(g.a.y, g.b.y);
          const bottom = Math.max(g.a.y, g.b.y);
          context.save();
          context.globalAlpha = FILL_ALPHA;
          context.fillStyle = color;
          context.fillRect(left * hr, top * vr, Math.abs(g.b.x - g.a.x) * hr, (bottom - top) * vr);
          context.restore();
          context.strokeStyle = color;
          context.lineWidth = width * hr;
          context.setLineDash(lineDash(d.line_style, hr));
          context.beginPath();
          context.moveTo(from.x * hr, from.y * vr);
          context.lineTo(to.x * hr, to.y * vr);
          context.stroke();
          drawArrowHead(context, from, to, width, hr, vr);
          context.font = `${FONT_PX * vr}px sans-serif`;
          context.textBaseline = "top";
          context.textAlign = "left";
          context.fillStyle = color;
          g.lines.forEach((line, i) => context.fillText(line, left * hr, (bottom + LABEL_GAP_PX + i * LINE_PX) * vr));
          if (handles) drawHandles(context, [g.a, g.b], chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}
