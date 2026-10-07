import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
} from "lightweight-charts";

import { type Anchor, type LineStyleName, snapIndex } from "../../../lib/drawings";

/** A grab radius around a handle, in CSS pixels. */
export const HANDLE_RADIUS_PX = 8;
/** How close to a drawing's body a click must land to select it (the price-line radius, 5 px). */
export const BODY_TOLERANCE_PX = 5;
/** The drawn size of a handle square. */
export const HANDLE_SIZE_PX = 7;

/** What a pointer position hits on one drawing: a named handle (a drag starts), or its body (a click selects). */
export interface DrawingHit {
  /** The handle id (`"a"`, `"target"`, ...), or null for a hit on the body alone. */
  handle: string | null;
  distance: number;
}

/**
 * The one interface every editable drawing primitive implements (Story 32.5), so the chart has a
 * single hit-test and grab path for all of them: `hit` names the nearest handle within
 * `HANDLE_RADIUS_PX`, else the body within `BODY_TOLERANCE_PX`, else null.
 */
export interface DrawingPrimitive extends ISeriesPrimitive<Time> {
  hit(x: number, y: number): DrawingHit | null;
  /** Repaint from the current bar times (they changed: a new page, a timeframe's data). */
  refresh(): void;
}

/**
 * The time of every bar of the series the drawings are attached to, ascending, whitespace gap
 * slots included (they are series points, so they take a logical slot too). One instance per
 * chart, shared by its drawing primitives; the chart replaces `times` whenever its data does.
 * Anchors are drawn on the latest bar at or before their time (`snap`), never stored that way.
 */
export class BarGrid {
  times: readonly number[] = [];

  set(times: readonly number[]): void {
    this.times = times;
  }

  /**
   * The bar a time is drawn on; the time itself while there are no bars (nothing to snap to).
   *
   * Known limit: a time before the oldest loaded bar snaps to null, so a drawing anchored there
   * (a stored trendline, Fibonacci or position older than the loaded history) is not drawn until
   * the chart pages that bar in, even when part of it lies in the visible range. Upgrade path:
   * extrapolate an out-of-range time to a negative logical index from the bar interval and draw
   * through `logicalToCoordinate`, which the library allows past the data.
   */
  snap(time: number): number | null {
    if (this.times.length === 0) return time;
    const index = snapIndex(this.times, time);
    return index === null ? null : this.times[index];
  }

  indexOf(time: number): number | null {
    return snapIndex(this.times, time);
  }

  /** The bar time at a (fractional) logical index, clamped to the loaded bars. */
  timeAtLogical(logical: number): number | null {
    if (this.times.length === 0) return null;
    const index = Math.min(this.times.length - 1, Math.max(0, Math.round(logical)));
    return this.times[index];
  }
}

/** Pixel distance from (px, py) to the segment (ax, ay)-(bx, by). */
export function distanceToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** The nearest of `handles` within the grab radius, or null. */
export function nearestHandle(
  handles: readonly { id: string; x: number; y: number }[],
  x: number,
  y: number,
): DrawingHit | null {
  let best: DrawingHit | null = null;
  for (const h of handles) {
    const distance = Math.hypot(x - h.x, y - h.y);
    if (distance <= HANDLE_RADIUS_PX && (best === null || distance < best.distance)) {
      best = { handle: h.id, distance };
    }
  }
  return best;
}

/**
 * Story 33.10: the canvas dash pattern of a stored line style at a bitmap pixel `ratio` (`[]` is
 * solid). Any other value, which a hand-edited file could carry past the client, draws solid.
 */
export function lineDash(style: LineStyleName | undefined, ratio: number): number[] {
  if (style === "dashed") return [6 * ratio, 4 * ratio];
  if (style === "dotted") return [1.5 * ratio, 3 * ratio];
  return [];
}

/**
 * Story 33.10: the editable drawings' handle squares, in the bitmap space of a primitive's draw
 * (`hr`/`vr` the pixel ratios): filled with `fill`, outlined in the current stroke style.
 */
export function drawHandles(context: CanvasRenderingContext2D, points: readonly { x: number; y: number }[], fill: string, hr: number, vr: number): void {
  const half = (HANDLE_SIZE_PX / 2) * hr;
  context.fillStyle = fill;
  context.setLineDash([]);
  for (const p of points) {
    const x = p.x * hr - half;
    const y = p.y * vr - half;
    context.fillRect(x, y, half * 2, half * 2);
    context.strokeRect(x, y, half * 2, half * 2);
  }
}

/** The arrow head's sides: this long per pixel of line width, at most `ARROW_MAX_PX` (a 4 px line's
 * head stays an arrow head, not a fan), at this angle off the line. */
const ARROW_PX = 10;
export const ARROW_MAX_PX = 16;
const ARROW_ANGLE = Math.PI / 7;

/**
 * Story 33.10: two short sides at `to`, pointing back along `to`-`from` (an arrow's head, a range's
 * pointer), in the current stroke style; none for a zero-length line (no direction).
 */
export function drawArrowHead(
  context: CanvasRenderingContext2D,
  from: { x: number; y: number },
  to: { x: number; y: number },
  lineWidth: number,
  hr: number,
  vr: number,
): void {
  if (from.x === to.x && from.y === to.y) return;
  const back = Math.atan2(from.y - to.y, from.x - to.x);
  const side = Math.min(ARROW_PX * Math.max(1, lineWidth), ARROW_MAX_PX);
  context.setLineDash([]);
  for (const turn of [-ARROW_ANGLE, ARROW_ANGLE]) {
    context.beginPath();
    context.moveTo(to.x * hr, to.y * vr);
    context.lineTo((to.x + side * Math.cos(back + turn)) * hr, (to.y + side * Math.sin(back + turn)) * vr);
    context.stroke();
  }
}

/**
 * The distance from (x, y) to the box between two corners, for a body hit: the distance to its
 * border, at most `BODY_TOLERANCE_PX` anywhere inside it (so a line crossing a box is still the
 * nearer body on its own pixels). Null outside the tolerance.
 */
export function boxDistance(x: number, y: number, a: { x: number; y: number }, b: { x: number; y: number }): number | null {
  const left = Math.min(a.x, b.x);
  const right = Math.max(a.x, b.x);
  const top = Math.min(a.y, b.y);
  const bottom = Math.max(a.y, b.y);
  const inside = x >= left && x <= right && y >= top && y <= bottom;
  const dx = Math.max(left - x, 0, x - right);
  const dy = Math.max(top - y, 0, y - bottom);
  const border = inside ? Math.min(x - left, right - x, y - top, bottom - y) : Math.hypot(dx, dy);
  if (inside) return Math.min(border, BODY_TOLERANCE_PX);
  return border <= BODY_TOLERANCE_PX ? border : null;
}

/**
 * Story 33.10: the attach lifecycle of the click-placed drawings (vertical line, rectangle,
 * channel, text, ranges). The invariant it carries: a drawing's screen geometry is derived only
 * from its stored `{time, price}` anchors, recomputed on every `updateAllViews` (the library calls
 * it before each redraw), each anchor on the latest bar at or before its time (`BarGrid.snap`) --
 * never cached across a pan, zoom or new page, so the drawing cannot drift from its anchors.
 */
export abstract class AnchoredDrawingPrimitive<G> implements DrawingPrimitive {
  protected chart: IChartApi | null = null;
  protected series: ISeriesApi<"Candlestick" | "Line"> | null = null;
  protected geometry: G | null = null;
  protected handlesVisible = false;
  private requestUpdate: (() => void) | null = null;
  private readonly view: IPrimitivePaneView = {
    renderer: (): IPrimitivePaneRenderer | null => (this.geometry === null ? null : this.renderer(this.geometry)),
  };

  protected readonly grid: BarGrid;

  protected constructor(grid: BarGrid) {
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

  setHandlesVisible(visible: boolean): void {
    if (visible === this.handlesVisible) return;
    this.handlesVisible = visible;
    this.requestUpdate?.();
  }

  refresh(): void {
    this.requestUpdate?.();
  }

  updateAllViews(): void {
    this.geometry = this.chart && this.series ? this.compute() : null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** The current screen geometry, or `null` when not drawable -- exposed for tests. */
  screen(): G | null {
    return this.geometry;
  }

  abstract hit(x: number, y: number): DrawingHit | null;

  /** Ask the library for a repaint (the drawing changed). */
  protected changed(): void {
    this.requestUpdate?.();
  }

  /** An anchor's screen point, or null while it has none (scrolled out, older than every bar). */
  protected pointOf(anchor: Anchor): { x: number; y: number } | null {
    const x = this.xOf(anchor.time);
    const y = this.series?.priceToCoordinate(anchor.price) ?? null;
    return x === null || y === null ? null : { x, y };
  }

  protected xOf(time: number): number | null {
    const snapped = this.grid.snap(time);
    return snapped === null ? null : (this.chart?.timeScale().timeToCoordinate(snapped as Time) ?? null);
  }

  protected priceY(price: number): number | null {
    return this.series?.priceToCoordinate(price) ?? null;
  }

  protected abstract compute(): G | null;

  protected abstract renderer(geometry: G): IPrimitivePaneRenderer;
}

/** The type of a pane renderer's draw target (fancy-canvas, a transitive dependency). */
export type DrawTarget = Parameters<IPrimitivePaneRenderer["draw"]>[0];
