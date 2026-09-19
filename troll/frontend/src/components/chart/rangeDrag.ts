import type { IChartApi, ISeriesApi } from "lightweight-charts";

import type { TrendlineAnchor } from "./primitives/TrendlinePrimitive";

export interface RangeDragHandlers {
  /** Every mouse-move once a drag started, with the drag's start and the current point. */
  onMove: (start: TrendlineAnchor, end: TrendlineAnchor) => void;
  /** Left-button release; `last` is null when the press never moved (a plain click). */
  onRelease: (last: { start: TrendlineAnchor; end: TrendlineAnchor } | null) => void;
}

/**
 * Shared click-drag plumbing for the range tools (measurement 18.3, fixed-range volume
 * profile 18.6). Capture-phase mousedown with stopPropagation() keeps the library from
 * starting a pan (only once a start point resolved, so a press past the data range still
 * pans); move/up are window-level so a drag can end outside the chart. Returns the
 * cleanup, which removes the listeners and is also the Esc path (callers flip their
 * active flag false).
 */
export function attachRangeDrag(
  container: HTMLElement,
  chart: IChartApi,
  host: ISeriesApi<"Candlestick">,
  handlers: RangeDragHandlers,
): () => void {
  let start: TrendlineAnchor | null = null;
  let last: { start: TrendlineAnchor; end: TrendlineAnchor } | null = null;

  const pointFrom = (event: MouseEvent): TrendlineAnchor | null => {
    const box = container.getBoundingClientRect();
    const time = chart.timeScale().coordinateToTime(event.clientX - box.left);
    const price = host.coordinateToPrice(event.clientY - box.top);
    return time === null || price === null ? null : { time, price };
  };

  const handleMouseDown = (event: MouseEvent): void => {
    // `start` still set = a release was lost (window blur); ignore rather than restart.
    if (event.button !== 0 || start) return;
    start = pointFrom(event);
    if (start) event.stopPropagation();
  };

  const handleMouseMove = (event: MouseEvent): void => {
    if (!start) return;
    // No button held = the release happened outside the window (blur): finish the drag
    // instead of leaving it stuck to the cursor.
    if (event.buttons === 0) {
      finish();
      return;
    }
    const end = pointFrom(event);
    if (!end) return;
    last = { start, end };
    handlers.onMove(start, end);
  };

  const finish = (): void => {
    const finished = last;
    start = null;
    last = null;
    handlers.onRelease(finished);
  };

  const handleMouseUp = (event: MouseEvent): void => {
    if (!start || event.button !== 0) return;
    finish();
  };

  container.addEventListener("mousedown", handleMouseDown, true);
  window.addEventListener("mousemove", handleMouseMove);
  window.addEventListener("mouseup", handleMouseUp);
  return () => {
    container.removeEventListener("mousedown", handleMouseDown, true);
    window.removeEventListener("mousemove", handleMouseMove);
    window.removeEventListener("mouseup", handleMouseUp);
  };
}
