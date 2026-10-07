import type { IPrimitivePaneRenderer } from "lightweight-charts";

import { type ChannelDrawing, DEFAULT_DRAWING_LINE_WIDTH } from "../../../lib/drawings";
import { chartVar } from "../chartTheme";
import {
  AnchoredDrawingPrimitive,
  BODY_TOLERANCE_PX,
  BarGrid,
  type DrawTarget,
  type DrawingHit,
  distanceToSegment,
  drawHandles,
  lineDash,
  nearestHandle,
} from "./drawingPrimitive";

const FILL_ALPHA = 0.1;

interface Point {
  x: number;
  y: number;
}

interface Geometry {
  a: Point;
  b: Point;
  /** The parallel's ends: A and B moved by the offset (a price). */
  a2: Point;
  b2: Point;
  /** The offset handle: the middle of the parallel. */
  offset: Point;
}

/**
 * Story 33.10: a parallel channel: the A-B line, its parallel `offset` (a price distance) away and a
 * translucent fill between them. Handles: `a` and `b` move the line (the offset is kept), `offset`
 * at the middle of the parallel moves the parallel alone.
 */
export class ChannelPrimitive extends AnchoredDrawingPrimitive<Geometry> {
  private drawing: ChannelDrawing;

  constructor(drawing: ChannelDrawing, grid: BarGrid = new BarGrid()) {
    super(grid);
    this.drawing = drawing;
  }

  update(drawing: ChannelDrawing): void {
    if (drawing === this.drawing) return;
    this.drawing = drawing;
    this.changed();
  }

  protected compute(): Geometry | null {
    const [anchorA, anchorB] = this.drawing.anchors;
    const offset = this.drawing.offset;
    const a = this.pointOf(anchorA);
    const b = this.pointOf(anchorB);
    const ay2 = this.priceY(anchorA.price + offset);
    const by2 = this.priceY(anchorB.price + offset);
    if (!a || !b || ay2 === null || by2 === null) return null;
    const a2 = { x: a.x, y: ay2 };
    const b2 = { x: b.x, y: by2 };
    return { a, b, a2, b2, offset: { x: (a2.x + b2.x) / 2, y: (a2.y + b2.y) / 2 } };
  }

  /** A handle, else either line within its tolerance, else anywhere between them. */
  hit(x: number, y: number): DrawingHit | null {
    const g = this.geometry;
    if (!g) return null;
    const handle = nearestHandle(handlesOf(g), x, y);
    if (handle) return handle;
    const distance = Math.min(
      distanceToSegment(x, y, g.a.x, g.a.y, g.b.x, g.b.y),
      distanceToSegment(x, y, g.a2.x, g.a2.y, g.b2.x, g.b2.y),
    );
    if (distance <= BODY_TOLERANCE_PX) return { handle: null, distance };
    return between(g, x, y) ? { handle: null, distance: BODY_TOLERANCE_PX } : null;
  }

  protected renderer(g: Geometry): IPrimitivePaneRenderer {
    const d = this.drawing;
    const color = d.color ?? chartVar("--chart-drawing");
    const handles = this.handlesVisible && !d.locked;
    return {
      draw: (target: DrawTarget): void => {
        target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
          const at = (p: Point): [number, number] => [p.x * hr, p.y * vr];
          context.save();
          context.globalAlpha = FILL_ALPHA;
          context.fillStyle = color;
          context.beginPath();
          context.moveTo(...at(g.a));
          context.lineTo(...at(g.b));
          context.lineTo(...at(g.b2));
          context.lineTo(...at(g.a2));
          context.closePath();
          context.fill();
          context.restore();
          context.strokeStyle = color;
          context.lineWidth = (d.line_width ?? DEFAULT_DRAWING_LINE_WIDTH) * hr;
          context.setLineDash(lineDash(d.line_style, hr));
          for (const [from, to] of [
            [g.a, g.b],
            [g.a2, g.b2],
          ]) {
            context.beginPath();
            context.moveTo(...at(from));
            context.lineTo(...at(to));
            context.stroke();
          }
          if (handles) drawHandles(context, handlesOf(g), chartVar("--chart-bg"), hr, vr);
        });
      },
    };
  }
}

function handlesOf(g: Geometry): { id: string; x: number; y: number }[] {
  return [
    { id: "a", ...g.a },
    { id: "b", ...g.b },
    { id: "offset", ...g.offset },
  ];
}

/** Whether (x, y) lies between the two lines, within the anchors' horizontal span. */
function between(g: Geometry, x: number, y: number): boolean {
  const left = Math.min(g.a.x, g.b.x);
  const right = Math.max(g.a.x, g.b.x);
  if (x < left || x > right || g.a.x === g.b.x) return false;
  const t = (x - g.a.x) / (g.b.x - g.a.x);
  const y1 = g.a.y + t * (g.b.y - g.a.y);
  const y2 = g.a2.y + t * (g.b2.y - g.a2.y);
  return y >= Math.min(y1, y2) && y <= Math.max(y1, y2);
}
