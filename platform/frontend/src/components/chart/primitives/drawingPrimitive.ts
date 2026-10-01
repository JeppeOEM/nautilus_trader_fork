import type { ISeriesPrimitive, Time } from "lightweight-charts";

import { snapIndex } from "../../../lib/drawings";

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
