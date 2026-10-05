import type { IChartApi, ISeriesApi, Time } from "lightweight-charts";

import type { BarGrid } from "./primitives/drawingPrimitive";
import type { TrendlineAnchor } from "./primitives/TrendlinePrimitive";

export interface RangeDragHandlers {
  /** Every pointer move once a drag started, with the drag's start and the current point. */
  onMove: (start: TrendlineAnchor, end: TrendlineAnchor) => void;
  /** Release (left button / touch end); `last` is null when the press never moved (a plain click). */
  onRelease: (last: { start: TrendlineAnchor; end: TrendlineAnchor } | null) => void;
}

export interface PlotPoint {
  /** Px from the left edge of the time scale (the coordinate space `timeScale()` converts). */
  x: number;
  /** Px from the top of the price pane (the space its series' `coordinateToPrice` converts). */
  y: number;
}

/** The plot-local point of a client position, wherever it lies (moves may leave the plot). */
export function localPoint(container: HTMLElement, chart: IChartApi, clientX: number, clientY: number): PlotPoint {
  const box = container.getBoundingClientRect();
  return { x: clientX - box.left - chart.priceScale("left").width(), y: clientY - box.top };
}

/**
 * DW-144: the plot-local point of a press, or null when it falls outside the price pane's plot
 * area -- on the price/time axis strips, in an indicator pane, or before the chart was laid out
 * (pane height 0). A press there belongs to the library (axis scaling, pane resize), never to a
 * drawing tool.
 */
export function plotPoint(container: HTMLElement, chart: IChartApi, clientX: number, clientY: number): PlotPoint | null {
  const height = chart.panes()[0]?.getHeight() ?? 0;
  if (height <= 0) return null;
  const point = localPoint(container, chart, clientX, clientY);
  const inside = point.x >= 0 && point.x < chart.timeScale().width() && point.y >= 0 && point.y < height;
  return inside ? point : null;
}

/**
 * DW-143/DW-150: the time under a plot x. `coordinateToTime` is null past the newest bar (and before the
 * oldest), which froze every drag at the last valid point; the fallback clamps the logical index
 * to the loaded bars plus the forming one, the same mapping the drawing-handle drag uses.
 */
export function timeAtX(chart: IChartApi, grid: BarGrid, x: number): Time | null {
  const timeScale = chart.timeScale();
  const time = timeScale.coordinateToTime(x);
  if (time !== null) return time;
  const logical = timeScale.coordinateToLogical(x);
  return logical === null ? null : (grid.timeAtLogical(logical) as Time | null);
}

/**
 * Shared click-drag plumbing for the range tools (measurement 18.3, fixed-range volume
 * profile 18.6, Fibonacci 32.5). Capture-phase mousedown/touchstart with stopPropagation()
 * keeps the library from starting a pan (only once a start point resolved inside the price
 * pane's plot, so a press past the data range still pans); move/up are window-level so a drag
 * can end outside the chart. Mouse and touch share one drag model, each drag owned by the
 * input that started it. Returns the cleanup, which removes the listeners and is also the Esc
 * path (callers flip their active flag false).
 */
export function attachRangeDrag(
  container: HTMLElement,
  chart: IChartApi,
  host: ISeriesApi<"Candlestick" | "Line">,
  grid: BarGrid,
  handlers: RangeDragHandlers,
): () => void {
  let start: TrendlineAnchor | null = null;
  let source: "mouse" | "touch" | null = null;
  // The finger that started a touch drag: every later touch event is read for it alone.
  let touchId: number | null = null;
  let last: { start: TrendlineAnchor; end: TrendlineAnchor } | null = null;

  // A press needs a real bar under it: an empty-margin press keeps panning the chart.
  const pressAt = (clientX: number, clientY: number): TrendlineAnchor | null => {
    const point = plotPoint(container, chart, clientX, clientY);
    if (!point) return null;
    const time = chart.timeScale().coordinateToTime(point.x);
    const price = host.coordinateToPrice(point.y);
    return time === null || price === null ? null : { time, price };
  };

  const moveTo = (clientX: number, clientY: number): void => {
    if (!start) return;
    const point = localPoint(container, chart, clientX, clientY);
    const time = timeAtX(chart, grid, point.x);
    const price = host.coordinateToPrice(point.y);
    if (time === null || price === null) return;
    const end = { time, price };
    last = { start, end };
    handlers.onMove(start, end);
  };

  const finish = (): void => {
    const finished = last;
    if (source === "touch") detachTouchDrag();
    start = null;
    source = null;
    touchId = null;
    last = null;
    handlers.onRelease(finished);
  };

  const handleMouseDown = (event: MouseEvent): void => {
    // `start` still set = a release was lost (window blur); ignore rather than restart.
    if (event.button !== 0 || start) return;
    start = pressAt(event.clientX, event.clientY);
    if (!start) return;
    source = "mouse";
    event.stopPropagation();
  };

  const handleMouseMove = (event: MouseEvent): void => {
    if (source !== "mouse") return;
    // No button held = the release happened outside the window (blur): finish the drag
    // instead of leaving it stuck to the cursor.
    if (event.buttons === 0) {
      finish();
      return;
    }
    moveTo(event.clientX, event.clientY);
  };

  const handleMouseUp = (event: MouseEvent): void => {
    if (source !== "mouse" || event.button !== 0) return;
    finish();
  };

  const trackedTouch = (list: TouchList): Touch | null => {
    for (const touch of Array.from(list)) if (touch.identifier === touchId) return touch;
    return null;
  };

  const handleTouchStart = (event: TouchEvent): void => {
    if (source === "touch") {
      // The dragging finger's end was lost (its node replaced mid-gesture): finish like a lost release.
      if (!trackedTouch(event.touches)) finish();
      else if (last === null) {
        // A second finger before the first one moved is a pinch whose fingers landed apart (they
        // rarely land in one event): drop the unmoved drag, a plain click to every caller, and let
        // the library start its pinch from this event -- the same rule as its own "move before
        // the second touch prevents the pinch".
        finish();
        return;
      } else {
        // A second finger mid-drag would start the library's pinch under the drag.
        event.stopPropagation();
        event.preventDefault();
        return;
      }
    }
    // Two fingers down at once (a pinch) are the library's, never a drag start.
    if (start || event.touches.length !== 1) return;
    const touch = event.touches[0];
    start = pressAt(touch.clientX, touch.clientY);
    if (!start) return;
    source = "touch";
    touchId = touch.identifier;
    attachTouchDrag();
    event.stopPropagation(); // no library pan
    event.preventDefault(); // no emulated mouse events, which would start a second drag
  };

  const handleTouchMove = (event: TouchEvent): void => {
    event.preventDefault(); // no page scroll while dragging
    const touch = trackedTouch(event.touches);
    if (touch) moveTo(touch.clientX, touch.clientY);
  };

  const handleTouchEnd = (event: TouchEvent): void => {
    // Another finger lifting is not this drag's release.
    if (!trackedTouch(event.changedTouches)) return;
    event.preventDefault();
    finish();
  };

  // A cancelled touch (the OS took the gesture) is a lost release: finish like the mouse does.
  const handleTouchCancel = (event: TouchEvent): void => {
    if (trackedTouch(event.changedTouches)) finish();
  };

  // Window-level and non-passive only while a touch drag runs: an armed tool must not make every
  // touch-scroll elsewhere on the page wait for the main thread.
  const touchOptions: AddEventListenerOptions = { passive: false };
  function attachTouchDrag(): void {
    window.addEventListener("touchmove", handleTouchMove, touchOptions);
    window.addEventListener("touchend", handleTouchEnd, touchOptions);
    window.addEventListener("touchcancel", handleTouchCancel);
  }
  function detachTouchDrag(): void {
    window.removeEventListener("touchmove", handleTouchMove);
    window.removeEventListener("touchend", handleTouchEnd);
    window.removeEventListener("touchcancel", handleTouchCancel);
  }

  container.addEventListener("mousedown", handleMouseDown, true);
  container.addEventListener("touchstart", handleTouchStart, { capture: true, passive: false });
  window.addEventListener("mousemove", handleMouseMove);
  window.addEventListener("mouseup", handleMouseUp);
  return () => {
    container.removeEventListener("mousedown", handleMouseDown, true);
    container.removeEventListener("touchstart", handleTouchStart, true);
    window.removeEventListener("mousemove", handleMouseMove);
    window.removeEventListener("mouseup", handleMouseUp);
    detachTouchDrag();
  };
}
