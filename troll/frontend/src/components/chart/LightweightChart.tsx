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
import type { SnapshotLinesData } from "../../hooks/useSnapshotSeries";
import { assignPaneColor, cssVar } from "./paneColors";

export type PaneSeriesKind = "Line" | "Histogram";

export type ChartMode = "candles" | "lines";

// Story 15.7's 5 main-pane line series, in a fixed order -- also the color-slot
// assignment order (mirrors DEFAULT_PANE_IDS' role for indicator sub-panes).
const LINE_SERIES_IDS = ["bid", "ask", "mid", "micro", "price"] as const;
type LineSeriesId = (typeof LINE_SERIES_IDS)[number];

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
  /** Story 15.7: which data source currently owns the main pane. Defaults to `"candles"`
   * (every pre-15.7 caller/test omits this prop). Switching `mode` removes the previous
   * mode's main-pane series and adds the new mode's, on the SAME main pane (index 0) --
   * it never calls `chart.timeScale().setVisibleLogicalRange()`/`fitContent()` (AC #3),
   * the exact "add/remove never resets the view" discipline Story 15.4's indicator-pane
   * registry already established, reused here for a main-pane series swap instead of a
   * sub-pane add/remove. */
  mode?: ChartMode;
  data: ChartDatum[];
  /** Story 15.7: bid/ask/mid/micro/price rows for Lines mode (`useSnapshotSeries`) --
   * only read while `mode === "lines"`; ignored otherwise, same as `data`/`liveBar` are
   * ignored while `mode === "lines"`. */
  linesData?: SnapshotLinesData;
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
  /** Story 15.5: the currently-forming candle bar, from `useLiveCandle`. Applied via
   * `series.update()` (not `setData()`) on the candlestick series only -- independent of
   * the `data`/`setData()` effect above and Story 15.4's `panes` effect below; neither of
   * those is touched by this prop. `null`/`undefined` means "no live bar yet" (e.g.
   * before the live socket's first message, or synchronously reset on instrument/bar-size
   * change) and is a no-op, not a clear of the last-drawn bar. */
  liveBar?: ChartDatum | null;
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

type MainLineSeriesApi = ISeriesApi<"Line", Time>;

/**
 * Owns the one `lightweight-charts` `createChart()` call for a coin's chart page
 * (AD-F4) -- the main pane (candlestick series in Candles mode, or 5 line series in Lines
 * mode, Story 15.7) plus, since Story 15.4, a keyed registry of indicator sub-panes
 * (OFI/OBI/microprice/spread/volume). Mounts and unmounts cleanly: `ChartPage` remounts
 * this component (via `key={iid}`) on instrument change rather than re-pointing one
 * long-lived instance, since `lightweight-charts` has no supported "re-point this chart at
 * different data" API.
 */
export default function LightweightChart({
  mode = "candles",
  data,
  linesData,
  onChartApi,
  panes = [],
  liveBar,
}: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lineSeriesRef = useRef<Record<LineSeriesId, MainLineSeriesApi> | null>(null);
  const prevLengthRef = useRef(0);
  const prevLinesLengthRef = useRef(0);
  const panesRef = useRef<Map<string, PaneEntry>>(new Map());
  const lastLiveBarTimeRef = useRef<number | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Story 15.9: lightweight-charts' own defaults aren't VGA-derived -- every one of
    // background/text/grid/crosshair is explicitly set from the semantic tokens so the
    // chart itself doesn't stay the one non-conforming element on an otherwise-restyled
    // page.
    const chart = createChart(container, {
      width: container.clientWidth,
      height: 500,
      layout: {
        background: { color: cssVar("--color-bg", "#000000") },
        textColor: cssVar("--color-text", "#aaaaaa"),
        fontFamily: cssVar("--font-terminal", "monospace"),
      },
      grid: {
        vertLines: { color: cssVar("--color-border", "#555555") },
        horzLines: { color: cssVar("--color-border", "#555555") },
      },
      crosshair: {
        vertLine: {
          color: cssVar("--color-active", "#55ffff"),
          labelBackgroundColor: cssVar("--color-active-bg", "#0000aa"),
        },
        horzLine: {
          color: cssVar("--color-active", "#55ffff"),
          labelBackgroundColor: cssVar("--color-active-bg", "#0000aa"),
        },
      },
      // Both scales default to a non-token gray border line (library default
      // '#2B2B43') -- override explicitly, same as grid/crosshair above.
      rightPriceScale: { borderColor: cssVar("--color-border", "#555555") },
      timeScale: { borderColor: cssVar("--color-border", "#555555") },
    });
    chartRef.current = chart;
    onChartApi(chart);

    // Canvas text (unlike DOM text) never re-flows on its own once a `@font-face`
    // finishes loading -- if the webfont is still pending when `createChart()` reads
    // `--font-terminal`'s computed value above, the price/time-scale labels are drawn
    // with the fallback font and stay that way until something explicitly reapplies
    // the option. `document.fonts.ready` resolves once, is a no-op if already
    // resolved, and this effect only runs once per mount, so this can't loop.
    let cancelled = false;
    void document.fonts?.ready?.then(() => {
      if (cancelled || chartRef.current !== chart) return;
      chart.applyOptions({ layout: { fontFamily: cssVar("--font-terminal", "monospace") } });
    });

    const handleResize = () => chart.applyOptions({ width: container.clientWidth });
    window.addEventListener("resize", handleResize);
    const panes = panesRef.current;

    return () => {
      cancelled = true;
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      seriesRef.current = null;
      lineSeriesRef.current = null;
      panes.clear();
      onChartApi(null);
      chart.remove();
    };
    // Mount-only: exactly one createChart() call for this component's lifetime (AD-F4) --
    // `onChartApi` is a state setter passed by the caller, stable across renders. Main-pane
    // series creation itself lives in the `[mode]` effect below, not here, so it can run
    // again on a Candles<->Lines toggle without a second createChart() call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // The main-pane series-identity swap (Story 15.7, AC #1/#3): removes whichever main-
    // pane series belonged to the PREVIOUS mode and adds the new mode's, on the same main
    // pane (index 0) -- never calls setVisibleLogicalRange()/fitContent() (same "add/
    // remove never resets the view" discipline as the indicator-pane registry effect
    // below). Guarded so a data-only re-render (mode unchanged) never removes/recreates
    // these series -- only an actual mode change does.
    const chart = chartRef.current;
    if (!chart) return;

    if (mode === "candles") {
      if (lineSeriesRef.current) {
        for (const id of LINE_SERIES_IDS) chart.removeSeries(lineSeriesRef.current[id]);
        lineSeriesRef.current = null;
        prevLinesLengthRef.current = 0;
      }
      if (!seriesRef.current) {
        // Up/down candle colors explicitly from the semantic tokens (AC #4/#1) --
        // lightweight-charts' own default green/red is not VGA-derived.
        const up = cssVar("--color-up", "#55ff55");
        const down = cssVar("--color-down", "#ff5555");
        const dim = cssVar("--color-text-dim", "#555555");
        seriesRef.current = chart.addSeries(CandlestickSeries, {
          upColor: up,
          downColor: down,
          borderUpColor: up,
          borderDownColor: down,
          wickUpColor: up,
          wickDownColor: down,
          // The base borderColor/wickColor fields (as opposed to the Up/Down
          // variants above) are vestigial fallbacks whose library defaults
          // (`#378658`/`#737375`) are otherwise never overridden -- set
          // explicitly so nothing non-token-derived can ever render.
          borderColor: dim,
          wickColor: dim,
        });
        prevLengthRef.current = 0;
      }
    } else {
      if (seriesRef.current) {
        chart.removeSeries(seriesRef.current);
        seriesRef.current = null;
        prevLengthRef.current = 0;
      }
      if (!lineSeriesRef.current) {
        const lineIds = [...LINE_SERIES_IDS];
        const entries = LINE_SERIES_IDS.map(
          (id) => [id, chart.addSeries(LineSeries, { color: assignPaneColor(id, lineIds) }, 0)] as const,
        );
        lineSeriesRef.current = Object.fromEntries(entries) as Record<LineSeriesId, MainLineSeriesApi>;
        prevLinesLengthRef.current = 0;
      }
    }
  }, [mode]);

  useEffect(() => {
    if (mode !== "candles") return;
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
  }, [data, mode]);

  useEffect(() => {
    if (mode !== "lines") return;
    const series = lineSeriesRef.current;
    const chart = chartRef.current;
    if (!series) return;
    const rows = linesData ?? { bid: [], ask: [], mid: [], micro: [], price: [] };

    // Same scroll-back shift-preservation as the candlestick data effect above -- all 5
    // arrays are always the same length (built together by `_price_series_rows`/
    // `useSnapshotSeries`), so one representative length/shift computation covers all 5.
    const addedAtFront = prevLinesLengthRef.current > 0 ? rows.bid.length - prevLinesLengthRef.current : 0;
    const rangeBeforeUpdate = addedAtFront > 0 ? chart?.timeScale().getVisibleLogicalRange() : null;

    for (const id of LINE_SERIES_IDS) series[id].setData(rows[id]);
    prevLinesLengthRef.current = rows.bid.length;

    if (rangeBeforeUpdate && chart) {
      chart.timeScale().setVisibleLogicalRange({
        from: rangeBeforeUpdate.from + addedAtFront,
        to: rangeBeforeUpdate.to + addedAtFront,
      });
    }
  }, [linesData, mode]);

  useEffect(() => {
    // Story 15.5's live edge: independent of the mount effect, the data/setData() effects,
    // and the panes effect below -- touches only seriesRef, via update() rather than a
    // full setData() (AD-F7: the frontend never re-aggregates, it just paints the
    // already-aggregated forming bar `useLiveCandle` handed it). A no-op in Lines mode
    // (`seriesRef.current` is `null` there, see the `[mode]` effect above) -- Lines mode
    // has no live-edge concept of its own (Task 3's Dev Note: this toggle only ever swaps
    // the main pane's historical series).
    if (!liveBar) return;
    const series = seriesRef.current;
    if (!series) return;
    // lightweight-charts requires non-decreasing update() times; a live bar racing in
    // behind the last-applied one (e.g. a stray message right after an instrument/
    // bar-size switch) would otherwise throw and silently kill all further live updates.
    const time = liveBar.time as unknown as number;
    if (lastLiveBarTimeRef.current !== null && time < lastLiveBarTimeRef.current) return;
    lastLiveBarTimeRef.current = time;
    series.update(liveBar);
    // `mode` is a dependency too: switching Lines -> Candles recreates seriesRef (the
    // `[mode]` effect above) with no data yet, so this must re-fire to paint the already-
    // held `liveBar` onto the fresh series -- otherwise the forming bar stays blank until
    // the next websocket tick.
  }, [liveBar, mode]);

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
