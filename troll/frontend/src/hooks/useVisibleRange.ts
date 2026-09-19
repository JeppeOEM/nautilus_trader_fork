import type { IChartApi, Time } from "lightweight-charts";
import { useEffect, useState } from "react";

export interface VisibleTimeRange {
  from: number;
  to: number;
}

/**
 * The chart's visible time range as UTC seconds, re-reported on every pan/zoom
 * (`subscribeVisibleTimeRangeChange`). `null` until the chart has one (no data yet) or
 * while `chart` is null. Story 18.7's VRVP is the consumer -- the "always recompute"
 * trigger; nothing else in the app needs it.
 */
export function useVisibleRange(chart: IChartApi | null): VisibleTimeRange | null {
  const [range, setRange] = useState<VisibleTimeRange | null>(null);

  useEffect(() => {
    if (!chart) return;
    const timeScale = chart.timeScale();
    const apply = (next: { from: Time; to: Time } | null): void => {
      // Same range reported twice (the library also fires on a plain resize) must not
      // re-render its consumers.
      setRange((prev) => {
        if (next === null) return prev === null ? prev : null;
        const from = next.from as number;
        const to = next.to as number;
        return prev && prev.from === from && prev.to === to ? prev : { from, to };
      });
    };
    timeScale.subscribeVisibleTimeRangeChange(apply);
    // The subscription only fires on a CHANGE: seed with whatever is visible right now.
    apply(timeScale.getVisibleRange());
    return () => timeScale.unsubscribeVisibleTimeRangeChange(apply);
  }, [chart]);

  return chart ? range : null;
}
