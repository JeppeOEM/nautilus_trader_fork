import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPaneApi,
  type IPriceLine,
  type ISeriesApi,
  type LineData,
  type MouseEventParams,
  type Time,
  type WhitespaceData,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { ChartDatum, VolumeDatum } from "../../hooks/useCandles";
import type { IndicatorDatum } from "../../hooks/useIndicatorSeries";
import type { SnapshotLinesData } from "../../hooks/useSnapshotSeries";
import { assignPaneColor, cssVar } from "./paneColors";
import {
  MeasurementPrimitive,
  computeMeasurement,
  formatMeasurement,
} from "./primitives/MeasurementPrimitive";
import { attachRangeDrag } from "./rangeDrag";
import { VolumeProfilePrimitive, type VolumeProfileRenderSpec } from "./primitives/VolumeProfilePrimitive";
import { VerticalMarkerPrimitive } from "./primitives/VerticalMarkerPrimitive";
import { TrendlinePrimitive, type TrendlineAnchor } from "./primitives/TrendlinePrimitive";

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

// Story 18.1: a tool-drawn horizontal price line (AC #2). `id` is the caller's stable
// key (ChartPage uses deterministic "hline-N" counter ids), diffed exactly like
// `IndicatorPaneSpec.id` -- the line's whole lifecycle rides on it.
export interface PriceLineSpec {
  id: string;
  price: number;
  color: string;
  title?: string;
}

// Story 18.2: a tool-drawn custom-primitive drawing. A tagged union so Story 18.3's
// measurement joins as another `kind`; kept separate from PriceLineSpec because a
// two-anchor primitive and a native single-value price line are different mechanisms.
export interface TrendlineSpec {
  id: string;
  kind: "trendline";
  anchors: [TrendlineAnchor, TrendlineAnchor];
  color: string;
}
export type DrawingSpec = TrendlineSpec;

// Story 18.5: a Volume Profile placed on the main pane; `id` is the caller's stable key.
export interface VolumeProfileSpec extends VolumeProfileRenderSpec {
  id: string;
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
  /** Story 18.1 (AC #2/#4): declarative horizontal price lines on the main
   * candlestick series, diffed against an internal `Map<string, IPriceLine>`
   * registry exactly like `panes` above -- new ids `series.createPriceLine()`,
   * changed price/color/title `applyOptions()` of only the differing fields,
   * removed ids `series.removePriceLine()`; never touches
   * `timeScale().setVisibleLogicalRange()`/`fitContent()` (same AC discipline as
   * `panes`). Candles-mode-only (Lines mode has no single "the" main series to own
   * a price line -- spec Task 2's MVP scope decision): a no-op in Lines mode, and
   * the candles->lines branch of the `[mode]` effect already cleared the registry
   * when it removed the series the lines lived on -- a lines->candles return
   * re-creates every spec on the fresh series. */
  priceLines?: PriceLineSpec[];
  /** Story 18.1 (AC #3): reports a live drag of an existing line, in the library's
   * own crosshair coordinate space (see the drag effects below). This component
   * never mutates the caller's `priceLines` state itself -- it only reports; the
   * caller updates the spec and the registry diff above moves the line. */
  onPriceLineDrag?: (id: string, price: number) => void;
  /** Story 18.1 (AC #2/#4): reports a chart click as a price, converted via the
   * main series' `coordinateToPrice(param.point.y)`. The conversion must live here:
   * it is a series method, and the series instances are solely owned by this
   * component (AD-F4) -- the caller decides what a click means (ChartPage places a
   * line only while its hline tool is armed). Only subscribed while `mode ===
   * "candles"`; never called for a param without a point or when the conversion
   * returns null. */
  onPriceClick?: (price: number) => void;
  /** Story 18.2: declarative custom-primitive drawings, attached to the current main-pane
   * series (the candlestick series, or the `price` line series in Lines mode) and diffed
   * against an internal `Map<string, TrendlinePrimitive>` registry like `priceLines` --
   * add via `attachPrimitive`, changed anchors/color via `update()`, removed ids via
   * `detachPrimitive`. Never touches the visible range. A mode flip replaces the host
   * series, so the registry is cleared there and every spec re-attached on the new one. */
  drawings?: DrawingSpec[];
  /** Story 18.2: reports a chart click as a `{time, price}` point (same click subscription
   * and grab-suppression as `onPriceClick`; a click with no resolvable time -- past the
   * last bar's coordinate space -- is not reported). Works in both modes. */
  onPointClick?: (point: TrendlineAnchor) => void;
  /** Story 18.3: while true (candles mode only), a click-drag on the chart draws a
   * transient measurement rectangle + label (a `MeasurementPrimitive` owned entirely by
   * this component -- never in `drawings`) instead of panning; release removes it and
   * reports `onMeasureEnd`. Turning the prop false mid-drag (Esc) cancels with no residue.
   * The label is computed from `data` + `volume`, the arrays the chart already holds. */
  measureActive?: boolean;
  volume?: VolumeDatum[];
  onMeasureEnd?: () => void;
  /** Story 18.4: when set, a vertical marker line is drawn at this time (the replay start
   * bar); `null`/omitted removes it. Attached to the main candlestick series. */
  markerTime?: Time | null;
  /** Story 18.5: declarative Volume Profiles (one `VolumeProfilePrimitive` each), diffed by
   * id like `drawings`. Nothing in the app places one yet -- Stories 18.6-18.9 do. */
  volumeProfiles?: VolumeProfileSpec[];
  /** Story 18.6: while true (candles mode only) a click-drag selects a time range instead
   * of panning -- a live rectangle preview (no calculation), and on release exactly one
   * `onRangeSelect(start, end)`; a click without a drag reports nothing. Esc/disarm cancels. */
  rangeSelectActive?: boolean;
  onRangeSelect?: (start: TrendlineAnchor, end: TrendlineAnchor) => void;
  /** Story 18.6: edge resize for profiles whose spec carries `edges`. Grabbing an edge
   * (within a few px of it, inside the profile's vertical extent) reports `onProfileEdgeDrag`
   * on every move -- for a cheap ghost only -- and exactly one `onProfileEdgeCommit` on release. */
  /** Edges are only grabbable while true (default): the caller turns it off while another
   * tool is armed so an edge press never steals that tool's click. */
  profileEdgesEditable?: boolean;
  onProfileEdgeDrag?: (id: string, edge: "start" | "end", time: Time) => void;
  onProfileEdgeCommit?: (id: string, edge: "start" | "end", time: Time) => void;
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

// Story 18.1: one fixed width for every tool-drawn price line -- no per-line width in
// PriceLineSpec until a drawing tool actually needs one (YAGNI).
const PRICE_LINE_WIDTH = 1;

// Drag grab tolerance in pixels, close enough to the library's own price-line hit
// radius that a hover the library reports as "custom-price-line" is also the line this
// hit-test finds.
const PRICE_LINE_GRAB_TOLERANCE_PX = 5;

/**
 * Story 18.1 (AC #3): the price-line id a mousedown just grabbed, or `null`. Only a
 * crosshair param the library itself reported as hovering a custom price line owned by
 * the main series qualifies, and the nearest registry line within the grab tolerance
 * wins -- `hoveredInfo` carries no line identity, so this y-coordinate hit-test against
 * the specs is what recovers it.
 */
function findGrabbedPriceLineId(
  specs: PriceLineSpec[],
  series: ISeriesApi<"Candlestick">,
  param: MouseEventParams,
): string | null {
  const point = param.point;
  if (!point) return null;
  const info = param.hoveredInfo;
  if (info?.objectKind !== "custom-price-line" || info.series !== series) return null;
  let grabbedId: string | null = null;
  let bestDistance = PRICE_LINE_GRAB_TOLERANCE_PX;
  for (const spec of specs) {
    const lineY = series.priceToCoordinate(spec.price);
    if (lineY === null) continue;
    const distance = Math.abs(point.y - lineY);
    if (distance <= bestDistance) {
      bestDistance = distance;
      grabbedId = spec.id;
    }
  }
  return grabbedId;
}

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
  priceLines = [],
  onPriceLineDrag,
  onPriceClick,
  drawings = [],
  onPointClick,
  measureActive = false,
  volume = [],
  onMeasureEnd,
  markerTime = null,
  volumeProfiles = [],
  rangeSelectActive = false,
  onRangeSelect,
  profileEdgesEditable = true,
  onProfileEdgeDrag,
  onProfileEdgeCommit,
  liveBar,
}: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lineSeriesRef = useRef<Record<LineSeriesId, MainLineSeriesApi> | null>(null);
  const prevFirstTimeRef = useRef<Time | null>(null);
  const prevLinesLengthRef = useRef(0);
  const panesRef = useRef<Map<string, PaneEntry>>(new Map());
  // Story 18.1: price-line registry + drag bookkeeping. `lastCrosshairRef` holds the
  // library's latest crosshair param (cleared when the mouse leaves the chart, so
  // stale data can never start a drag); `dragIdRef` the id being dragged; the click
  // suppression flag lives from a line-grab mousedown until the chart click that
  // would otherwise have followed it (see the mousedown effect below).
  const priceLineRegistryRef = useRef<Map<string, IPriceLine>>(new Map());
  const lastCrosshairRef = useRef<MouseEventParams | null>(null);
  const dragIdRef = useRef<string | null>(null);
  const suppressNextClickRef = useRef(false);
  const measureDataRef = useRef<{ data: ChartDatum[]; volume: VolumeDatum[] }>({ data, volume });
  const profileRegistryRef = useRef<Map<string, VolumeProfilePrimitive>>(new Map());
  // Latest-callback/latest-specs refs: the drag effects below must not re-subscribe (and
  // lose an in-flight drag) whenever the caller re-renders with fresh closures or specs.
  const latestRef = useRef({ volumeProfiles, onRangeSelect, onProfileEdgeDrag, onProfileEdgeCommit });
  const markerRef = useRef<VerticalMarkerPrimitive | null>(null);
  const drawingRegistryRef = useRef<Map<string, TrendlinePrimitive>>(new Map());
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
    // Captured to a local for the cleanup below, same as `panes` -- reading
    // `.current` inside a cleanup is what the react-hooks/exhaustive-deps lint flags.
    const priceLineRegistry = priceLineRegistryRef.current;
    const drawingRegistry = drawingRegistryRef.current;
    const profileRegistry = profileRegistryRef.current;

    return () => {
      cancelled = true;
      window.removeEventListener("resize", handleResize);
      chartRef.current = null;
      seriesRef.current = null;
      lineSeriesRef.current = null;
      panes.clear();
      // Story 18.1: the price lines die with the chart here, same as the panes -- the
      // registry must not outlive the series instances it holds lines on.
      priceLineRegistry.clear();
      drawingRegistry.clear();
      profileRegistry.clear();
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

    // Story 18.2: drawings are attached to the main-pane host series, which is replaced
    // on every mode flip -- drop the entries (no detach: the series goes away entirely)
    // and let the [drawings, mode] effect re-attach them to the new host.
    drawingRegistryRef.current.clear();
    profileRegistryRef.current.clear();
    markerRef.current = null;

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
        prevFirstTimeRef.current = null;
      }
    } else {
      if (seriesRef.current) {
        chart.removeSeries(seriesRef.current);
        seriesRef.current = null;
        prevFirstTimeRef.current = null;
        // Story 18.1: the candlestick series' price lines die with the series here --
        // drop the registry entries without removePriceLine() calls (the series is
        // going away entirely), but NOT the `priceLines` prop, which stays the source
        // of truth: the [priceLines, mode] diff effect below sees an empty registry on
        // a lines->candles return and re-creates every spec on the fresh series.
        priceLineRegistryRef.current.clear();
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
    //
    // Detected by where the previous FIRST bar now sits, not by a length change: replay
    // (Story 18.4) grows/shrinks the array at its newest end, which must never be
    // mistaken for a prepend.
    const prevFirst = prevFirstTimeRef.current;
    const addedAtFront = prevFirst === null ? 0 : Math.max(0, data.findIndex((d) => d.time === prevFirst));
    const rangeBeforeUpdate = addedAtFront > 0 ? chart?.timeScale().getVisibleLogicalRange() : null;

    series.setData(data);
    prevFirstTimeRef.current = data.length > 0 ? data[0].time : null;

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

  useEffect(() => {
    // Story 18.1 (AC #2/#4): the priceLines prop's own registry-diff effect, the exact
    // discipline of the panes effect above -- per-id add/update/remove, never a
    // timeScale/visible-range call anywhere. Candles-mode-only per the prop's doc; the
    // [mode] effect above has already cleared the registry (with the series) before
    // this runs on a candles->lines flip, so the early return leaves nothing stale.
    if (mode !== "candles") return;
    const series = seriesRef.current;
    if (!series) return;
    const registry = priceLineRegistryRef.current;
    const specsById = new Map(priceLines.map((spec) => [spec.id, spec] as const));

    for (const [id, line] of [...registry]) {
      if (!specsById.has(id)) {
        series.removePriceLine(line);
        registry.delete(id);
      }
    }

    for (const spec of priceLines) {
      const line = registry.get(spec.id);
      if (!line) {
        registry.set(
          spec.id,
          series.createPriceLine({
            id: spec.id,
            price: spec.price,
            color: spec.color,
            lineWidth: PRICE_LINE_WIDTH,
            axisLabelVisible: true,
            title: spec.title,
          }),
        );
        continue;
      }
      // Read current options first, apply only the fields that differ -- mirrors the
      // panes effect's color check, so an unrelated re-render is a pure no-op. Title
      // normalizes to "" because the library defaults an unspecified create title to
      // an empty string, not undefined.
      const current = line.options();
      if (current.price !== spec.price) line.applyOptions({ price: spec.price });
      if (current.color !== spec.color) line.applyOptions({ color: spec.color });
      if (current.title !== (spec.title ?? "")) line.applyOptions({ title: spec.title ?? "" });
    }
  }, [priceLines, mode]);

  useEffect(() => {
    measureDataRef.current = { data, volume };
  }, [data, volume]);

  useEffect(() => {
    // Story 18.5: volumeProfiles registry diff -- same discipline as `drawings`.
    const host = seriesRef.current ?? lineSeriesRef.current?.price;
    if (!host) return;
    const registry = profileRegistryRef.current;
    const specsById = new Map(volumeProfiles.map((spec) => [spec.id, spec] as const));

    for (const [id, primitive] of [...registry]) {
      if (!specsById.has(id)) {
        host.detachPrimitive(primitive);
        registry.delete(id);
      }
    }
    for (const spec of volumeProfiles) {
      const primitive = registry.get(spec.id);
      if (primitive) {
        primitive.update(spec);
        continue;
      }
      const created = new VolumeProfilePrimitive(spec);
      host.attachPrimitive(created);
      registry.set(spec.id, created);
    }
  }, [volumeProfiles, mode]);

  useEffect(() => {
    // Story 18.4 (AC #2/#6): add/move/remove the replay start marker.
    const host = seriesRef.current;
    if (!host || mode !== "candles") return;
    if (markerTime === null) {
      if (markerRef.current) host.detachPrimitive(markerRef.current);
      markerRef.current = null;
      return;
    }
    if (markerRef.current) {
      markerRef.current.setTime(markerTime);
      return;
    }
    markerRef.current = new VerticalMarkerPrimitive(markerTime, cssVar("--color-warn", "#ffff55"));
    host.attachPrimitive(markerRef.current);
  }, [markerTime, mode]);

  useEffect(() => {
    latestRef.current = { volumeProfiles, onRangeSelect, onProfileEdgeDrag, onProfileEdgeCommit };
  });

  useEffect(() => {
    // Story 18.6 (AC #2): range selection for the fixed-range volume profile -- a live
    // rectangle preview (the measurement primitive with no label; NO profile calculation
    // while dragging), then exactly one onRangeSelect on release.
    const container = containerRef.current;
    const chart = chartRef.current;
    const host = seriesRef.current;
    if (!container || !chart || !host || !rangeSelectActive || mode !== "candles") return;

    const preview = new MeasurementPrimitive(cssVar("--color-active", "#55ffff"));
    let attached = false;
    const stopDrag = attachRangeDrag(container, chart, host, {
      onMove: (start, end) => {
        if (!attached) {
          host.attachPrimitive(preview);
          attached = true;
        }
        preview.setSelection(start, end, []);
      },
      onRelease: (last) => {
        if (!last || !attached) return;
        host.detachPrimitive(preview);
        attached = false;
        latestRef.current.onRangeSelect?.(last.start, last.end);
      },
    });
    return () => {
      stopDrag();
      if (attached) host.detachPrimitive(preview);
    };
  }, [rangeSelectActive, mode]);

  useEffect(() => {
    // Story 18.6 (AC #3): edge grab-and-drag for placed profiles. Off while a range tool
    // is armed (their capture-phase drags own the mouse then).
    const container = containerRef.current;
    const chart = chartRef.current;
    const host = seriesRef.current;
    if (!container || !chart || !host || !profileEdgesEditable || rangeSelectActive || measureActive || mode !== "candles") return;

    const EDGE_TOLERANCE_PX = 6;
    let grabbed: { id: string; edge: "start" | "end" } | null = null;
    let lastTime: Time | null = null;

    const timeAt = (event: MouseEvent): Time | null =>
      chart.timeScale().coordinateToTime(event.clientX - container.getBoundingClientRect().left);

    const findEdge = (event: MouseEvent): { id: string; edge: "start" | "end" } | null => {
      const box = container.getBoundingClientRect();
      const x = event.clientX - box.left;
      const y = event.clientY - box.top;
      for (const spec of latestRef.current.volumeProfiles) {
        if (!spec.edges || spec.profile.rows.length === 0) continue;
        const top = host.priceToCoordinate(spec.profile.rows[spec.profile.rows.length - 1].priceHigh);
        const bottom = host.priceToCoordinate(spec.profile.rows[0].priceLow);
        if (top === null || bottom === null) continue;
        if (y < Math.min(top, bottom) - EDGE_TOLERANCE_PX || y > Math.max(top, bottom) + EDGE_TOLERANCE_PX) continue;
        // The nearer edge wins, so a narrow range (edges within one tolerance of each other)
        // keeps both grabbable.
        let best: { id: string; edge: "start" | "end" } | null = null;
        let bestDistance = EDGE_TOLERANCE_PX;
        for (const edge of ["start", "end"] as const) {
          const edgeX = chart.timeScale().timeToCoordinate(spec.edges[edge === "start" ? "startTime" : "endTime"]);
          if (edgeX === null || Math.abs(x - edgeX) > bestDistance) continue;
          bestDistance = Math.abs(x - edgeX);
          best = { id: spec.id, edge };
        }
        if (best) return best;
      }
      return null;
    };

    const handleMouseDown = (event: MouseEvent): void => {
      if (event.button !== 0 || grabbed) return;
      grabbed = findEdge(event);
      if (grabbed) event.stopPropagation();
    };
    const handleMouseMove = (event: MouseEvent): void => {
      if (!grabbed) return;
      // No button held = the release happened outside the window (blur): finish the drag
      // instead of leaving it stuck to the cursor.
      if (event.buttons === 0) {
        handleMouseUp(event, true);
        return;
      }
      const time = timeAt(event);
      if (time === null) return;
      lastTime = time;
      latestRef.current.onProfileEdgeDrag?.(grabbed.id, grabbed.edge, time);
    };
    const handleMouseUp = (event: MouseEvent, lost = false): void => {
      if (!grabbed || (!lost && event.button !== 0)) return;
      const done = grabbed;
      const time = lastTime;
      grabbed = null;
      lastTime = null;
      if (time !== null) latestRef.current.onProfileEdgeCommit?.(done.id, done.edge, time);
    };

    container.addEventListener("mousedown", handleMouseDown, true);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      container.removeEventListener("mousedown", handleMouseDown, true);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [profileEdgesEditable, rangeSelectActive, measureActive, mode]);

  useEffect(() => {
    // Story 18.3 (AC #2/#3/#5): the transient click-drag measurement, on the shared
    // range-drag plumbing. The primitive is attached lazily on the first move (a click
    // with no drag shows nothing and leaves the tool armed) and detached on release or,
    // on Esc/disarm, by the cleanup below.
    const container = containerRef.current;
    const chart = chartRef.current;
    const host = seriesRef.current;
    if (!container || !chart || !host || !measureActive || mode !== "candles") return;

    const primitive = new MeasurementPrimitive(cssVar("--color-active", "#55ffff"));
    let attached = false;

    const stopDrag = attachRangeDrag(container, chart, host, {
      onMove: (start, end) => {
        const { data: candles, volume: volumes } = measureDataRef.current;
        if (!attached) {
          host.attachPrimitive(primitive);
          attached = true;
        }
        primitive.setSelection(start, end, formatMeasurement(computeMeasurement(start, end, candles, volumes)));
      },
      onRelease: (last) => {
        if (!last || !attached) return;
        host.detachPrimitive(primitive);
        attached = false;
        onMeasureEnd?.();
      },
    });
    return () => {
      stopDrag();
      if (attached) host.detachPrimitive(primitive);
    };
  }, [measureActive, onMeasureEnd, mode]);

  useEffect(() => {
    // Story 18.2 (AC #4): the drawings prop's registry-diff effect -- same per-id
    // add/update/remove discipline as priceLines, never a visible-range call.
    const host = seriesRef.current ?? lineSeriesRef.current?.price;
    if (!host) return;
    const registry = drawingRegistryRef.current;
    const specsById = new Map(drawings.map((spec) => [spec.id, spec] as const));

    for (const [id, primitive] of [...registry]) {
      if (!specsById.has(id)) {
        host.detachPrimitive(primitive);
        registry.delete(id);
      }
    }

    for (const spec of drawings) {
      const primitive = registry.get(spec.id);
      if (primitive) {
        primitive.update(spec.anchors, spec.color);
        continue;
      }
      const created = new TrendlinePrimitive(spec.anchors, spec.color);
      host.attachPrimitive(created);
      registry.set(spec.id, created);
    }
  }, [drawings, mode]);

  useEffect(() => {
    // Story 18.1 (AC #3): the one crosshairMove subscription serves both halves of the
    // drag -- remembering the latest hover (what the mousedown grab below hit-tests
    // against) and, while a drag is active, converting its own `param.point.y` to a
    // price. Staying inside the library's `param.point` coordinate space (rather than
    // native mousemove + getBoundingClientRect) keeps the grab hit-test and the drag
    // conversion in the same space, pane offsets included. `paneIndex === 0` guards
    // against y-converting from an indicator sub-pane.
    const chart = chartRef.current;
    if (!chart || !onPriceLineDrag || mode !== "candles") return;
    // A drag can never carry over from a previous subscription lifetime (e.g. a
    // candles->lines->candles flip while the button was somehow still held) -- a fresh
    // subscription starts dragless.
    dragIdRef.current = null;

    const handleCrosshairMove = (param: MouseEventParams): void => {
      lastCrosshairRef.current = param.point ? param : null;
      const draggedId = dragIdRef.current;
      if (draggedId === null) return;
      if (!param.point || param.paneIndex !== 0) return;
      const price = seriesRef.current?.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;
      onPriceLineDrag(draggedId, price);
    };

    chart.subscribeCrosshairMove(handleCrosshairMove);
    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      // The subscription that would have refreshed it is gone -- a stale param from
      // this lifetime must never be able to start a drag in the next one.
      lastCrosshairRef.current = null;
    };
  }, [onPriceLineDrag, mode]);

  useEffect(() => {
    // Story 18.1 (AC #3): the drag's start/end. Capture phase so a line grab runs
    // BEFORE lightweight-charts' own internal mousedown handlers (attached to inner
    // elements) -- stopPropagation() then prevents any chart pan from starting. The
    // window-level mouseup (not container-level: the drag can end with the cursor
    // outside the chart) is what always ends it.
    const container = containerRef.current;
    if (!container || !onPriceLineDrag || mode !== "candles") return;

    const handleMouseDown = (event: MouseEvent): void => {
      const series = seriesRef.current;
      const param = lastCrosshairRef.current;
      const grabbedId = series && param ? findGrabbedPriceLineId(priceLines, series, param) : null;
      dragIdRef.current = grabbedId;
      // A grab whose release happens outside the chart never fires a chart click, so
      // the suppression flag would otherwise swallow the NEXT real click -- every
      // non-grab mousedown clears it, keeping it true only from grab to click.
      suppressNextClickRef.current = grabbedId !== null;
      if (grabbedId === null) return;
      event.stopPropagation();
    };

    const handleMouseUp = (): void => {
      dragIdRef.current = null;
    };

    container.addEventListener("mousedown", handleMouseDown, true);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      container.removeEventListener("mousedown", handleMouseDown, true);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [priceLines, onPriceLineDrag, mode]);

  useEffect(() => {
    // Story 18.1/18.2: click reporting. `onPriceClick` is candles-only; `onPointClick`
    // works in both modes. Subscribed only while at least one applicable callback is
    // provided -- otherwise the chart's own click behavior is left completely untouched.
    const chart = chartRef.current;
    const wantsPrice = onPriceClick && mode === "candles";
    if (!chart || (!wantsPrice && !onPointClick)) return;

    const handleClick = (param: MouseEventParams): void => {
      if (suppressNextClickRef.current) {
        suppressNextClickRef.current = false;
        return;
      }
      if (!param.point) return;
      // Lines mode's host is the `price` line series (see the drawings effect).
      const host = seriesRef.current ?? lineSeriesRef.current?.price;
      const price = host?.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;
      if (wantsPrice) onPriceClick(price);
      // Story 18.2: `param.time` is only set over an existing bar; fall back to the
      // time scale's own x->time conversion for the empty area right of the last bar.
      const time = param.time ?? chart.timeScale().coordinateToTime(param.point.x);
      if (time !== null && time !== undefined) onPointClick?.({ time, price });
    };

    chart.subscribeClick(handleClick);
    return () => chart.unsubscribeClick(handleClick);
  }, [onPriceClick, onPointClick, mode]);

  return <div ref={containerRef} />;
}
