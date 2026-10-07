import type { IPrimitivePaneRenderer } from "lightweight-charts";

import { DEFAULT_DRAWING_LINE_WIDTH, type RectDrawing, rectLabel } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  AnchoredDrawingPrimitive,
  BarGrid,
  type DrawTarget,
  type DrawingHit,
  boxDistance,
  drawHandles,
  lineDash,
  nearestHandle,
} from "./drawingPrimitive";

const FONT_PX = 11;
const LABEL_PAD_PX = 4;

interface Geometry {
  a: { x: number; y: number };
  b: { x: number; y: number };
  /** "height (percent)", null while the precision is unknown (never a guessed one). */
  label: string | null;
}

/**
 * Story 33.10: a rectangle between two opposite corners (handles `a` and `b`), filled in its colour
 * at `fill_opacity`, with its price height and percent in the top-left corner.
 */
export class RectPrimitive extends AnchoredDrawingPrimitive<Geometry> {
  private drawing: RectDrawing;
  private pricePrecision: number | null;

  constructor(drawing: RectDrawing, pricePrecision: number | null, grid: BarGrid = new BarGrid()) {
    super(grid);
    this.drawing = drawing;
    this.pricePrecision = pricePrecision;
  }

  update(drawing: RectDrawing, pricePrecision: number | null): void {
    if (drawing === this.drawing && pricePrecision === this.pricePrecision) return;
    this.drawing = drawing;
    this.pricePrecision = pricePrecision;
    this.changed();
  }

  protected compute(): Geometry | null {
    const a = this.pointOf(this.drawing.anchors[0]);
    const b = this.pointOf(this.drawing.anchors[1]);
    if (!a || !b) return null;
    const label = this.pricePrecision === null ? null : rectLabel(this.drawing, this.pricePrecision);
    return { a, b, label };
  }

  /** A corner handle, else the box (its border, or anywhere inside it). */
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

  protected renderer(g: Geometry): IPrimitivePaneRenderer {
    const d = this.drawing;
    const color = d.color ?? chartVar("--chart-drawing");
    const handles = this.handlesVisible && !d.locked;
    // A hand-edited opacity outside 0..1 must not throw or paint opaque: clamped.
    const alpha = Number.isFinite(d.fill_opacity) ? Math.min(1, Math.max(0, d.fill_opacity)) : 0;
    return {
      draw: (target: DrawTarget): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const x = Math.min(g.a.x, g.b.x) * hr;
          const y = Math.min(g.a.y, g.b.y) * vr;
          const w = Math.abs(g.b.x - g.a.x) * hr;
          const h = Math.abs(g.b.y - g.a.y) * vr;
          context.save();
          context.globalAlpha = alpha;
          context.fillStyle = color;
          context.fillRect(x, y, w, h);
          context.restore();
          context.strokeStyle = color;
          context.lineWidth = (d.line_width ?? DEFAULT_DRAWING_LINE_WIDTH) * hr;
          context.setLineDash(lineDash(d.line_style, hr));
          context.strokeRect(x, y, w, h);
          if (g.label !== null) {
            context.font = `${FONT_PX * vr}px sans-serif`;
            context.textBaseline = "top";
            context.textAlign = "left";
            context.fillStyle = color;
            context.fillText(g.label, x + LABEL_PAD_PX * hr, y + LABEL_PAD_PX * vr);
          }
          if (handles) drawHandles(context, [g.a, g.b], chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}
