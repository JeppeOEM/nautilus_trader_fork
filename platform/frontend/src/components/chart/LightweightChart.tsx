import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type IPaneApi,
  type IPriceLine,
  LineStyle,
  type ISeriesApi,
  type LineWidth,
  type LineData,
  type MouseEventParams,
  type Time,
  type WhitespaceData,
} from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { ChartDatum, VolumeDatum } from "../../hooks/useCandles";
import type { LiveBar } from "../../hooks/useLiveCandle";
import type { IndicatorDatum } from "../../hooks/useIndicatorSeries";
import type { SnapshotLinesData } from "../../hooks/useSnapshotSeries";
import { type GapRun, MAX_GAP_ROWS_PER_GAP, findGapRuns, gapRunsBySlot } from "../../lib/gaps";
import { type GapLookup, type LegendAction, type LegendSeries, renderLegends } from "./legend";
import { DEFAULT_LINE_STYLE, DEFAULT_LINE_WIDTH, type LineStyleName } from "../../lib/indicatorStyle";
import { chartVar } from "./chartTheme";
import { assignPaneColor, cssVar } from "./paneColors";
import {
  MeasurementPrimitive,
  computeMeasurement,
  formatMeasurement,
} from "./primitives/MeasurementPrimitive";
import { attachRangeDrag } from "./rangeDrag";
import { VolumeProfilePrimitive, type VolumeProfileRenderSpec } from "./primitives/VolumeProfilePrimitive";
import { VerticalMarkerPrimitive } from "./primitives/VerticalMarkerPrimitive";
import { GapPrimitive } from "./primitives/GapPrimitive";
import { TrendlinePrimitive, type TrendlineAnchor } from "./primitives/TrendlinePrimitive";

export type PaneSeriesKind = "Line" | "Histogram";

export type ChartMode = "candles" | "lines";

// Story 18.10: a one-shot view command from the page's Fit / Latest buttons. `seq` makes a
// repeated identical click a NEW command (the effect keys on the object).
export interface ViewCommand {
  kind: "fit" | "latest";
  seq: number;
}

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
  /** "pane" (default) = its own pane under the price chart; "overlay" = drawn inside the
   * price pane (TradingView-style: MAs/bands sharing the price scale). */
  placement?: "pane" | "overlay";
  /** Outputs of one indicator (e.g. MACD's line/signal/histogram) share one pane and one
   * legend row; defaults to `id`, i.e. a lone series. */
  group?: string;
  /** Legend title for the group, e.g. "RelativeStrengthIndex (14)". */
  groupLabel?: string;
  /** Legend tooltip for this series' value, e.g. "value" / "signal". */
  outputLabel?: string;
  /** Story 32.3, the legend eye. An overlay's series is hidden in place (`visible: false`); a pane
   * indicator's pane is collapsed -- removed from the chart, the page shrinking by its height --
   * and re-added at its former position and height when shown. The spec (and its data) stays in
   * this array either way, so showing refetches nothing. */
  hidden?: boolean;
  /** False for Volume: its legend row gets the eye and the x but no settings gear. */
  configurable?: boolean;
  /** False for a series no configured entry owns (stale values between an Apply and its refetch):
   * its legend row is a plain readout, with no buttons that could act on nothing. Default true. */
  actionable?: boolean;
  /** Story 32.3 style, from the indicator entry; absent = the library default (3 px, solid). */
  lineWidth?: number;
  lineStyle?: LineStyleName;
  /** A histogram output's colours for value >= 0 and < 0; absent = the one `color`. */
  upColor?: string;
  downColor?: string;
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
  /** Trendline first click, previewed as a line following the cursor until the second. */
  pendingAnchor?: TrendlineAnchor | null;
  /** True while no tool is armed: a click on a drawn line then opens its edit menu. */
  drawEditable?: boolean;
  /** Edit-menu actions; `id` is a `PriceLineSpec` or `DrawingSpec` id. */
  onDrawingColor?: (id: string, color: string) => void;
  onDrawingDelete?: (id: string) => void;
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
  /** Story 18.10: crosshair on/off (the left toolbar's toggle); default on. */
  crosshairVisible?: boolean;
  /** Story 18.10: applied once per new command object -- `fit` snaps the visible range to
   * all loaded data, `latest` scrolls to the newest bar. The only place this component
   * deliberately moves the view; the data/pane effects still never do. */
  viewCommand?: ViewCommand | null;
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
  liveBar?: LiveBar | null;
  /** Story 32.3: a legend eye / gear / x was pressed; `group` is the indicator instance id (the
   * spec's `group`) or "volume". The page persists the change; this component only reports. */
  onLegendAction?: (action: LegendAction, group: string) => void;
}

type AnySeriesApi = ISeriesApi<"Line", Time> | ISeriesApi<"Histogram", Time>;

interface PaneEntry {
  /** null for an overlay: it lives in the price pane (0), so there is no pane to remove. */
  pane: IPaneApi<Time> | null;
  group: string;
  spec: IndicatorPaneSpec;
  series: AnySeriesApi;
  /** The last `spec.data` reference applied to `series`, so an unrelated pane's data
   * refresh doesn't force every other still-visible pane to re-run `setData()` too --
   * `panes` is rebuilt by the caller's own `useMemo` on every candle/indicator page
   * fetch, not just the one series that actually changed. */
  lastData: IndicatorDatum[];
  /** Story 32.1: the pane's one gap painter, held by exactly one entry of each non-overlay
   * pane (one per pane, so translucent fills never stack); null on every other entry. */
  gap: GapPrimitive | null;
  /** `upColor|downColor` last painted into the histogram's per-point colours. */
  lastUpDown: string;
}

// Story 32.3: the library's `LineStyle` for each persisted style name (Solid 0, Dotted 1, Dashed 2).
const LINE_STYLE_OF: Record<LineStyleName, LineStyle> = {
  solid: LineStyle.Solid,
  dotted: LineStyle.Dotted,
  dashed: LineStyle.Dashed,
};

/** What a line series is drawn with beyond its colour. Always both fields, the library default
 * where the spec states none, so a width or style the entry no longer stores (cleared, or a failed
 * save rolled back) goes back to the default instead of sticking. */
function lineOptions(spec: IndicatorPaneSpec): { lineWidth?: LineWidth; lineStyle?: LineStyle } {
  if (spec.kind !== "Line") return {};
  // The width comes from a hand-editable file: clamp to the library's 1..4 integers, and ignore a
  // style name that is not one of ours rather than hand the library `undefined`.
  const width =
    typeof spec.lineWidth === "number" && Number.isFinite(spec.lineWidth)
      ? Math.min(4, Math.max(1, Math.round(spec.lineWidth)))
      : DEFAULT_LINE_WIDTH;
  const style = (spec.lineStyle && LINE_STYLE_OF[spec.lineStyle]) ?? LINE_STYLE_OF[DEFAULT_LINE_STYLE];
  return { lineWidth: width as LineWidth, lineStyle: style };
}

const upDownKey = (spec: IndicatorPaneSpec): string =>
  spec.kind === "Histogram" ? `${spec.upColor ?? ""}|${spec.downColor ?? ""}` : "";

/** A histogram with up/down colours paints each bar by its sign; the data in state is untouched. */
function paintedData(spec: IndicatorPaneSpec): IndicatorDatum[] {
  if (spec.kind !== "Histogram" || (!spec.upColor && !spec.downColor)) return spec.data;
  return spec.data.map((d) => {
    // A null / NaN value is a gap, not a positive bar: no colour (never "up").
    if (!("value" in d) || typeof d.value !== "number" || Number.isNaN(d.value)) return d;
    return { ...d, color: d.value >= 0 ? (spec.upColor ?? spec.color) : (spec.downColor ?? spec.color) };
  });
}

function setSeriesData(series: AnySeriesApi, data: IndicatorDatum[]): void {
  // Both `Line` and `Histogram` series accept LineData-shaped points (Histogram's own
  // `color` per-point field is optional) -- one cast site here, not a runtime branch
  // per series kind.
  (series.setData as (d: (LineData<Time> | WhitespaceData<Time>)[]) => void)(data);
}

type MainLineSeriesApi = ISeriesApi<"Line", Time>;

// Story 32.2: pane heights in px. The chart's total height is the price pane plus one entry per
// extra pane, so adding a pane grows the page (the operator scrolls) instead of squeezing the
// price pane; only the width follows the container.
export const PRICE_PANE_PX = 500;
export const VOLUME_PANE_PX = 120;
export const INDICATOR_PANE_PX = 160;
// Known limit: the page height is 500 + 120 + 160 per extra pane with no cap, by design (the
// operator scrolls, Story 32.2). Upgrade path: a max-panes warning in the Indicators dialog.
const VOLUME_PANE_ID = "volume";

/** Current pixel height of every pane a registry entry sits on, read BEFORE the registry
 * mutates (adding or removing a pane makes the library re-split the old total). Index 0 of the
 * result is the price pane; `null` means "not laid out yet, use the default". */
function snapshotPaneHeights(
  chart: IChartApi,
  registry: Map<string, PaneEntry>,
): { price: number | null; panes: Map<IPaneApi<Time>, number> } {
  const panes = new Map<IPaneApi<Time>, number>();
  for (const entry of registry.values()) {
    const px = entry.pane?.getHeight() ?? 0;
    if (entry.pane && px > 0) panes.set(entry.pane, px);
  }
  const price = chart.panes()[0]?.getHeight() ?? 0;
  return { price: price > 0 ? price : null, panes };
}

/**
 * Pins every pane to its pixel size and resizes the chart to their sum. The library splits the
 * chart by stretch factor, so a stretch factor equal to the pane's px, with a chart height of
 * exactly Σ px + its own chrome (1 px separators, the time axis), gives each pane exactly that
 * many px -- an existing pane (a divider the operator dragged included) keeps its size and only
 * the total changes. `setHeight` is not used: it takes the difference from the other panes.
 */
function layoutPaneHeights(
  chart: IChartApi,
  registry: Map<string, PaneEntry>,
  before: ReturnType<typeof snapshotPaneHeights>,
  priceKnown: boolean,
  remembered: Map<string, number>,
): boolean {
  const pricePx = priceKnown ? (before.price ?? PRICE_PANE_PX) : PRICE_PANE_PX;
  chart.panes()[0]?.setStretchFactor(pricePx);
  let total = pricePx;
  const seen = new Set<IPaneApi<Time>>();
  for (const entry of registry.values()) {
    if (!entry.pane || seen.has(entry.pane)) continue;
    seen.add(entry.pane);
    const fallback = entry.group === VOLUME_PANE_ID ? VOLUME_PANE_PX : INDICATOR_PANE_PX;
    // Before the axis was measured the library split an axis-less total, so every pane's own
    // height is short by its share of the axis: only the defaults are trustworthy then.
    // A pane the legend eye collapsed comes back at the height it had (Story 32.3); that height
    // was measured, so it is trusted even before the axis was.
    const px = (priceKnown ? before.panes.get(entry.pane) : undefined) ?? remembered.get(entry.group) ?? fallback;
    remembered.delete(entry.group);
    entry.pane.setStretchFactor(px);
    total += px;
  }
  const axisPx = chart.timeScale().height();
  chart.applyOptions({ height: Math.round(total + seen.size + axisPx) });
  // Only an applied height that included the measured time axis makes the panes' own heights
  // trustworthy as the next sync's baseline.
  return axisPx > 0;
}

// Story 32.1: the dedicated gap colour -- a chart-only token no other code reads.
function gapColor(): string {
  return chartVar("--chart-gap");
}

// Gap runs come from the price series only -- the candles, or in Lines mode the first line
// (bid, null exactly on a gap row) -- never from an indicator's own whitespace: warm-up is
// not a gap (Story 32.1). The forming live bar (drawn with `update()`, not in `data`) closes a
// trailing run, so a hole over the cap up to it still reads its real length and "(compressed)".
function priceGapRuns(
  mode: ChartMode,
  data: ChartDatum[],
  linesData: SnapshotLinesData | undefined,
  liveTime: number | undefined,
): GapRun[] {
  if (mode === "candles") return findGapRuns(data, (d) => "open" in d, MAX_GAP_ROWS_PER_GAP, liveTime);
  return findGapRuns(linesData?.bid ?? [], (d) => "value" in d);
}

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

/** The id of the trendline or price line under a click, or `null`. */
function findClickedDrawingId(
  point: { x: number; y: number },
  series: ISeriesApi<"Candlestick"> | null,
  drawings: Map<string, TrendlinePrimitive>,
  specs: PriceLineSpec[],
): string | null {
  let bestId: string | null = null;
  let best = PRICE_LINE_GRAB_TOLERANCE_PX + 1;
  for (const [id, primitive] of drawings) {
    const d = primitive.distanceTo(point.x, point.y);
    if (d !== null && d < best) [best, bestId] = [d, id];
  }
  for (const spec of series ? specs : []) {
    const y = series?.priceToCoordinate(spec.price);
    if (y != null && Math.abs(point.y - y) < best) [best, bestId] = [Math.abs(point.y - y), spec.id];
  }
  return bestId;
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
  pendingAnchor = null,
  drawEditable = false,
  onDrawingColor,
  onDrawingDelete,
  onPointClick,
  measureActive = false,
  volume = [],
  onMeasureEnd,
  markerTime = null,
  crosshairVisible = true,
  viewCommand = null,
  volumeProfiles = [],
  rangeSelectActive = false,
  onRangeSelect,
  profileEdgesEditable = true,
  onProfileEdgeDrag,
  onProfileEdgeCommit,
  liveBar,
  onLegendAction,
}: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const lineSeriesRef = useRef<Record<LineSeriesId, MainLineSeriesApi> | null>(null);
  const prevFirstTimeRef = useRef<Time | null>(null);
  const prevLinesLengthRef = useRef(0);
  const panesRef = useRef<Map<string, PaneEntry>>(new Map());
  const legendItemsRef = useRef<LegendSeries[]>([]);
  // Story 32.3: the px height of each pane the legend eye collapsed, by group, until it is shown.
  const collapsedHeightsRef = useRef<Map<string, number>>(new Map());
  const legendActionRef = useRef(onLegendAction);
  legendActionRef.current = onLegendAction;
  const handleLegendAction = useCallback(
    (action: LegendAction, group: string): void => legendActionRef.current?.(action, group),
    [],
  );
  // Story 18.1: price-line registry + drag bookkeeping. `lastCrosshairRef` holds the
  // library's latest crosshair param (cleared when the mouse leaves the chart, so
  // stale data can never start a drag); `dragIdRef` the id being dragged; the click
  // suppression flag lives from a line-grab mousedown until the chart click that
  // would otherwise have followed it (see the mousedown effect below).
  const priceLineRegistryRef = useRef<Map<string, IPriceLine>>(new Map());
  const lastCrosshairRef = useRef<MouseEventParams | null>(null);
  const dragIdRef = useRef<string | null>(null);
  const suppressNextClickRef = useRef(false);
  const dragMovedRef = useRef(false);
  const previewRef = useRef<TrendlinePrimitive | null>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const editRef = useRef({ priceLines, drawEditable });
  editRef.current = { priceLines, drawEditable };
  const measureDataRef = useRef<{ data: ChartDatum[]; volume: VolumeDatum[] }>({ data, volume });
  const profileRegistryRef = useRef<Map<string, VolumeProfilePrimitive>>(new Map());
  // Latest-callback/latest-specs refs: the drag effects below must not re-subscribe (and
  // lose an in-flight drag) whenever the caller re-renders with fresh closures or specs.
  const latestRef = useRef({ volumeProfiles, onRangeSelect, onProfileEdgeDrag, onProfileEdgeCommit });
  const markerRef = useRef<VerticalMarkerPrimitive | null>(null);
  const drawingRegistryRef = useRef<Map<string, TrendlinePrimitive>>(new Map());
  // Newest time painted on the candlestick series -- the last setData() point or the last
  // live update(), whichever is later. lightweight-charts throws on an update() older than
  // its last point, so the live effect checks against this and every setData() resets it.
  const lastPaintedTimeRef = useRef<number | null>(null);
  // Story 32.1: every gap slot of the price series, painted on the price pane (labelled, on
  // its host series) and on every non-overlay pane (unlabelled, PaneEntry.gap) alike, and
  // looked up by slot time for the legend's crosshair readout.
  const liveTime = liveBar ? (liveBar.time as unknown as number) : undefined;
  const gapRuns = useMemo(() => priceGapRuns(mode, data, linesData, liveTime), [mode, data, linesData, liveTime]);
  const gapRunsRef = useRef(gapRuns);
  gapRunsRef.current = gapRuns;
  const gapLookupRef = useRef<GapLookup>(new Map());
  gapLookupRef.current = useMemo(() => gapRunsBySlot(gapRuns), [gapRuns]);
  // False until a layout has sized the chart with the time axis measured: before that the price
  // pane's own height is not the 500 px budget (the library took the axis out of the initial 500).
  const laidOutRef = useRef(false);
  const priceGapRef = useRef<{ host: ISeriesApi<"Candlestick"> | MainLineSeriesApi; primitive: GapPrimitive } | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // Story 15.9: lightweight-charts' own defaults aren't VGA-derived -- every one of
    // background/text/grid/crosshair is explicitly set from the semantic tokens so the
    // chart itself doesn't stay the one non-conforming element on an otherwise-restyled
    // page.
    laidOutRef.current = false; // a fresh chart's price pane has not been laid out yet
    const chart = createChart(container, {
      width: container.clientWidth,
      height: PRICE_PANE_PX,
      layout: {
        background: { color: chartVar("--chart-bg") },
        textColor: chartVar("--chart-text"),
        fontFamily: cssVar("--font-terminal", "monospace"),
      },
      grid: {
        vertLines: { color: chartVar("--chart-grid") },
        horzLines: { color: chartVar("--chart-grid") },
      },
      crosshair: {
        vertLine: {
          color: chartVar("--chart-crosshair"),
          labelBackgroundColor: chartVar("--chart-crosshair-label-bg"),
        },
        horzLine: {
          color: chartVar("--chart-crosshair"),
          labelBackgroundColor: chartVar("--chart-crosshair-label-bg"),
        },
      },
      // Both scales default to a non-token gray border line (library default
      // a dark navy) -- override explicitly, same as grid/crosshair above.
      rightPriceScale: { borderColor: chartVar("--chart-border") },
      // Intraday bars are unreadable without clock labels -- the library default shows dates only.
      timeScale: { borderColor: chartVar("--chart-border"), timeVisible: true },
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

    // Deferred to a frame: applying the width inside the observer callback re-lays-out the
    // observed container in the same frame, which is what raises "ResizeObserver loop completed
    // with undelivered notifications". Coalesces bursts to one apply per frame too.
    let resizeFrame = 0;
    const handleResize = () => {
      cancelAnimationFrame(resizeFrame);
      resizeFrame = requestAnimationFrame(() => {
        chart.applyOptions({ width: container.clientWidth });
        // The first pane sync can run before the time axis is measured; the observer's first
        // delivery follows layout, so it completes that layout with the axis known.
        if (!laidOutRef.current) {
          const registry = panesRef.current;
          laidOutRef.current = layoutPaneHeights(
            chart,
            registry,
            snapshotPaneHeights(chart, registry),
            false,
            collapsedHeightsRef.current,
          );
        }
      });
    };
    // ResizeObserver, not window "resize": catches layout-only reflows and a container that
    // was hidden (clientWidth 0) at mount. Fires once on observe, so it also does the first sync.
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(container);
    const panes = panesRef.current;
    const collapsedHeights = collapsedHeightsRef.current;
    // Captured to a local for the cleanup below, same as `panes` -- reading
    // `.current` inside a cleanup is what the react-hooks/exhaustive-deps lint flags.
    const priceLineRegistry = priceLineRegistryRef.current;
    const drawingRegistry = drawingRegistryRef.current;
    const profileRegistry = profileRegistryRef.current;

    return () => {
      cancelled = true;
      resizeObserver.disconnect();
      cancelAnimationFrame(resizeFrame);
      chartRef.current = null;
      seriesRef.current = null;
      lineSeriesRef.current = null;
      // The gap primitives die with the chart below (chart.remove()), like the panes.
      priceGapRef.current = null;
      panes.clear();
      collapsedHeights.clear();
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
    // Story 32.1: the price gap painter is detached from the old host while that series still
    // exists; the [gapRuns, mode] effect below attaches a fresh one to the new host.
    const priceGap = priceGapRef.current;
    if (priceGap) {
      priceGap.host.detachPrimitive(priceGap.primitive);
      priceGapRef.current = null;
    }

    if (mode === "candles") {
      if (lineSeriesRef.current) {
        for (const id of LINE_SERIES_IDS) chart.removeSeries(lineSeriesRef.current[id]);
        lineSeriesRef.current = null;
        prevLinesLengthRef.current = 0;
      }
      if (!seriesRef.current) {
        // Up/down candle colours explicitly from the chart tokens (Story 32.4) --
        // lightweight-charts' own defaults are never relied on.
        const up = chartVar("--chart-up");
        const down = chartVar("--chart-down");
        const dim = chartVar("--chart-text-dim");
        seriesRef.current = chart.addSeries(CandlestickSeries, {
          upColor: up,
          downColor: down,
          borderUpColor: up,
          borderDownColor: down,
          wickUpColor: up,
          wickDownColor: down,
          // The base borderColor/wickColor fields (as opposed to the Up/Down
          // variants above) are vestigial fallbacks whose library defaults
          // are otherwise never overridden -- set
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
    lastPaintedTimeRef.current = data.length > 0 ? (data[data.length - 1].time as unknown as number) : null;

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
    const chart = chartRef.current;
    if (!chart) return;
    const registry = panesRef.current;
    // A hidden pane indicator is collapsed: its spec stays in `panes` (data and legend row) but no
    // pane or series exists for it. A hidden overlay is still drawn by the library, invisibly.
    const placed = panes.filter((spec) => !(spec.hidden && spec.placement !== "overlay"));
    const specsById = new Map(placed.map((spec) => [spec.id, spec] as const));
    const heightsBefore = snapshotPaneHeights(chart, registry);
    // Heights are re-pinned only when the pane set or its order changes: a data-only refresh
    // re-laying out would fight a divider the operator is dragging at that moment.
    let panesChanged = false;

    // Remove ids no longer present first -- never touches timeScale/visible range, just
    // `chart.removePane()` (AC #4). A shared pane goes only with its group's last series.
    for (const [id, entry] of [...registry]) {
      if (specsById.has(id)) continue;
      registry.delete(id);
      // A pane's gap painter goes with its host series; a pane that stays gets a new one on a
      // sibling below.
      if (entry.gap) entry.series.detachPrimitive(entry.gap);
      const groupStillUsed = [...registry.values()].some((e) => e.pane === entry.pane);
      if (entry.pane && !groupStillUsed) {
        // Collapsed by the eye (its spec is still in `panes`): remember the height it had.
        const px = heightsBefore.panes.get(entry.pane);
        if (px && panes.some((spec) => spec.id === id)) collapsedHeightsRef.current.set(entry.group, px);
        chart.removePane(entry.pane.paneIndex());
        panesChanged = true;
      } else chart.removeSeries(entry.series);
    }

    // A remembered collapsed height outlives only its indicator: once no pane spec carries the
    // group (removed, not just hidden) the entry would otherwise leak and mis-size a later re-add.
    const liveGroups = new Set(panes.map((spec) => spec.group ?? spec.id));
    for (const group of [...collapsedHeightsRef.current.keys()]) {
      if (!liveGroups.has(group)) collapsedHeightsRef.current.delete(group);
    }

    // Add new ids / update data+color for ids that stayed -- again, no visible-range
    // call anywhere in this branch.
    for (const spec of placed) {
      let entry = registry.get(spec.id);
      if (!entry) {
        const group = spec.group ?? spec.id;
        const overlay = spec.placement === "overlay";
        // The group's existing pane (a sibling output already added), else a new one.
        let pane = overlay ? null : ([...registry.values()].find((e) => e.group === group && e.pane)?.pane ?? null);
        if (!overlay && !pane) {
          pane = chart.addPane();
          panesChanged = true;
        }
        const definition = spec.kind === "Line" ? LineSeries : HistogramSeries;
        const series = chart.addSeries(
          definition,
          { color: spec.color, ...lineOptions(spec), ...(spec.hidden ? { visible: false } : {}) },
          pane ? pane.paneIndex() : 0,
        ) as AnySeriesApi;
        entry = { pane, group, spec, series, lastData: spec.data, gap: null, lastUpDown: upDownKey(spec) };
        registry.set(spec.id, entry);
        setSeriesData(entry.series, paintedData(spec));
        continue;
      }
      entry.spec = spec;
      const options = entry.series.options() as { color?: string; visible?: boolean; lineWidth?: number; lineStyle?: LineStyle };
      const changes: { color?: string; visible?: boolean; lineWidth?: LineWidth; lineStyle?: LineStyle } = {};
      if (options.color !== spec.color) changes.color = spec.color;
      if ((options.visible ?? true) === !!spec.hidden) changes.visible = !spec.hidden;
      const wanted = lineOptions(spec);
      if (wanted.lineWidth !== undefined && options.lineWidth !== wanted.lineWidth) changes.lineWidth = wanted.lineWidth;
      if (wanted.lineStyle !== undefined && options.lineStyle !== wanted.lineStyle) changes.lineStyle = wanted.lineStyle;
      if (Object.keys(changes).length > 0) entry.series.applyOptions(changes);
      const colorKey = upDownKey(spec);
      if (entry.lastData !== spec.data || entry.lastUpDown !== colorKey) {
        setSeriesData(entry.series, paintedData(spec));
        entry.lastData = spec.data;
        entry.lastUpDown = colorKey;
      }
    }

    // Panes stack in the panes prop's order, so a volume pane switched back on (created after
    // the indicator panes) moves up to sit first under the price pane, and a pane the eye brings
    // back returns to its old place among the others. paneIndex() is live.
    const stacked = new Set<IPaneApi<Time>>();
    for (const spec of placed) {
      const pane = registry.get(spec.id)?.pane;
      if (!pane || stacked.has(pane)) continue;
      stacked.add(pane);
      if (pane.paneIndex() !== stacked.size) {
        pane.moveTo(stacked.size);
        panesChanged = true;
      }
    }
    if (panesChanged || !laidOutRef.current) {
      laidOutRef.current = layoutPaneHeights(chart, registry, heightsBefore, laidOutRef.current, collapsedHeightsRef.current);
    }

    // Story 32.1: exactly one gap painter per non-overlay pane (volume and indicator panes),
    // on the first of its series still registered.
    for (const entry of registry.values()) {
      if (!entry.pane || [...registry.values()].some((e) => e.pane === entry.pane && e.gap)) continue;
      entry.gap = new GapPrimitive(gapColor(), { label: false });
      entry.series.attachPrimitive(entry.gap);
      entry.gap.setRuns(gapRunsRef.current);
    }

    // Legend rows follow the panes prop's order (= stacking order), including a collapsed
    // indicator, whose row is kept -- crossed -- on the price pane's legend (`pane: null`).
    legendItemsRef.current = panes.map((spec): LegendSeries => {
      const e = registry.get(spec.id);
      return {
        group: spec.group ?? spec.id,
        groupLabel: spec.groupLabel ?? spec.id,
        outputLabel: spec.outputLabel ?? spec.id,
        color: spec.color,
        series: e?.series ?? null,
        data: spec.data,
        pane: e?.pane ?? null,
        hidden: spec.hidden === true,
        configurable: spec.configurable !== false,
        actionable: spec.actionable !== false,
      };
    });
    // A new pane's element only exists after the library's next paint: retry per frame
    // (bounded) until every pane has one.
    let frame = 0;
    let tries = 30;
    const draw = (): void => {
      if (renderLegends(chart, legendItemsRef.current, null, gapLookupRef.current, handleLegendAction) || tries-- <= 0) return;
      frame = requestAnimationFrame(draw);
    };
    draw();
    return () => cancelAnimationFrame(frame);
  }, [panes, handleLegendAction]);

  useEffect(() => {
    // Story 32.1: the price pane's labelled gap painter lives on the current host series (the
    // candlestick series, or the first Lines series), attached once per host; every data
    // change pushes the fresh runs to it and to each non-overlay pane's painter.
    const host = mode === "candles" ? seriesRef.current : (lineSeriesRef.current?.bid ?? null);
    if (!host) return;
    let priceGap = priceGapRef.current;
    if (priceGap?.host !== host) {
      priceGap = {
        host,
        primitive: new GapPrimitive(gapColor(), { label: true, fontFamily: cssVar("--font-terminal", "monospace") }),
      };
      host.attachPrimitive(priceGap.primitive);
      priceGapRef.current = priceGap;
    }
    priceGap.primitive.setRuns(gapRuns);
    for (const entry of panesRef.current.values()) entry.gap?.setRuns(gapRuns);
  }, [gapRuns, mode]);

  useEffect(() => {
    // Story 15.5's live edge: paints the already-aggregated forming bar `useLiveCandle`
    // handed it via update(), never a full setData() (AD-F7: the frontend never
    // re-aggregates). A no-op in Lines mode (`seriesRef.current` is `null` there).
    // Declared AFTER the panes effect on purpose: both fire in the commit that closes a
    // bar (ChartPage promotes the closed bar into `data`/`volume`), and the volume pane's
    // setData() must not run after -- and erase -- this update.
    if (!liveBar) return;
    const series = seriesRef.current;
    if (!series) return;
    // A held bar older than the series' newest point (this effect re-firing on a
    // `panes`/`data` change, or a stray message after a bar-size switch) is skipped, not
    // thrown on: lightweight-charts' "Cannot update oldest data" would escape the effect
    // and kill every later live update.
    const time = liveBar.time as unknown as number;
    if (lastPaintedTimeRef.current !== null && time < lastPaintedTimeRef.current) return;
    lastPaintedTimeRef.current = time;
    // The server seeds its forming bar with the whole bucket (LiveCandleBus.seed), so it is
    // painted as-is -- no client-side merge with history (one aggregation path, AD-F7).
    const { volume, ...candle } = liveBar;
    series.update(candle);
    // Volume pane follows the forming bar; its series only exists once the panes effect has
    // added it, and is absent in Lines mode's registry-less state -- both are no-ops.
    panesRef.current.get("volume")?.series.update({ time: liveBar.time, value: volume });
    // `mode`: Lines -> Candles recreates seriesRef with no data. `data`: every setData()
    // replaces the series with history that lacks the forming bar. Both must repaint the
    // held `liveBar` at once, not wait for the next websocket tick.
  }, [liveBar, mode, panes, data]);

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
            lineStyle: LineStyle.Solid,
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

  const crosshairShownRef = useRef(true);
  useEffect(() => {
    // Story 18.10: only a real change reaches the chart (the initial "on" is the library's
    // own default, so mount adds no applyOptions call). The crosshair MODE stays untouched
    // (the library default snaps to data, and hover/drag handling here depends on its
    // events still flowing) -- "off" only hides the lines and their axis labels.
    const chart = chartRef.current;
    if (!chart || crosshairShownRef.current === crosshairVisible) return;
    crosshairShownRef.current = crosshairVisible;
    const line = { visible: crosshairVisible, labelVisible: crosshairVisible };
    chart.applyOptions({ crosshair: { vertLine: line, horzLine: line } });
  }, [crosshairVisible]);

  useEffect(() => {
    if (!viewCommand) return;
    const timeScale = chartRef.current?.timeScale();
    if (!timeScale) return;
    if (viewCommand.kind === "fit") timeScale.fitContent();
    else timeScale.scrollToRealTime();
  }, [viewCommand]);

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
    markerRef.current = new VerticalMarkerPrimitive(markerTime, chartVar("--chart-marker"));
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

    const preview = new MeasurementPrimitive(chartVar("--chart-drawing"));
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

    const primitive = new MeasurementPrimitive(chartVar("--chart-drawing"));
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
      dragMovedRef.current = true;
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
    // Legend values follow the crosshair; off-chart (time undefined) they fall back to the
    // latest value. Its own subscription, declared after the drag one above.
    const chart = chartRef.current;
    if (!chart) return;
    const handle = (param: MouseEventParams): void => {
      renderLegends(chart, legendItemsRef.current, param, gapLookupRef.current, handleLegendAction);
    };
    chart.subscribeCrosshairMove(handle);
    return () => chart.unsubscribeCrosshairMove(handle);
  }, [mode, handleLegendAction]);

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
      dragMovedRef.current = false;
      // A grab whose release happens outside the chart never fires a chart click, so
      // the suppression flag would otherwise swallow the NEXT real click -- every
      // non-grab mousedown clears it, keeping it true only from grab to click.
      suppressNextClickRef.current = false;
      if (grabbedId === null) return;
      event.stopPropagation();
    };

    const handleMouseUp = (): void => {
      // With no tool armed, an unmoved grab is a plain click (opens the edit menu); a real
      // drag, or any grab while a tool is armed, swallows the click that follows.
      suppressNextClickRef.current =
        dragIdRef.current !== null && (dragMovedRef.current || !editRef.current.drawEditable);
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
    if (!chart || (!wantsPrice && !onPointClick && !drawEditable)) return;

    const handleClick = (param: MouseEventParams): void => {
      if (suppressNextClickRef.current) {
        suppressNextClickRef.current = false;
        return;
      }
      if (!param.point) return;
      if (editRef.current.drawEditable) {
        const hitId = findClickedDrawingId(param.point, seriesRef.current, drawingRegistryRef.current, editRef.current.priceLines);
        const ev = param.sourceEvent;
        setMenu(hitId && ev ? { id: hitId, x: ev.clientX, y: ev.clientY } : null);
      }
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
  }, [onPriceClick, onPointClick, drawEditable, mode]);

  useEffect(() => {
    // Trendline preview: the pending first anchor drawn to the cursor until the second click.
    const chart = chartRef.current;
    const host = seriesRef.current ?? lineSeriesRef.current?.price;
    if (!chart || !host || !pendingAnchor) return;
    const preview = new TrendlinePrimitive([pendingAnchor, pendingAnchor], chartVar("--chart-drawing"));
    host.attachPrimitive(preview);
    previewRef.current = preview;
    const move = (param: MouseEventParams): void => {
      const price = param.point ? host.coordinateToPrice(param.point.y) : null;
      const time = param.point ? (param.time ?? chart.timeScale().coordinateToTime(param.point.x)) : null;
      if (price !== null && time !== null) preview.update([pendingAnchor, { time, price }], chartVar("--chart-drawing"));
    };
    chart.subscribeCrosshairMove(move);
    return () => {
      chart.unsubscribeCrosshairMove(move);
      host.detachPrimitive(preview);
      previewRef.current = null;
    };
  }, [pendingAnchor, mode]);

  const menuSpec = menu ? (priceLines.find((l) => l.id === menu.id) ?? drawings.find((d) => d.id === menu.id)) : undefined;
  const menuColor = /^#[0-9a-f]{6}$/i.test(menuSpec?.color ?? "") ? menuSpec!.color : chartVar("--chart-drawing");
  return (
    <>
      <div ref={containerRef} />
      {menu && menuSpec && (
        <div
          role="menu"
          aria-label="Drawing options"
          style={{
            position: "fixed", left: menu.x + 8, top: menu.y + 8, zIndex: 1000, display: "flex", gap: 8,
            alignItems: "center", padding: 6, background: chartVar("--chart-bg"),
            border: `1px solid ${chartVar("--chart-border")}`,
          }}
        >
          <input
            type="color"
            aria-label="Line color"
            value={menuColor}
            onChange={(e) => onDrawingColor?.(menu.id, e.target.value)}
          />
          <button
            type="button"
            className="tabbtn"
            onClick={() => {
              onDrawingDelete?.(menu.id);
              setMenu(null);
            }}
          >
            Delete
          </button>
        </div>
      )}
    </>
  );
}
