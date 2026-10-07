import type { IPrimitivePaneRenderer } from "lightweight-charts";

import { DEFAULT_DRAWING_LINE_WIDTH, type VlineDrawing } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  AnchoredDrawingPrimitive,
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawTarget,
  type DrawingHit,
  drawHandles,
  lineDash,
} from "./drawingPrimitive";

interface Geometry {
  x: number;
}

/**
 * Story 33.10: a vertical line at a bar, across the whole price pane. The line itself is its one
 * handle (`"time"`), like a horizontal price line's: a grab anywhere along it drags the bar; the
 * square drawn at mid-height only shows it is editable.
 */
export class VlinePrimitive extends AnchoredDrawingPrimitive<Geometry> {
  private drawing: VlineDrawing;

  constructor(drawing: VlineDrawing, grid: BarGrid = new BarGrid()) {
    super(grid);
    this.drawing = drawing;
  }

  update(drawing: VlineDrawing): void {
    if (drawing === this.drawing) return;
    this.drawing = drawing;
    this.changed();
  }

  protected compute(): Geometry | null {
    const x = this.xOf(this.drawing.time);
    return x === null ? null : { x };
  }

  hit(x: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const distance = Math.abs(x - g.x);
    return distance <= BODY_TOLERANCE_PX ? { handle: "time", distance } : null;
  }

  protected renderer(g: Geometry): IPrimitivePaneRenderer {
    const d = this.drawing;
    const color = d.color ?? chartVar("--chart-drawing");
    const handles = this.handlesVisible && !d.locked;
    return {
      draw: (target: DrawTarget): void => {
        target.useBitmapCoordinateSpace(({ context, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          context.strokeStyle = color;
          context.lineWidth = (d.line_width ?? DEFAULT_DRAWING_LINE_WIDTH) * hr;
          context.setLineDash(lineDash(d.line_style, hr));
          context.beginPath();
          context.moveTo(g.x * hr, 0);
          context.lineTo(g.x * hr, bitmapSize.height);
          context.stroke();
          if (handles) drawHandles(context, [{ x: g.x, y: bitmapSize.height / vr / 2 }], chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}
