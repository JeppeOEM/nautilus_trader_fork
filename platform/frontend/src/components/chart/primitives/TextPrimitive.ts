import type { IPrimitivePaneRenderer } from "lightweight-charts";

import type { TextDrawing } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  AnchoredDrawingPrimitive,
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawTarget,
  type DrawingHit,
  drawHandles,
  nearestHandle,
} from "./drawingPrimitive";

const PAD_PX = 3;
const LINE_HEIGHT = 1.25;
/** A character's width per pixel of font size, until a paint has measured the real text. */
const CHAR_WIDTH_ESTIMATE = 0.6;

interface Geometry {
  /** The anchor: the box's top-left corner. */
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Story 33.10: a text note: its lines (`\n` breaks a line) in its colour and font size, inside a box
 * whose top-left corner is the anchor. The box is the body (a click anywhere on it selects it);
 * the handle `anchor` sits on that corner. The box's width is the text as last measured by a paint
 * (an estimate from the font size before the first one).
 */
export class TextPrimitive extends AnchoredDrawingPrimitive<Geometry> {
  private drawing: TextDrawing;
  /** The widest line's width in CSS px at the last paint, for the text it measured. */
  private measured: { text: string; fontSize: number; width: number } | null = null;

  constructor(drawing: TextDrawing, grid: BarGrid = new BarGrid()) {
    super(grid);
    this.drawing = drawing;
  }

  update(drawing: TextDrawing): void {
    if (drawing === this.drawing) return;
    this.drawing = drawing;
    this.changed();
  }

  protected compute(): Geometry | null {
    const at = this.pointOf(this.drawing.anchor);
    if (!at) return null;
    const { text, font_size: size } = this.drawing;
    const lines = text.split("\n");
    const fresh = this.measured !== null && this.measured.text === text && this.measured.fontSize === size;
    const textWidth = fresh ? this.measured!.width : Math.max(...lines.map((l) => l.length)) * size * CHAR_WIDTH_ESTIMATE;
    return { x: at.x, y: at.y, width: textWidth + 2 * PAD_PX, height: lines.length * size * LINE_HEIGHT + 2 * PAD_PX };
  }

  /** The corner handle, else the box. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const handle = nearestHandle([{ id: "anchor", x: g.x, y: g.y }], x, y);
    if (handle) return handle;
    // Anywhere on the box counts, at the body tolerance: a line or edge passing closer still wins.
    const inside = x >= g.x && x <= g.x + g.width && y >= g.y && y <= g.y + g.height;
    return inside ? { handle: null, distance: BODY_TOLERANCE_PX } : null;
  }

  protected renderer(g: Geometry): IPrimitivePaneRenderer {
    const d = this.drawing;
    const color = d.color ?? chartVar("--chart-drawing");
    const handles = this.handlesVisible && !d.locked;
    const lines = d.text.split("\n");
    return {
      draw: (target: DrawTarget): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          context.font = `${d.font_size * vr}px sans-serif`;
          context.textBaseline = "top";
          context.textAlign = "left";
          context.fillStyle = color;
          let widest = 0;
          lines.forEach((line, i) => {
            widest = Math.max(widest, context.measureText(line).width / hr);
            context.fillText(line, (g.x + PAD_PX) * hr, (g.y + PAD_PX + i * d.font_size * LINE_HEIGHT) * vr);
          });
          this.measured = { text: d.text, fontSize: d.font_size, width: widest };
          if (!handles) return;
          context.strokeStyle = color;
          context.lineWidth = hr;
          context.setLineDash([2 * hr, 2 * hr]);
          context.strokeRect(g.x * hr, g.y * vr, (widest + 2 * PAD_PX) * hr, g.height * vr);
          context.strokeStyle = color;
          drawHandles(context, [{ x: g.x, y: g.y }], chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}
