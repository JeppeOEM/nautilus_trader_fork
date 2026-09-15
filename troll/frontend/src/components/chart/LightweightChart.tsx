import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPaneApi,
  type ISeriesApi,
  type LineData,
  type Time,
  type WhitespaceData,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { ChartDatum } from "../../hooks/useCandles";
import type { IndicatorDatum } from "../../hooks/useIndicatorSeries";

export type PaneSeriesKind = "Line" | "Histogram";

export interface IndicatorPaneSpec {
  /** Registry key -- `dashboard.py`'s `_indicator_id(name, params)` scheme (e.g.
   * `"MultiLevelOFI"`, `"volume"`). Never reused across two conceptually different data
   * sources (see Story 15.4's naming-collision warning re: `custom_indicators.py`'s
   * unrelated `"OrderFlowImbalance"` id). */
  id: string;
  kind: PaneSeriesKind;
  data: IndicatorDatum[];
  color: string;
}

interface LightweightChartProps {
  data: ChartDatum[];
  /** Notified with the chart instance right after creation, and with `null` right
   * before it is torn down -- lets a caller (e.g. `useCandles`) subscribe to
   * `chart.timeScale()` without this component needing to know about pagination. */
  onChartApi: (chart: IChartApi | null) => void;
  /** Declarative pane set (Story 15.4, AD-F4) -- the ONLY way indicator sub-panes are
   * added/removed/updated; no caller ever calls `chart.addPane()`/`chart.addSeries()`
   * itself (`Map<string, IPaneApi>` registry lives entirely inside this component). This
   * component diffs `panes` against its own internal registry on every change: ids no
   * longer present are removed via `chart.removePane()`, new ids get a fresh
   * `chart.addPane()` + `chart.addSeries(..., pane.paneIndex())`, and ids that stayed
   * just get their series' data/color updated in place. None of that ever calls
   * `chart.timeScale().setVisibleLogicalRange(...)` (AC #4 -- adding/removing/
   * reconfiguring a pane must never reset the chart's current zoom/pan). */
  panes?: IndicatorPaneSpec[];
}

type AnySeriesApi = ISeriesApi<"Line", Time> | ISeriesApi<"Histogram", Time>;

interface PaneEntry {
  pane: IPaneApi<Time>;
  series: AnySeriesApi;
  /** The last `spec.data` reference applied to `series`, so an unrelated pane's data
   * refresh doesn't force every other still-visible pane to re-run `setData()` too --
   * `panes` is rebuilt by the caller's own `useMemo` on every candle/indicator page
   * fetch, not just the one series that actually changed. */
  lastData: IndicatorDatum[];
}

function setSeriesData(series: AnySeriesApi, data: IndicatorDatum[]): void {
  // Both `Line` and `Histogram` series accept LineData-shaped points (Histogram's own
  // `color` per-point field is optional) -- one cast site here, not a runtime branch
  // per series kind.
  (series.setData as (d: (LineData<Time> | WhitespaceData<Time>)[]) => void)(data);
}

/**
 * Owns the one `lightweight-charts` `createChart()` call for a coin's chart page
 * (AD-F4) -- candlestick series plus, since Story 15.4, a keyed registry of indicator
 * sub-panes (OFI/OBI/microprice/spread/volume). Mounts and unmounts cleanly: `ChartPage`
 * remounts this component (via `key={iid}`) on instrument change rather than re-pointing
 * one long-lived instance, since `lightweight-charts` has no supported "re-point this
 * chart at different data" API.
 */
export default function LightweightChart({ data, onChartApi, panes = [] }: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const prevLengthRef = useRef(0);
  const panesRef = useRef<Map<string, PaneEntry>>(new Map());

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, { width: container.clientWidth, height: 500 });
    chartRef.current = chart;
    seriesRef.current = chart.addSeries(CandlestickSeries);
    onChartApi(chart);

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);
    const panes = panesRef.current;

    return () => {
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      seriesRef.current = null;
      panes.clear();
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

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const registry = panesRef.current;
    const specsById = new Map(panes.map((spec) => [spec.id, spec] as const));

    // Remove ids no longer present first -- never touches timeScale/visible range, just
    // `chart.removePane()` (AC #4).
    for (const [id, entry] of [...registry]) {
      if (!specsById.has(id)) {
        chart.removePane(entry.pane.paneIndex());
        registry.delete(id);
      }
    }

    // Add new ids / update data+color for ids that stayed -- again, no visible-range
    // call anywhere in this branch.
    for (const spec of panes) {
      let entry = registry.get(spec.id);
      if (!entry) {
        const pane = chart.addPane();
        const definition = spec.kind === "Line" ? LineSeries : HistogramSeries;
        const series = chart.addSeries(definition, { color: spec.color }, pane.paneIndex()) as AnySeriesApi;
        entry = { pane, series, lastData: spec.data };
        registry.set(spec.id, entry);
        setSeriesData(entry.series, spec.data);
        continue;
      }
      if (entry.series.options().color !== spec.color) {
        entry.series.applyOptions({ color: spec.color });
      }
      if (entry.lastData !== spec.data) {
        setSeriesData(entry.series, spec.data);
        entry.lastData = spec.data;
      }
    }
  }, [panes]);

  return <div ref={containerRef} />;
}
