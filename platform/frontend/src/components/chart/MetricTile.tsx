import {
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type Time,
  type WhitespaceData,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { cssVar } from "./paneColors";

export type MetricDatum = LineData<Time> | WhitespaceData<Time>;

interface MetricTileProps {
  label: string;
  data: MetricDatum[];
}

/**
 * One independent, small `createChart()` + single line series per metric column (Story
 * 17.2/15.8, AC #2/#3) -- deliberately NOT `LightweightChart.tsx`: that component's
 * pane-sync/candlestick/live-edge registry solves a different problem (N series sharing
 * one time axis, panned and zoomed together, Story 15.4) that these small-multiples
 * tiles have no need for (Dev Notes: "resist importing any of Story 15.4's pane-sync
 * machinery here"). Mounts and unmounts cleanly per tile, same "one createChart() call
 * per instance lifetime" discipline, just without any cross-tile registry.
 */
export default function MetricTile({ label, data }: MetricTileProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Line", Time> | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Same VGA-derived token set as LightweightChart.tsx's own createChart() call
    // (Story 15.9 visual identity) -- kept visually consistent without importing that
    // component's pane-sync/candle machinery.
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 180,
      layout: {
        background: { color: cssVar("--color-bg", "#000000") },
        textColor: cssVar("--color-text", "#aaaaaa"),
        fontFamily: cssVar("--font-terminal", "monospace"),
      },
      grid: {
        vertLines: { color: cssVar("--color-border", "#555555") },
        horzLines: { color: cssVar("--color-border", "#555555") },
      },
      rightPriceScale: { borderColor: cssVar("--color-border", "#555555") },
      timeScale: { borderColor: cssVar("--color-border", "#555555") },
    });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(LineSeries, { color: cssVar("--vga-light-cyan", "#55ffff") });

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      seriesRef.current = null;
      chart.remove();
    };
    // Mount-only: exactly one createChart() call for this tile's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    seriesRef.current?.setData(data);
  }, [data]);

  return (
    <div className="term-box" data-label={label}>
      <div ref={containerRef} />
    </div>
  );
}
