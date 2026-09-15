import { CandlestickSeries, createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { ChartDatum } from "../../hooks/useCandles";

interface LightweightChartProps {
  data: ChartDatum[];
  /** Notified with the chart instance right after creation, and with `null` right
   * before it is torn down -- lets a caller (e.g. `useCandles`) subscribe to
   * `chart.timeScale()` without this component needing to know about pagination. */
  onChartApi: (chart: IChartApi | null) => void;
}

/**
 * Owns the one `lightweight-charts` `createChart()` call for a coin's chart page
 * (AD-F4) -- candlestick series only; indicator panes are Story 15.4's job. Mounts and
 * unmounts cleanly: `ChartPage` remounts this component (via `key={iid}`) on instrument
 * change rather than re-pointing one long-lived instance, since `lightweight-charts` has
 * no supported "re-point this chart at different data" API.
 */
export default function LightweightChart({ data, onChartApi }: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const prevLengthRef = useRef(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, { width: container.clientWidth, height: 500 });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries);
    onChartApi(chart);

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      seriesRef.current = null;
      onChartApi(null);
      chart.remove();
    };
    // Mount-only: exactly one createChart()/addSeries() call for this component's
    // lifetime (AD-F4) -- `onChartApi` is a state setter passed by the caller, stable
    // across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series) return;

    // A scroll-back refill prepends older bars in front of the existing array, which
    // shifts every already-visible bar's logical index forward by the number of newly
    // added bars. setData() replaces the whole dataset and does not itself compensate
    // for that shift, so without this the view snaps to a different set of bars every
    // time older history loads -- defeating the point of scroll-back pagination
    // (AC #3). Only applies once there was a previous, non-empty dataset (the initial
    // load has no prior view to preserve).
    const addedAtFront = prevLengthRef.current > 0 ? data.length - prevLengthRef.current : 0;
    const rangeBeforeUpdate = addedAtFront > 0 ? chart?.timeScale().getVisibleLogicalRange() : null;

    series.setData(data);
    prevLengthRef.current = data.length;

    if (rangeBeforeUpdate && chart) {
      chart.timeScale().setVisibleLogicalRange({
        from: rangeBeforeUpdate.from + addedAtFront,
        to: rangeBeforeUpdate.to + addedAtFront,
      });
    }
  }, [data]);

  return <div ref={containerRef} />;
}
