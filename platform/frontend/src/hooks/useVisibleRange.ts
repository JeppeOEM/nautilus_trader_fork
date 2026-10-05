import type { IChartApi } from "lightweight-charts";
import { useEffect, useState } from "react";

export interface VisibleTimeRange {
  from: number;
  to: number;
  /** The view reaches left of the oldest loaded bar (logical `from` < -0.5): `from`/`to` then
   * clamp to the loaded bars, so a profile of them covers less than what the operator sees. */
  pastOldest: boolean;
}

// Bar 0's left half still belongs to bar 0: only a view that starts further left than that shows
// empty space before the oldest bar.
const OLDEST_BAR_LEFT_EDGE = -0.5;

/**
 * The chart's visible time range as UTC seconds, re-read on every pan/zoom. `null` until the
 * chart has one (no data yet) or while `chart` is null. Story 18.7's VRVP is the consumer -- the
 * "always recompute" trigger; nothing else in the app needs it.
 *
 * DW-151: a pan or a kinetic scroll reports a change per pointer event, several per frame, and the
 * VRVP rebuilds its whole profile on each; the reports are coalesced into one read per animation
 * frame. Both ranges are subscribed to: the logical one also changes when older bars are
 * prepended under an unchanged view (exactly when `pastOldest` can flip), the time one when new
 * data lands under unchanged indices (a replay step into right whitespace, a same-length
 * `setData`). One frame's worth of either is one read.
 */
export function useVisibleRange(chart: IChartApi | null): VisibleTimeRange | null {
  const [range, setRange] = useState<VisibleTimeRange | null>(null);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    let frame: number | null = null;
    const read = (): void => {
      frame = null;
      const next = timeScale.getVisibleRange();
      const logical = timeScale.getVisibleLogicalRange();
      // Same range reported twice (the library also fires on a plain resize) must not
      // re-render its consumers.
      setRange((prev) => {
        if (next === null) return prev === null ? prev : null;
        const from = next.from as number;
        const to = next.to as number;
        const pastOldest = logical !== null && logical.from < OLDEST_BAR_LEFT_EDGE;
        return prev && prev.from === from && prev.to === to && prev.pastOldest === pastOldest
          ? prev
          : { from, to, pastOldest };
      });
    };
    const schedule = (): void => {
      if (frame === null) frame = requestAnimationFrame(read);
    };
    timeScale.subscribeVisibleLogicalRangeChange(schedule);
    timeScale.subscribeVisibleTimeRangeChange(schedule);
    // The subscription only fires on a CHANGE: seed with whatever is visible right now.
    read();
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(schedule);
      timeScale.unsubscribeVisibleTimeRangeChange(schedule);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [chart]);

  return chart ? range : null;
}
