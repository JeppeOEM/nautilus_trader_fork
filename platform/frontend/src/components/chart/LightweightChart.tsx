import {
  AreaSeries,
  BarSeries,
  BaselineSeries,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type IPaneApi,
  type IPriceLine,
  LineStyle,
  PriceScaleMode,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineWidth,
  type LogicalRange,
  type LineData,
  type MouseEventParams,
  type Time,
  type WhitespaceData,
} from "lightweight-charts";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { ChartDatum, VolumeDatum } from "../../hooks/useCandles";
import type { LiveBar } from "../../hooks/useLiveCandle";
import type { SnapshotLinesData } from "../../hooks/useSnapshotSeries";
import { type GapRun, MAX_GAP_ROWS_PER_GAP, findGapRuns, gapRunsBySlot } from "../../lib/gaps";
import {
  type GapLookup,
  type IndicatorDatum,
  type LegendAction,
  type LegendSeries,
  renderLegends,
} from "./legend";
import { DEFAULT_LINE_STYLE, DEFAULT_LINE_WIDTH, type LineStyleName } from "../../lib/indicatorStyle";
import { chartVar, chartVarAlpha, fibLevelColor } from "./chartTheme";
import {
  type ChartType,
  type MainRow,
  type MainSeriesKind,
  PRICE_SCALE_LABELS,
  PRICE_SCALE_MODES,
  type PriceScaleModeName,
  type UpDownColors,
  firstVisibleClose,
  liveSeriesRow,
  seriesKindOf,
  seriesRows,
} from "../../lib/chartTypes";
import { assignPaneColor, cssVar } from "./paneColors";
import {
  type MeasurementIndex,
  MeasurementPrimitive,
  buildMeasurementIndex,
  computeMeasurement,
  formatMeasurement,
} from "./primitives/MeasurementPrimitive";
import { attachRangeDrag, localPoint, plotPoint, timeAtX } from "./rangeDrag";
import { VolumeProfilePrimitive, type VolumeProfileRenderSpec } from "./primitives/VolumeProfilePrimitive";
import { VerticalMarkerPrimitive } from "./primitives/VerticalMarkerPrimitive";
import { GapPrimitive } from "./primitives/GapPrimitive";
import { TrendlinePrimitive, type TrendlineAnchor } from "./primitives/TrendlinePrimitive";
import { FibPrimitive } from "./primitives/FibPrimitive";
import { PositionPrimitive } from "./primitives/PositionPrimitive";
import { AnchoredVpPrimitive } from "./primitives/AnchoredVpPrimitive";
import { AnchoredVwapPrimitive } from "./primitives/AnchoredVwapPrimitive";
import { FootprintPrimitive, type FootprintRenderSpec } from "./primitives/FootprintPrimitive";
import { footprintLegendText } from "../../lib/footprint";
import type { MarkerSpec } from "./LiquidationMarkers";
import type { AlertCondition } from "../../lib/alertConditions";
import { BarGrid, type DrawingPrimitive } from "./primitives/drawingPrimitive";
import type { VwapPoint } from "../../lib/anchoredVwap";
import {
  type Anchor,
  type AnchoredVpDrawing,
  type AnchoredVwapDrawing,
  type DragPoint,
  type FibDrawing,
  type InstrumentPrecision,
  type PositionDrawing,
  defaultFibLevels,
} from "../../lib/drawings";

export type PaneSeriesKind = "Line" | "Histogram";

export type ChartMode = "candles" | "lines";

/** Story 33.9: the right price scale of the price pane, as the page wants it on screen (the mode is
 * the effective one: Percent while a compare is drawn). */
export interface PriceScaleSettings {
  mode: PriceScaleModeName;
  autoScale: boolean;
  invert: boolean;
}

/** A change to the price scale the operator made on the chart itself (the scale's menu, a double-click
 * or a drag of the scale), in the layout's own field names. */
export interface PriceScalePatch {
  mode?: PriceScaleModeName;
  auto_scale?: boolean;
  invert?: boolean;
}

/** The library's `PriceScaleMode` of each stored mode name. */
const SCALE_MODE_OF: Record<PriceScaleModeName, PriceScaleMode> = {
  normal: PriceScaleMode.Normal,
  log: PriceScaleMode.Logarithmic,
  percent: PriceScaleMode.Percentage,
  indexed: PriceScaleMode.IndexedTo100,
};

const DEFAULT_PRICE_SCALE_SETTINGS: PriceScaleSettings = { mode: "normal", autoScale: true, invert: false };

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
  /** False for a row with no settings: it gets the eye and the x but no gear (Volume has one since
   * Story 33.6: its colour mode). */
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
  /** Story 33.5: how the legend prints this series' value at a slot (`time`, chart seconds) -- the
   * exact text its row carries there, through `lib/units.ts` -- instead of the plain readout. */
  format?: (value: number, time: number | null) => string;
  /** Story 33.5: a fixed legend readout in place of the value (e.g. "load failed"). */
  text?: string;
  /** Story 33.5: a dashed line at 0 in this series' pane (Basis). */
  zeroLine?: boolean;
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
  anchors: [Anchor, Anchor];
  color: string;
}
// Story 32.5: the Fibonacci retracement and the Long/Short position are the same kind of drawing
// (anchors in time + price, a series primitive); their specs are `lib/drawings.ts`'s own types.
// Story 32.7: the Anchored VP (its anchor marker and handle; the profile itself is a `volumeProfiles`
// spec) and the Anchored VWAP (its computed points) join them, each carrying what the page computed.
export interface AnchoredVpSpec extends AnchoredVpDrawing {
  /** The price the anchor handle sits at (the profile's top), null while the profile has no rows. */
  anchorPrice: number | null;
}
export interface AnchoredVwapSpec extends AnchoredVwapDrawing {
  points: readonly VwapPoint[];
}
export type DrawingSpec = TrendlineSpec | FibDrawing | PositionDrawing | AnchoredVpSpec | AnchoredVwapSpec;
type DrawingPrimitiveOf =
  | TrendlinePrimitive
  | FibPrimitive
  | PositionPrimitive
  | AnchoredVpPrimitive
  | AnchoredVwapPrimitive;

/** A drawing's primitive, new. */
function createDrawingPrimitive(
  spec: DrawingSpec,
  precision: InstrumentPrecision | null,
  grid: BarGrid,
): DrawingPrimitiveOf {
  switch (spec.kind) {
    case "trendline":
      return new TrendlinePrimitive(spec.anchors, spec.color, grid);
    case "fib":
      return new FibPrimitive(spec, precision?.price ?? null, grid);
    case "position":
      return new PositionPrimitive(spec, precision, grid);
    case "anchored_vp":
      return new AnchoredVpPrimitive(spec.time, spec.anchorPrice, grid);
    case "anchored_vwap":
      return new AnchoredVwapPrimitive(spec, spec.points);
  }
}

/** Hands a registered primitive its drawing's current spec (the primitive is of that spec's kind). */
function updateDrawingPrimitive(primitive: DrawingPrimitiveOf, spec: DrawingSpec, precision: InstrumentPrecision | null): void {
  switch (spec.kind) {
    case "trendline":
      (primitive as TrendlinePrimitive).update(spec.anchors, spec.color);
      break;
    case "fib":
      (primitive as FibPrimitive).update(spec, precision?.price ?? null);
      break;
    case "position":
      (primitive as PositionPrimitive).update(spec, precision);
      break;
    case "anchored_vp":
      (primitive as AnchoredVpPrimitive).update(spec.time, spec.anchorPrice);
      break;
    case "anchored_vwap":
      (primitive as AnchoredVwapPrimitive).update(spec, spec.points);
      break;
  }
}
interface DrawingEntry {
  kind: DrawingSpec["kind"];
  primitive: DrawingPrimitiveOf;
}

/** What the pointer grabbed: a drawing and the named handle of it (null: only its body). */
interface GrabTarget {
  id: string;
  handle: string | null;
}

// Story 18.5: a Volume Profile placed on the main pane; `id` is the caller's stable key.
export interface VolumeProfileSpec extends VolumeProfileRenderSpec {
  id: string;
}

/** A read-only legend row for something that is not an indicator pane (Story 32.7). */
export interface LegendExtra {
  id: string;
  label: string;
  color: string;
  /** The value shown, or null while there is none (a VWAP with no volume yet). */
  value: number | null;
  format: (value: number) => string;
  /** Story 33.6: a fixed readout in place of the value (a stored VWAP's replay error). */
  text?: string;
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
  /** Story 32.5: the menu's "Settings..." entry, shown for the kinds that have a modal (Fibonacci,
   * position). */
  onDrawingSettings?: (id: string) => void;
  /** Story 33.8: the menu's "Add alert…" entry, on a horizontal line (a `price_cross` at its price)
   * or a trendline (a `trendline_cross` naming it); the page opens its alert dialog prefilled. */
  onDrawingAlert?: (condition: AlertCondition) => void;
  /** Story 32.5: the instrument's price/size decimals (the catalog definition's, from the candles
   * response); `null` until known, and then no drawing prints a label. */
  precision?: InstrumentPrecision | null;
  /** Story 32.5: a live drag of a drawing's handle (an anchor, a position's target / stop / entry /
   * right edge), reported on every move with the pointer's bar time and price; this component
   * never changes the spec itself. Horizontal lines keep `onPriceLineDrag`. */
  onDrawingDrag?: (id: string, handle: string, point: DragPoint) => void;
  /** Story 32.5: while true, a click-drag draws a live Fibonacci preview (anchor A at the press,
   * B at the pointer) instead of panning, and release reports exactly one `onFibPlace(a, b)`; a
   * click without a drag reports nothing. Esc / disarm cancels with no residue. */
  fibActive?: boolean;
  onFibPlace?: (a: TrendlineAnchor, b: TrendlineAnchor) => void;
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
  /** DW-145: while true (a replay is active), a candles `setData` whose newest bar time changed
   * scrolls that bar into view when it is off-screen -- the visible width kept, the bar at the
   * right edge. A refill that only prepends older bars (newest time unchanged) never follows,
   * and flipping this prop alone moves nothing. Default false: the data effects leave the view. */
  followNewest?: boolean;
  /** Story 32.7: where the Auto Anchored profile starts: a vertical marker line at this time (its own
   * `VerticalMarkerPrimitive`, apart from the replay marker); `null`/omitted removes it. */
  anchorMarkerTime?: Time | null;
  /** Story 32.7: one legend row per entry on the price pane (a drawing's current value, e.g. the
   * Anchored VWAP), read-only: no eye, gear or x. */
  legendExtras?: LegendExtra[];
  /** Story 18.10: crosshair on/off (the left toolbar's toggle); default on. */
  crosshairVisible?: boolean;
  /** Story 18.10: applied once per new command object -- `fit` snaps the visible range to
   * all loaded data, `latest` scrolls to the newest bar. The only user-commanded view move;
   * otherwise the view changes only through the candles data effect's own keeping (scroll-back
   * prepend compensation, the Story 32.6 initial zoom and the opt-in `followNewest`). */
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
  /** DW-150: an edge drag ended without a release -- the edge effect was torn down mid-drag (a
   * tool armed, the mode flipped, unmount). Called once; nothing is committed, so the caller only
   * clears its ghost. */
  onProfileEdgeCancel?: () => void;
  /** Story 15.5: the currently-forming candle bar, from `useLiveCandle`. Applied via
   * `series.update()` (not `setData()`) on the candlestick series only -- independent of
   * the `data`/`setData()` effect above and Story 15.4's `panes` effect below; neither of
   * those is touched by this prop. `null`/`undefined` means "no live bar yet" (e.g.
   * before the live socket's first message, or synchronously reset on instrument/bar-size
   * change) and is a no-op, not a clear of the last-drawn bar. */
  liveBar?: LiveBar | null;
  /** Story 33.6: the forming bar's Volume colour (the page's `volume_color_by` mapping, the same one
   * its closed bars are painted with); absent = the volume pane's own colour. */
  liveVolumeColor?: string;
  /** Story 32.8: the volume footprint of the closed bars, drawn by one `FootprintPrimitive` on the
   * candle series (Candles mode only) with a "Footprint" legend row (gear and x) on the price pane;
   * `null`/omitted removes both. */
  footprint?: FootprintRenderSpec | null;
  /** Story 32.3: a legend eye / gear / x was pressed; `group` is the indicator instance id (the
   * spec's `group`), "volume" or "footprint". The page persists the change; this component only
   * reports. */
  onLegendAction?: (action: LegendAction, group: string) => void;
  /** Story 32.6: the pane heights (px, by pane group id; "price" for the main pane) a saved layout
   * restores. Read once at mount, then kept as the last known heights. */
  initialPaneHeights?: Record<string, number>;
  /** Story 32.6: the operator released the pointer after a press that changed a pane's height (a
   * divider drag): the heights of every pane then, by the same ids. Never fired for a relayout this
   * component did itself (a pane added or removed). */
  onPaneHeights?: (heights: Record<string, number>) => void;
  /** Story 32.6: how many bars to show, the latest at the right edge, applied once when the first
   * candles of the Candles mode arrive. The zoom only, never an absolute scroll position. */
  initialVisibleBars?: number;
  /** Story 32.6: the visible bar count after a zoom, debounced; silent until `initialVisibleBars`
   * was applied (the library's own first fit must not overwrite the saved zoom) and in Lines mode. */
  onVisibleBars?: (bars: number) => void;
  /** Story 33.5: liquidation markers on the candle series (Candles mode only), one lightweight-charts
   * `createSeriesMarkers` plugin set on every change and detached on unmount; hovering one shows its
   * `tooltip` lines. Empty/omitted draws none. */
  liquidationMarkers?: readonly MarkerSpec[];
  /** Story 33.5: the time scale's bar spacing (px), reported on mount and on every zoom, so the page
   * can hide markers too narrow to read. */
  onBarSpacing?: (barSpacing: number) => void;
  /** Story 33.9: how the main series is drawn in Candles mode (default `candles`). Its rows come from
   * `lib/chartTypes.ts`'s `seriesRows` and go to the main series' `setData`/`update` alone (AD-F6):
   * every other consumer here -- gap runs, measure, profiles, footprint, markers, price lines,
   * drawings, the legend -- keeps reading the real `data`. A type drawn by another series definition
   * replaces the main series (the same teardown and re-attach as a mode switch) without a view reset.
   *
   * Known limit: in Percent / Indexed to 100 the library converts price <-> y through each series' own
   * first visible value, so while Heikin Ashi is drawn a click, a drag or a drawing converts through
   * the HA series' first close rather than the real one (Normal and Log are exact: no base value).
   * Upgrade path: host the drawings on a hidden real-close series in those modes. */
  chartType?: ChartType;
  /** Story 33.9: the price pane's right scale; default Normal, auto-scaled, not inverted. */
  priceScale?: PriceScaleSettings;
  /** Story 33.9: the operator changed the scale on the chart (its right-click menu, a double-click on
   * it that restores auto-scale, or a drag of it that turned auto-scale off). The page persists it. */
  onPriceScale?: (patch: PriceScalePatch) => void;
  /** Story 33.9: why the scale menu's Normal and Log entries are disabled (a compare forces Percent);
   * null/omitted enables them. */
  scaleModesLocked?: string | null;
  /** Story 33.9: the read-only legend row shown while Heikin Ashi is the chart type. */
  heikinLabel?: string;
}

/** DW-145: after a replay `setData`, bring the newest bar back into view when its time changed
 * and it lies outside the visible logical range: the range keeps its width and ends half a bar
 * past it. lightweight-charts keeps the right offset relative to the last bar, so at the
 * realtime edge the head is already visible and this is a no-op; it acts when the view was
 * scrolled back before picking, or panned away while paused. */
function followNewestBar(chart: IChartApi, data: readonly ChartDatum[], prevNewest: number | null): void {
  if (data.length === 0) return;
  const last = data.length - 1;
  if ((data[last].time as unknown as number) === prevNewest) return;
  const range = chart.timeScale().getVisibleLogicalRange();
  // The whole bar (its index +/- half a slot) must be inside the range: a head half clipped at
  // either edge is followed too.
  if (!range || (last - 0.5 >= range.from && last + 0.5 <= range.to)) return;
  const width = range.to - range.from;
  chart.timeScale().setVisibleLogicalRange({ from: last + 0.5 - width, to: last + 0.5 });
}

/** Quiet period after the last visible-range event before the zoom is reported. */
export const VISIBLE_BARS_DEBOUNCE_MS = 300;

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
  /** Story 33.5: the spec's dashed zero line, while `zeroLine` is set. */
  zero: IPriceLine | null;
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
/** The main series of Candles mode, whichever definition its chart type draws with (Story 33.9). */
export type MainSeriesApi = ISeriesApi<MainSeriesKind, Time>;

/** Adds the main series of `kind` on the price pane, every colour from the chart tokens (Story 32.4). */
function addMainSeries(chart: IChartApi, kind: MainSeriesKind): MainSeriesApi {
  const up = chartVar("--chart-up");
  const down = chartVar("--chart-down");
  switch (kind) {
    case "Candlestick": {
      const dim = chartVar("--chart-text-dim");
      return chart.addSeries(CandlestickSeries, {
        upColor: up,
        downColor: down,
        borderUpColor: up,
        borderDownColor: down,
        wickUpColor: up,
        wickDownColor: down,
        // The base borderColor/wickColor fields (as opposed to the Up/Down variants above) are
        // vestigial fallbacks whose library defaults are otherwise never overridden -- set explicitly
        // so nothing non-token-derived can ever render.
        borderColor: dim,
        wickColor: dim,
      });
    }
    case "Bar":
      return chart.addSeries(BarSeries, { upColor: up, downColor: down });
    case "Line":
      return chart.addSeries(LineSeries, { color: chartVar("--chart-line") });
    case "Area":
      return chart.addSeries(AreaSeries, {
        lineColor: chartVar("--chart-line"),
        topColor: chartVarAlpha("--chart-line", 0.28),
        bottomColor: chartVarAlpha("--chart-line", 0.02),
      });
    case "Baseline":
      return chart.addSeries(BaselineSeries, {
        topLineColor: up,
        topFillColor1: chartVarAlpha("--chart-up", 0.28),
        topFillColor2: chartVarAlpha("--chart-up", 0.05),
        bottomLineColor: down,
        bottomFillColor1: chartVarAlpha("--chart-down", 0.05),
        bottomFillColor2: chartVarAlpha("--chart-down", 0.28),
      });
  }
}

/** Whether a client point lies on the price pane's right scale strip. In container x the strip starts
 * after a visible left price scale and the plot (`left.width() + timeScale.width()`), never at the plot
 * width alone. */
function onRightScale(container: HTMLElement, chart: IChartApi, clientX: number, clientY: number): boolean {
  const height = chart.panes()[0]?.getHeight() ?? 0;
  const box = container.getBoundingClientRect();
  const x = clientX - box.left;
  const y = clientY - box.top;
  const start = chart.priceScale("left").width() + chart.timeScale().width();
  return height > 0 && y >= 0 && y < height && x >= start && x < start + chart.priceScale("right").width();
}

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

/** Every laid-out pane's current pixel height by group id ("price" for the main pane). */
function currentPaneHeights(chart: IChartApi, registry: Map<string, PaneEntry>): Record<string, number> {
  const out: Record<string, number> = {};
  const price = chart.panes()[0]?.getHeight() ?? 0;
  if (price > 0) out.price = Math.round(price);
  for (const entry of registry.values()) {
    const px = entry.pane?.getHeight() ?? 0;
    if (entry.pane && px > 0) out[entry.group] = Math.round(px);
  }
  return out;
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
  known: Map<string, number>,
): boolean {
  // `known`: the heights a saved layout restored or the operator last dragged to (Story 32.6). It
  // ranks below a pane's own measured height and a collapsed pane's remembered one, above the default.
  const pricePx = priceKnown ? (before.price ?? known.get("price") ?? PRICE_PANE_PX) : (known.get("price") ?? PRICE_PANE_PX);
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
    const px = (priceKnown ? before.panes.get(entry.pane) : undefined) ?? remembered.get(entry.group) ?? known.get(entry.group) ?? fallback;
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

/** Adds or removes a series' dashed zero line to match its spec (Story 33.5's Basis pane). */
function syncZeroLine(entry: PaneEntry): void {
  const wanted = entry.spec.zeroLine === true;
  if (wanted && !entry.zero) {
    entry.zero = entry.series.createPriceLine({
      price: 0,
      color: chartVar("--chart-text-dim"),
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
  } else if (!wanted && entry.zero) {
    entry.series.removePriceLine(entry.zero);
    entry.zero = null;
  }
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
 * The one hit-test of every drawing (Stories 18.1/18.2, unified by 32.5): what is under `point`.
 * A handle beats a body: the nearest handle of any registered drawing (an anchor, a position's
 * target / stop / entry / right edge) or, when `hlineReachable`, the nearest horizontal price
 * line (handle `"price"`, within the library's own price-line radius); only without a handle does
 * the nearest drawing body count. `primitives` false leaves the primitive drawings out (a grab
 * outside the Cursor tool), `bodies` false asks for handles alone (a grab starts only on one).
 * `hlineReachable` is the caller's call: a grab asks the library whether the pointer is over a
 * price line, the edit menu does not need to.
 */
function findDrawingHit(
  point: { x: number; y: number },
  series: MainSeriesApi | null,
  drawings: Map<string, DrawingEntry>,
  specs: PriceLineSpec[],
  options: { hlineReachable: boolean; primitives: boolean; bodies: boolean },
): GrabTarget | null {
  let handleHit: (GrabTarget & { distance: number }) | null = null;
  let bodyHit: (GrabTarget & { distance: number }) | null = null;
  for (const [id, entry] of options.primitives ? drawings : []) {
    const hit = (entry.primitive as DrawingPrimitive).hit(point.x, point.y);
    if (!hit) continue;
    if (hit.handle !== null) {
      if (handleHit === null || hit.distance < handleHit.distance) handleHit = { id, handle: hit.handle, distance: hit.distance };
    } else if (bodyHit === null || hit.distance < bodyHit.distance) {
      bodyHit = { id, handle: null, distance: hit.distance };
    }
  }
  for (const spec of options.hlineReachable ? specs : []) {
    const y = series?.priceToCoordinate(spec.price);
    if (y === null || y === undefined) continue;
    const distance = Math.abs(point.y - y);
    if (distance <= PRICE_LINE_GRAB_TOLERANCE_PX && (handleHit === null || distance < handleHit.distance)) {
      handleHit = { id: spec.id, handle: "price", distance };
    }
  }
  const best = handleHit ?? (options.bodies ? bodyHit : null);
  return best && { id: best.id, handle: best.handle };
}

/** Whether the library's crosshair param says the pointer is over one of the main series' price lines. */
function overPriceLine(param: MouseEventParams, series: MainSeriesApi | null): boolean {
  const info = param.hoveredInfo;
  return series !== null && info?.objectKind === "custom-price-line" && info.series === series;
}

// The measurement index before the first history arrives; the effect replaces it.
const EMPTY_MEASUREMENT_INDEX: MeasurementIndex = buildMeasurementIndex([], []);
const NO_MARKERS: readonly MarkerSpec[] = [];

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
  onDrawingSettings,
  onDrawingAlert,
  precision = null,
  onDrawingDrag,
  fibActive = false,
  onFibPlace,
  onPointClick,
  measureActive = false,
  volume = [],
  onMeasureEnd,
  markerTime = null,
  followNewest = false,
  anchorMarkerTime = null,
  legendExtras = [],
  footprint = null,
  crosshairVisible = true,
  viewCommand = null,
  volumeProfiles = [],
  rangeSelectActive = false,
  onRangeSelect,
  profileEdgesEditable = true,
  onProfileEdgeDrag,
  onProfileEdgeCommit,
  onProfileEdgeCancel,
  liveBar,
  liveVolumeColor,
  onLegendAction,
  initialPaneHeights,
  onPaneHeights,
  initialVisibleBars,
  onVisibleBars,
  liquidationMarkers = NO_MARKERS,
  onBarSpacing,
  chartType = "candles",
  priceScale = DEFAULT_PRICE_SCALE_SETTINGS,
  onPriceScale,
  scaleModesLocked = null,
  heikinLabel = "Heikin Ashi (derived)",
}: LightweightChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<MainSeriesApi | null>(null);
  // Story 33.9: the definition the main series was created with, and the view a series swap keeps.
  const seriesKindRef = useRef<MainSeriesKind | null>(null);
  const keptRangeRef = useRef<LogicalRange | null>(null);
  // Null in Lines mode, where no main series exists: a chart type change there must not re-run the
  // host swap and the primitive effects keyed on it (they would re-attach beside the live ones).
  const mainKind = mode === "candles" ? seriesKindOf(chartType) : null;
  const lineSeriesRef = useRef<Record<LineSeriesId, MainLineSeriesApi> | null>(null);
  const prevFirstTimeRef = useRef<Time | null>(null);
  const prevLinesLengthRef = useRef(0);
  const panesRef = useRef<Map<string, PaneEntry>>(new Map());
  const legendItemsRef = useRef<LegendSeries[]>([]);
  // Story 32.3: the px height of each pane the legend eye collapsed, by group, until it is shown.
  const collapsedHeightsRef = useRef<Map<string, number>>(new Map());
  // Story 32.6: last known pane heights by group id ("price" included): the saved layout's at mount,
  // then whatever the operator dragged to. A ref read at mount, so a later prop change re-pins nothing.
  const knownHeightsRef = useRef<Map<string, number>>(new Map(Object.entries(initialPaneHeights ?? {})));
  const paneHeightsCallbackRef = useRef(onPaneHeights);
  paneHeightsCallbackRef.current = onPaneHeights;
  const visibleBarsCallbackRef = useRef(onVisibleBars);
  visibleBarsCallbackRef.current = onVisibleBars;
  // Whether the saved zoom was applied (reports are held back until then) and the count last known.
  const visibleBarsReadyRef = useRef(false);
  const lastVisibleBarsRef = useRef<number | null>(null);
  const initialVisibleBarsRef = useRef(initialVisibleBars);
  // Read by the candles data effect, never one of its deps: a prop flip must not re-run setData.
  const followNewestRef = useRef(followNewest);
  followNewestRef.current = followNewest;
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
  const dragIdRef = useRef<GrabTarget | null>(null);
  const suppressNextClickRef = useRef(false);
  const dragMovedRef = useRef(false);
  const previewRef = useRef<TrendlinePrimitive | null>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const editRef = useRef({ priceLines, drawEditable, onDrawingDrag });
  editRef.current = { priceLines, drawEditable, onDrawingDrag };
  // Filled by the effect below before any drag can read it; the forming bar is read per move.
  const measureIndexRef = useRef<MeasurementIndex>(EMPTY_MEASUREMENT_INDEX);
  const liveBarRef = useRef(liveBar);
  liveBarRef.current = liveBar;
  const profileRegistryRef = useRef<Map<string, VolumeProfilePrimitive>>(new Map());
  // Latest-callback/latest-specs refs: the drag effects below must not re-subscribe (and
  // lose an in-flight drag) whenever the caller re-renders with fresh closures or specs.
  const latestRef = useRef({ volumeProfiles, onRangeSelect, onProfileEdgeDrag, onProfileEdgeCommit, onProfileEdgeCancel });
  const markerRef = useRef<VerticalMarkerPrimitive | null>(null);
  const anchorMarkerRef = useRef<VerticalMarkerPrimitive | null>(null);
  const footprintRef = useRef<FootprintPrimitive | null>(null);
  // Story 33.5: the liquidation markers' plugin on the candle series, and the hovered marker's tooltip.
  const markersPluginRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const markerSpecsRef = useRef<readonly MarkerSpec[]>(liquidationMarkers);
  markerSpecsRef.current = liquidationMarkers;
  const [markerTip, setMarkerTip] = useState<{ lines: readonly string[]; x: number; y: number } | null>(null);
  const barSpacingCallbackRef = useRef(onBarSpacing);
  barSpacingCallbackRef.current = onBarSpacing;
  const drawingRegistryRef = useRef<Map<string, DrawingEntry>>(new Map());
  // Story 32.5: the bar times every drawing primitive snaps its anchors to (one grid per chart).
  const gridRef = useRef(new BarGrid());
  const precisionRef = useRef(precision);
  precisionRef.current = precision;
  // Newest time painted on the candlestick series -- the last setData() point or the last
  // live update(), whichever is later. lightweight-charts throws on an update() older than
  // its last point, so the live effect checks against this and every setData() resets it.
  const lastPaintedTimeRef = useRef<number | null>(null);
  // Story 32.1: every gap slot of the price series, painted on the price pane (labelled, on
  // its host series) and on every non-overlay pane (unlabelled, PaneEntry.gap) alike, and
  // looked up by slot time for the legend's crosshair readout.
  const liveTime = liveBar ? (liveBar.time as unknown as number) : undefined;
  // Story 32.8: a string, so the legend effect re-runs on a settings change, never on new bars.
  const footprintLegend =
    footprint && mode === "candles" ? footprintLegendText(footprint.settings.mode, footprint.settings.row_ticks) : null;
  const heikinLegend = chartType === "heikin_ashi" && mode === "candles" ? heikinLabel : null;
  const gapRuns = useMemo(() => priceGapRuns(mode, data, linesData, liveTime), [mode, data, linesData, liveTime]);
  const gapRunsRef = useRef(gapRuns);
  gapRunsRef.current = gapRuns;
  const gapLookupRef = useRef<GapLookup>(new Map());
  gapLookupRef.current = useMemo(() => gapRunsBySlot(gapRuns), [gapRuns]);
  // Story 32.5: every slot of the price series (gap whitespace included, plus the forming live bar
  // drawn with `update()`), so a drawing anchored between two bars sits on the earlier one.
  const barTimes = useMemo(() => {
    const source: readonly { time: Time }[] = mode === "candles" ? data : (linesData?.bid ?? []);
    const times = source.map((d) => d.time as number);
    if (mode === "candles" && liveTime !== undefined && (times.length === 0 || liveTime > times[times.length - 1])) {
      times.push(liveTime);
    }
    return times;
  }, [mode, data, linesData, liveTime]);
  gridRef.current.set(barTimes);
  // False until a layout has sized the chart with the time axis measured: before that the price
  // pane's own height is not the 500 px budget (the library took the axis out of the initial 500).
  const laidOutRef = useRef(false);
  const priceGapRef = useRef<{ host: MainSeriesApi | MainLineSeriesApi; primitive: GapPrimitive } | null>(null);
  // Story 33.9: the main series' rows -- the one place a derived value (Heikin Ashi, hollow colours,
  // a close-only line) exists; read by its `setData`/`update` and the Baseline's base value only.
  const upDown = useMemo<UpDownColors>(() => ({ up: chartVar("--chart-up"), down: chartVar("--chart-down") }), []);
  const mainRows = useMemo<MainRow[]>(() => seriesRows(chartType, data, upDown), [chartType, data, upDown]);
  const [scaleMenu, setScaleMenu] = useState<{ x: number; y: number } | null>(null);
  // The scale menu's measured height, so its `top` keeps it inside the viewport like `right` does.
  const scaleMenuRef = useRef<HTMLDivElement | null>(null);
  const [scaleMenuHeight, setScaleMenuHeight] = useState(0);
  useLayoutEffect(() => {
    if (scaleMenu && scaleMenuRef.current) setScaleMenuHeight(scaleMenuRef.current.offsetHeight);
  }, [scaleMenu]);
  const priceScaleCallbackRef = useRef(onPriceScale);
  priceScaleCallbackRef.current = onPriceScale;
  const autoScaleRef = useRef(priceScale.autoScale);
  autoScaleRef.current = priceScale.autoScale;

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
            knownHeightsRef.current,
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
      footprintRef.current = null;
      // Story 33.5: the markers plugin is detached while its series still exists.
      markersPluginRef.current?.detach();
      markersPluginRef.current = null;
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

  // Story 32.6: a divider drag, as the operator's own pointer press. lightweight-charts 5.2.1 fires no
  // event for it, so heights are read at pointer-down and again at pointer-up (every pane, so a
  // press that did not move a divider reports nothing); the layout this component pins itself
  // (a pane added or removed) happens outside a press and is never reported.
  //
  // Known limit: a pane added or removed by a data refresh while the pointer is down is reported as
  // if dragged (the heights reported are the real ones, so only the saved layout gains them).
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let before: Record<string, number> | null = null;
    const onDown = (): void => {
      const chart = chartRef.current;
      before = chart ? currentPaneHeights(chart, panesRef.current) : null;
    };
    const onUp = (): void => {
      const start = before;
      before = null;
      const chart = chartRef.current;
      if (!start || !chart) return;
      const now = currentPaneHeights(chart, panesRef.current);
      if (!Object.keys(now).some((id) => id in start && Math.abs(now[id] - start[id]) >= 1)) return;
      for (const [id, px] of Object.entries(now)) knownHeightsRef.current.set(id, px);
      paneHeightsCallbackRef.current?.(now);
    };
    // A press the browser cancels (a touch turned into a scroll, a lost pointer) moved no divider:
    // forgetting it keeps a later, unrelated pointerup from reporting a relayout as a drag.
    const onCancel = (): void => {
      before = null;
    };
    container.addEventListener("pointerdown", onDown);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    return () => {
      container.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
    };
  }, []);

  // Story 32.6: the zoom, reported once per burst. Silent until the saved zoom was applied (the data
  // effect below) and while the candlestick series is absent (Lines mode's axis is snapshot seconds,
  // not bars). Known limit: a window resize changes how many bars fit and is reported as a zoom.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const timeScale = chart.timeScale();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let pending: number | null = null;
    const report = (): void => {
      timer = null;
      if (pending === null) return;
      const bars = pending;
      pending = null;
      lastVisibleBarsRef.current = bars;
      visibleBarsCallbackRef.current?.(bars);
    };
    const handler = (range: LogicalRange | null): void => {
      if (!range || !visibleBarsReadyRef.current || seriesRef.current === null) return;
      const bars = Math.round(range.to - range.from);
      if (timer !== null) clearTimeout(timer);
      timer = null;
      pending = bars >= 1 && bars !== lastVisibleBarsRef.current ? bars : null;
      if (pending !== null) timer = setTimeout(report, VISIBLE_BARS_DEBOUNCE_MS);
    };
    // A zoom made just before the chart goes away (a timeframe change, navigation) or the page
    // is left is reported at once rather than lost with the debounce timer.
    const flush = (): void => {
      if (timer === null) return;
      clearTimeout(timer);
      report();
    };
    timeScale.subscribeVisibleLogicalRangeChange(handler);
    window.addEventListener("pagehide", flush);
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(handler);
      window.removeEventListener("pagehide", flush);
      flush();
    };
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
    // Story 32.7: the Auto Anchored marker lived on the old host too; re-attach it on the new one.
    anchorMarkerRef.current = null;
    // Story 32.8: the footprint lives on the candle series only, re-attached on a return to Candles.
    footprintRef.current = null;
    // Story 33.5: so do the liquidation markers; detached here, while their series still exists.
    markersPluginRef.current?.detach();
    markersPluginRef.current = null;
    // Story 32.1: the price gap painter is detached from the old host while that series still
    // exists; the [gapRuns, mode] effect below attaches a fresh one to the new host.
    const priceGap = priceGapRef.current;
    if (priceGap) {
      priceGap.host.detachPrimitive(priceGap.primitive);
      priceGapRef.current = null;
    }

    if (mainKind !== null) {
      // Candles mode (`mainKind` is set exactly then).
      if (lineSeriesRef.current) {
        for (const id of LINE_SERIES_IDS) chart.removeSeries(lineSeriesRef.current[id]);
        lineSeriesRef.current = null;
        prevLinesLengthRef.current = 0;
      }
      // Story 33.9: a chart type drawn by another definition replaces the main series. Its price lines
      // die with it (re-created by the [priceLines] effect) and the view it showed is restored after
      // the new series' first setData, so a type switch never moves the chart.
      if (seriesRef.current && seriesKindRef.current !== mainKind) {
        // A range still pending (a switch while the data was empty) is the view to restore, not the
        // empty series' own.
        keptRangeRef.current ??= chart.timeScale().getVisibleLogicalRange();
        chart.removeSeries(seriesRef.current);
        seriesRef.current = null;
        priceLineRegistryRef.current.clear();
      }
      if (!seriesRef.current) {
        // Up/down colours explicitly from the chart tokens (Story 32.4) -- lightweight-charts' own
        // defaults are never relied on.
        seriesRef.current = addMainSeries(chart, mainKind);
        // A series added after the price-pane overlays (a type switch, a return from Lines) would be
        // the pane's last: drawn over the compare and indicator lines, and no longer the series the
        // right scale takes its formatter from (the lowest index). Back to the first, as on mount.
        seriesRef.current.setSeriesOrder(0);
        seriesKindRef.current = mainKind;
        prevFirstTimeRef.current = null;
      }
    } else {
      keptRangeRef.current = null; // a type switch's pending view does not outlive Candles mode
      if (seriesRef.current) {
        chart.removeSeries(seriesRef.current);
        seriesRef.current = null;
        seriesKindRef.current = null;
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
  }, [mode, mainKind]);

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
    const prevNewest = lastPaintedTimeRef.current;

    // AD-F6: the main series alone gets the chart type's rows; `data` stays the input of the rest.
    (series.setData as (rows: MainRow[]) => void)(mainRows);
    prevFirstTimeRef.current = data.length > 0 ? data[0].time : null;
    lastPaintedTimeRef.current = data.length > 0 ? (data[data.length - 1].time as unknown as number) : null;
    // The view kept across a type switch is restored only once the new series has bars: on an empty
    // series the library has no logical range to hold it, so it stays pending until data arrives.
    const kept = keptRangeRef.current;
    if (kept && chart && mainRows.length > 0) {
      keptRangeRef.current = null;
      chart.timeScale().setVisibleLogicalRange(kept);
    }

    if (rangeBeforeUpdate && chart) {
      chart.timeScale().setVisibleLogicalRange({
        from: rangeBeforeUpdate.from + addedAtFront,
        to: rangeBeforeUpdate.to + addedAtFront,
      });
    }

    // Story 32.6: the saved zoom, once, with the first candles: that many bars, the latest at the
    // right edge. From here on, zoom changes are reported (see the subscription above).
    if (!visibleBarsReadyRef.current && data.length > 0 && chart) {
      visibleBarsReadyRef.current = true;
      const bars = initialVisibleBarsRef.current;
      if (bars !== undefined && bars > 0) {
        const last = data.length - 1;
        chart.timeScale().setVisibleLogicalRange({ from: last - bars + 0.5, to: last + 0.5 });
        lastVisibleBarsRef.current = bars;
      }
    }

    if (followNewestRef.current && chart) followNewestBar(chart, data, prevNewest);
    // `mainRows` changes with `data` and `chartType`; `mainKind` re-runs it on a fresh series.
  }, [data, mainRows, mode, mainKind]);

  // Story 33.9: the Baseline's base value is the close of the first visible bar, re-applied whenever
  // the visible range moves (TradingView's baseline follows the left edge the same way).
  const dataRef = useRef(data);
  dataRef.current = data;
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series || mode !== "candles" || chartType !== "baseline") return;
    const timeScale = chart.timeScale();
    let applied: number | null = null;
    // The logical range only signals a move; the left edge is read as a time (`firstVisibleClose`).
    const apply = (): void => {
      const range = timeScale.getVisibleRange();
      if (!range) return;
      const price = firstVisibleClose(dataRef.current, range.from as number);
      if (price === null || price === applied) return;
      applied = price;
      (series as ISeriesApi<"Baseline", Time>).applyOptions({ baseValue: { type: "price", price } });
    };
    apply();
    timeScale.subscribeVisibleLogicalRangeChange(apply);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(apply);
  }, [chartType, mode, mainKind, data]);

  useEffect(() => {
    // Story 33.9: the price pane's right scale. Auto off keeps the range the operator dragged to
    // through scrolls; the library's own double-click on the scale turns it back on (reported below).
    const chart = chartRef.current;
    if (!chart) return;
    chart.priceScale("right").applyOptions({
      mode: SCALE_MODE_OF[priceScale.mode],
      autoScale: priceScale.autoScale,
      invertScale: priceScale.invert,
    });
  }, [priceScale.mode, priceScale.autoScale, priceScale.invert]);

  useEffect(() => {
    // Story 33.9: the scale strip's own gestures. A double-click on it is the library's auto-scale
    // reset, persisted as `auto_scale: true`; a drag (of the scale or the plot) that the library ended
    // with auto-scale off is persisted as `auto_scale: false`, so the header's Auto toggle never lies.
    // A right-click on it opens the scale menu.
    const container = containerRef.current;
    const chart = chartRef.current;
    if (!container || !chart) return;
    const report = (patch: PriceScalePatch): void => priceScaleCallbackRef.current?.(patch);
    const handleDblClick = (event: MouseEvent): void => {
      if (onRightScale(container, chart, event.clientX, event.clientY)) report({ auto_scale: true });
    };
    let pressed = false;
    const handleDown = (): void => {
      pressed = true;
    };
    const handleUp = (): void => {
      if (!pressed) return;
      pressed = false;
      if (autoScaleRef.current && chart.priceScale("right").options().autoScale === false) report({ auto_scale: false });
    };
    const handleContextMenu = (event: MouseEvent): void => {
      // Without an `onPriceScale` no menu choice could act: the browser's own menu stays.
      if (!priceScaleCallbackRef.current || !onRightScale(container, chart, event.clientX, event.clientY)) return;
      event.preventDefault();
      setScaleMenu({ x: event.clientX, y: event.clientY });
    };
    container.addEventListener("dblclick", handleDblClick);
    container.addEventListener("pointerdown", handleDown);
    // A drag the browser cancels (a touch turned scroll, a lost capture) still ended the library's drag.
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
    container.addEventListener("contextmenu", handleContextMenu);
    return () => {
      container.removeEventListener("dblclick", handleDblClick);
      container.removeEventListener("pointerdown", handleDown);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
      container.removeEventListener("contextmenu", handleContextMenu);
    };
  }, []);

  useEffect(() => {
    // The scale menu closes on Esc or a press anywhere outside it.
    if (!scaleMenu) return;
    const close = (): void => setScaleMenu(null);
    const handleKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") close();
    };
    const handleDown = (event: MouseEvent): void => {
      if (!(event.target instanceof Element && event.target.closest("[data-scale-menu]"))) close();
    };
    window.addEventListener("keydown", handleKey);
    window.addEventListener("mousedown", handleDown);
    return () => {
      window.removeEventListener("keydown", handleKey);
      window.removeEventListener("mousedown", handleDown);
    };
  }, [scaleMenu]);

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
        entry = { pane, group, spec, series, lastData: spec.data, gap: null, lastUpDown: upDownKey(spec), zero: null };
        registry.set(spec.id, entry);
        setSeriesData(entry.series, paintedData(spec));
        syncZeroLine(entry);
        continue;
      }
      entry.spec = spec;
      syncZeroLine(entry);
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
      laidOutRef.current = layoutPaneHeights(
        chart,
        registry,
        heightsBefore,
        laidOutRef.current,
        collapsedHeightsRef.current,
        knownHeightsRef.current,
      );
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
    // Story 32.8: the Footprint row reads its mode and row size; it has a gear and an x, no eye.
    const footprintRows: LegendSeries[] =
      footprintLegend === null
        ? []
        : [
            {
              group: "footprint",
              groupLabel: "Footprint",
              outputLabel: "Footprint",
              color: chartVar("--chart-text-dim"),
              series: null,
              data: [],
              pane: null,
              hideable: false,
              text: footprintLegend,
            },
          ];
    // Story 33.9: while Heikin Ashi draws the main series a read-only row says so -- the bars are
    // derived, not prices (AD-F6); no button, nothing to hide.
    const heikinRows: LegendSeries[] =
      heikinLegend === null
        ? []
        : [
            {
              group: "heikin-ashi",
              groupLabel: heikinLegend,
              outputLabel: heikinLegend,
              color: chartVar("--chart-text-dim"),
              series: null,
              data: [],
              pane: null,
              actionable: false,
              text: "display only",
            },
          ];
    const extraRows = legendExtras.map(
      (extra): LegendSeries => ({
        group: extra.id,
        groupLabel: extra.label,
        outputLabel: extra.label,
        color: extra.color,
        series: null,
        data: extra.value === null ? [] : [{ time: 0 as Time, value: extra.value }],
        pane: null,
        format: extra.format,
        text: extra.text,
        actionable: false,
      }),
    );
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
        format: spec.format,
        text: spec.text,
      };
    }).concat(heikinRows, footprintRows, extraRows);
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
  }, [panes, legendExtras, footprintLegend, heikinLegend, handleLegendAction]);

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
  }, [gapRuns, mode, mainKind]);

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
    const { time: barTime, open, high, low, close, volume } = liveBar;
    // Story 33.9: the chart type's row of the forming bar (Heikin Ashi chained on the last closed HA
    // bar, so earlier bars never change); the volume pane below keeps the real bar.
    const row = liveSeriesRow(chartType, data, mainRows, { time: barTime, open, high, low, close }, upDown);
    (series.update as (row: MainRow) => void)(row);
    // Volume pane follows the forming bar; its series only exists once the panes effect has
    // added it, and is absent in Lines mode's registry-less state -- both are no-ops. Story 33.6:
    // painted like its closed bars (`liveVolumeColor`), so a forming bar is never the flat colour.
    panesRef.current
      .get("volume")
      ?.series.update({ time: barTime, value: volume, ...(liveVolumeColor ? { color: liveVolumeColor } : {}) });
    // `mode`: Lines -> Candles recreates seriesRef with no data. `data`: every setData()
    // replaces the series with history that lacks the forming bar. Both must repaint the
    // held `liveBar` at once, not wait for the next websocket tick.
  }, [liveBar, liveVolumeColor, mode, panes, data, chartType, mainKind, mainRows, upDown]);

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
  }, [priceLines, mode, mainKind]);

  useEffect(() => {
    // DW-144: built once per history change, read by every measurement mouse-move.
    measureIndexRef.current = buildMeasurementIndex(data, volume);
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
  }, [volumeProfiles, mode, mainKind]);

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
    markerRef.current = new VerticalMarkerPrimitive(markerTime);
    host.attachPrimitive(markerRef.current);
  }, [markerTime, mode, mainKind]);

  useEffect(() => {
    // Story 32.7: the Auto Anchored profile's anchor marker, on the same discipline as the replay
    // marker above (add / move / remove), in the drawing colour so the two never read alike.
    const host = seriesRef.current;
    if (!host || mode !== "candles") return;
    if (anchorMarkerTime === null) {
      if (anchorMarkerRef.current) host.detachPrimitive(anchorMarkerRef.current);
      anchorMarkerRef.current = null;
      return;
    }
    if (anchorMarkerRef.current) {
      anchorMarkerRef.current.setTime(anchorMarkerTime);
      return;
    }
    anchorMarkerRef.current = new VerticalMarkerPrimitive(anchorMarkerTime, "--chart-drawing");
    host.attachPrimitive(anchorMarkerRef.current);
  }, [anchorMarkerTime, mode, mainKind]);

  useEffect(() => {
    // Story 32.8: add / update / remove the footprint, on the same discipline as the markers above.
    const host = seriesRef.current;
    if (!host || mode !== "candles") return;
    if (!footprint) {
      if (footprintRef.current) host.detachPrimitive(footprintRef.current);
      footprintRef.current = null;
      return;
    }
    if (footprintRef.current) {
      footprintRef.current.update(footprint);
      return;
    }
    footprintRef.current = new FootprintPrimitive(footprint);
    host.attachPrimitive(footprintRef.current);
  }, [footprint, mode, mainKind]);

  useEffect(() => {
    // Story 33.5: the liquidation markers, one `createSeriesMarkers` plugin on the candle series, its
    // markers replaced on every change (created on the first marker, re-created after a mode flip).
    const host = seriesRef.current;
    if (!host || mode !== "candles") return;
    const markers = liquidationMarkers.map(({ tooltip: _tooltip, ...marker }) => marker);
    // A tooltip open on a marker that this change removed (or merged) would outlive it under a still
    // pointer; the next crosshair move re-opens it on whatever marker is there now.
    setMarkerTip((prev) => (prev === null ? prev : null));
    if (markersPluginRef.current) markersPluginRef.current.setMarkers(markers);
    else if (markers.length > 0) markersPluginRef.current = createSeriesMarkers(host, markers);
  }, [liquidationMarkers, mode, mainKind]);

  useEffect(() => {
    latestRef.current = { volumeProfiles, onRangeSelect, onProfileEdgeDrag, onProfileEdgeCommit, onProfileEdgeCancel };
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
    const stopDrag = attachRangeDrag(container, chart, host, gridRef.current, {
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
  }, [rangeSelectActive, mode, mainKind]);

  useEffect(() => {
    // Story 18.6 (AC #3): edge grab-and-drag for placed profiles. Off while a range tool
    // (FRVP, measure, Fibonacci) is armed: their capture-phase drags own the mouse then, and a
    // sibling capture listener's stopPropagation could not keep both from starting.
    const container = containerRef.current;
    const chart = chartRef.current;
    const host = seriesRef.current;
    if (!container || !chart || !host || !profileEdgesEditable || rangeSelectActive || measureActive || fibActive) return;
    if (mode !== "candles") return;

    const EDGE_TOLERANCE_PX = 6;
    let grabbed: { id: string; edge: "start" | "end" } | null = null;
    let lastTime: Time | null = null;

    // DW-150: past the newest bar the edge follows the pointer to the last grid slot (the
    // forming bar included) instead of freezing at the last bar `coordinateToTime` resolved.
    const timeAt = (event: MouseEvent): Time | null =>
      timeAtX(chart, gridRef.current, localPoint(container, chart, event.clientX, event.clientY).x);

    const findEdge = (event: MouseEvent): { id: string; edge: "start" | "end" } | null => {
      // An edge is only grabbable inside the price pane's plot, never on an axis strip (DW-144's guard).
      const point = plotPoint(container, chart, event.clientX, event.clientY);
      if (!point) return null;
      const { x, y } = point;
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
    // DW-150: the resize cursor tells an edge is grabbable before the press. While grabbed the
    // cursor stays as it was at the press, wherever the drag goes.
    const setCursor = (cursor: string): void => {
      if (container.style.cursor !== cursor) container.style.cursor = cursor;
    };
    const handleHover = (event: MouseEvent): void => {
      // A held button is a library pan (or another tool's drag): no edge can be grabbed mid-press.
      if (!grabbed) setCursor(event.buttons === 0 && findEdge(event) ? "ew-resize" : "");
    };
    const handleLeave = (): void => {
      if (!grabbed) setCursor("");
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
      // A release outside the chart gets no hover to clear the cursor; inside, the next move restores it.
      setCursor("");
      if (time !== null) latestRef.current.onProfileEdgeCommit?.(done.id, done.edge, time);
    };

    container.addEventListener("mousedown", handleMouseDown, true);
    container.addEventListener("mousemove", handleHover);
    container.addEventListener("mouseleave", handleLeave);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      container.removeEventListener("mousedown", handleMouseDown, true);
      container.removeEventListener("mousemove", handleHover);
      container.removeEventListener("mouseleave", handleLeave);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
      setCursor("");
      // DW-150: torn down mid-drag (a tool armed, the mode flipped): the release this drag waited
      // for will never arrive here, so the caller's ghost is cleared -- never committed.
      if (grabbed) latestRef.current.onProfileEdgeCancel?.();
    };
  }, [profileEdgesEditable, rangeSelectActive, measureActive, fibActive, mode, mainKind]);

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

    const stopDrag = attachRangeDrag(container, chart, host, gridRef.current, {
      onMove: (start, end) => {
        if (!attached) {
          host.attachPrimitive(primitive);
          attached = true;
        }
        primitive.setSelection(start, end, formatMeasurement(computeMeasurement(start, end, measureIndexRef.current, liveBarRef.current)));
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
  }, [measureActive, onMeasureEnd, mode, mainKind]);

  useEffect(() => {
    // Story 32.5: the Fibonacci tool, on the same range-drag plumbing as the measurement: a live
    // preview (the default levels between the press and the pointer) attached lazily on the first
    // move, and on release exactly one `onFibPlace(a, b)`. A click without a drag shows and reports
    // nothing and leaves the tool armed; Esc / disarm runs the cleanup, which removes the preview.
    const container = containerRef.current;
    const chart = chartRef.current;
    const host = seriesRef.current ?? lineSeriesRef.current?.price;
    if (!container || !chart || !host || !fibActive) return;

    let preview: FibPrimitive | null = null;
    const stopDrag = attachRangeDrag(container, chart, host, gridRef.current, {
      onMove: (start, end) => {
        const shape: FibDrawing = {
          kind: "fib",
          id: "fib-preview",
          anchors: [
            { time: start.time as number, price: start.price },
            { time: end.time as number, price: end.price },
          ],
          levels: defaultFibLevels(fibLevelColor), // as placed (ChartPage's handleFibPlace): the preview is the drawing
          extend_right: true,
          label_side: "left",
          line_width: 1,
        };
        if (!preview) {
          preview = new FibPrimitive(shape, precisionRef.current?.price ?? null, gridRef.current);
          host.attachPrimitive(preview);
        } else {
          preview.update(shape, precisionRef.current?.price ?? null);
        }
      },
      onRelease: (last) => {
        if (!last || !preview) return;
        host.detachPrimitive(preview);
        preview = null;
        onFibPlace?.(last.start, last.end);
      },
    });
    return () => {
      stopDrag();
      if (preview) host.detachPrimitive(preview);
    };
  }, [fibActive, onFibPlace, mode, mainKind]);

  useEffect(() => {
    // Story 18.2 (AC #4): the drawings prop's registry-diff effect -- same per-id
    // add/update/remove discipline as priceLines, never a visible-range call. Story 32.5: one
    // registry for every kind (trendline, Fibonacci, position), each its own primitive on the
    // main-pane host; a changed kind under one id replaces the primitive.
    const host = seriesRef.current ?? lineSeriesRef.current?.price;
    if (!host) return;
    const registry = drawingRegistryRef.current;
    const specsById = new Map(drawings.map((spec) => [spec.id, spec] as const));
    const grid = gridRef.current;

    for (const [id, entry] of [...registry]) {
      if (specsById.get(id)?.kind !== entry.kind) {
        host.detachPrimitive(entry.primitive);
        registry.delete(id);
      }
    }

    for (const spec of drawings) {
      const entry = registry.get(spec.id);
      if (entry) {
        updateDrawingPrimitive(entry.primitive, spec, precision);
        continue;
      }
      const created = createDrawingPrimitive(spec, precision, grid);
      created.setHandlesVisible(editRef.current.drawEditable);
      host.attachPrimitive(created);
      registry.set(spec.id, { kind: spec.kind, primitive: created });
    }
  }, [drawings, mode, mainKind, precision]);

  useEffect(() => {
    // Story 32.5: handles are drawn only while drawings are editable (the Cursor tool), and every
    // primitive repaints when the bars it snaps to change (a page prepended, a timeframe's data).
    for (const { primitive } of drawingRegistryRef.current.values()) {
      primitive.setHandlesVisible(drawEditable);
      primitive.refresh();
    }
  }, [drawEditable, barTimes, drawings, mode, mainKind]);

  useEffect(() => {
    // Story 18.1 (AC #3): the one crosshairMove subscription serves both halves of the
    // drag -- remembering the latest hover (what the mousedown grab below hit-tests
    // against) and, while a drag is active, converting its own `param.point.y` to a
    // price. Staying inside the library's `param.point` coordinate space (rather than
    // native mousemove + getBoundingClientRect) keeps the grab hit-test and the drag
    // conversion in the same space, pane offsets included. `paneIndex === 0` guards
    // against y-converting from an indicator sub-pane.
    const chart = chartRef.current;
    // Story 32.5: a handle drag of a primitive drawing works in both modes; the price-line drag
    // (a native line on the candlestick series) stays Candles-only.
    if (!chart || (!onDrawingDrag && !(onPriceLineDrag && mode === "candles"))) return;
    // A drag can never carry over from a previous subscription lifetime (e.g. a
    // candles->lines->candles flip while the button was somehow still held) -- a fresh
    // subscription starts dragless.
    dragIdRef.current = null;

    const handleCrosshairMove = (param: MouseEventParams): void => {
      lastCrosshairRef.current = param.point ? param : null;
      const dragged = dragIdRef.current;
      if (dragged === null) return;
      if (!param.point || param.paneIndex !== 0) return;
      const host = seriesRef.current ?? lineSeriesRef.current?.price;
      const price = host?.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) return;
      dragMovedRef.current = true;
      if (dragged.handle === "price") {
        onPriceLineDrag?.(dragged.id, price);
        return;
      }
      const grid = gridRef.current;
      const logical = param.logical ?? chart.timeScale().coordinateToLogical(param.point.x);
      onDrawingDrag?.(dragged.id, dragged.handle ?? "", {
        price,
        time: logical === null ? null : grid.timeAtLogical(logical),
        barsSince: (time) => {
          const from = grid.indexOf(time);
          return from === null || logical === null ? null : Math.round(logical) - from;
        },
      });
    };

    chart.subscribeCrosshairMove(handleCrosshairMove);
    return () => {
      chart.unsubscribeCrosshairMove(handleCrosshairMove);
      // The subscription that would have refreshed it is gone -- a stale param from
      // this lifetime must never be able to start a drag in the next one.
      lastCrosshairRef.current = null;
    };
  }, [onPriceLineDrag, onDrawingDrag, mode]);

  // Story 33.5: a hovered liquidation marker (the library reports its id as `hoveredObjectId`) shows
  // its tooltip lines beside the pointer; read on the legend's own crosshair subscription.
  const showMarkerTip = useCallback((param: MouseEventParams): void => {
    const id = param.hoveredObjectId;
    const spec = typeof id === "string" ? markerSpecsRef.current.find((m) => m.id === id) : undefined;
    const container = containerRef.current;
    if (!spec || !param.point || !container) {
      setMarkerTip((prev) => (prev === null ? prev : null));
      return;
    }
    const rect = container.getBoundingClientRect();
    setMarkerTip({ lines: spec.tooltip, x: rect.left + param.point.x, y: rect.top + param.point.y });
  }, []);

  useEffect(() => {
    // Legend values follow the crosshair; off-chart (time undefined) they fall back to the
    // latest value. Its own subscription, declared after the drag one above.
    const chart = chartRef.current;
    if (!chart) return;
    const handle = (param: MouseEventParams): void => {
      renderLegends(chart, legendItemsRef.current, param, gapLookupRef.current, handleLegendAction);
      showMarkerTip(param);
    };
    chart.subscribeCrosshairMove(handle);
    return () => chart.unsubscribeCrosshairMove(handle);
  }, [mode, handleLegendAction, showMarkerTip]);

  useEffect(() => {
    // Story 18.1 (AC #3): the drag's start/end. Capture phase so a line grab runs
    // BEFORE lightweight-charts' own internal mousedown handlers (attached to inner
    // elements) -- stopPropagation() then prevents any chart pan from starting. The
    // window-level mouseup (not container-level: the drag can end with the cursor
    // outside the chart) is what always ends it.
    const container = containerRef.current;
    if (!container || (!onDrawingDrag && !(onPriceLineDrag && mode === "candles"))) return;

    const handleMouseDown = (event: MouseEvent): void => {
      // Only the primary button grabs: a right press (the drawing's context menu) or a middle one
      // must never start a drag that moves and saves the drawing.
      if (event.button !== 0) {
        dragIdRef.current = null;
        return;
      }
      const series = seriesRef.current;
      const param = lastCrosshairRef.current;
      // Story 32.5: the one grab for every drawing. A price line is grabbable with any tool (the
      // library reports the hover); a primitive's handle only in Cursor mode, so a placement
      // tool's own drag is never stolen. Only the price pane carries drawings.
      const grabbed =
        param?.point && (param.paneIndex === undefined || param.paneIndex === 0)
          ? findDrawingHit(param.point, series, drawingRegistryRef.current, priceLines, {
              // Not while the Fibonacci tool is armed: its drag (a sibling capture listener, which
              // stopPropagation can't stop) would also start, moving the line and placing a fib.
              hlineReachable: mode === "candles" && !fibActive && !!onPriceLineDrag && overPriceLine(param, series),
              primitives: !!onDrawingDrag && editRef.current.drawEditable,
              bodies: false,
            })
          : null;
      dragIdRef.current = grabbed;
      const grabbedId = grabbed?.id ?? null;
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
  }, [priceLines, onPriceLineDrag, onDrawingDrag, mode, fibActive]);

  useEffect(() => {
    // Story 33.8: in Cursor mode a right-click on a drawing opens the same edit menu a click does
    // (the browser's own menu is suppressed only then); anywhere else it stays the browser's.
    const container = containerRef.current;
    const chart = chartRef.current;
    if (!container || !chart || !drawEditable) return;
    const handleContextMenu = (event: MouseEvent): void => {
      const point = plotPoint(container, chart, event.clientX, event.clientY); // price pane only
      if (!point) return;
      const hitId = findDrawingHit(point, seriesRef.current, drawingRegistryRef.current, editRef.current.priceLines, {
        hlineReachable: true,
        primitives: true,
        bodies: true,
      })?.id;
      if (!hitId) return;
      event.preventDefault();
      setMenu({ id: hitId, x: event.clientX, y: event.clientY });
    };
    container.addEventListener("contextmenu", handleContextMenu);
    return () => container.removeEventListener("contextmenu", handleContextMenu);
  }, [drawEditable]);

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
        const hitId = findDrawingHit(param.point, seriesRef.current, drawingRegistryRef.current, editRef.current.priceLines, {
          hlineReachable: true,
          primitives: true,
          bodies: true,
        })?.id;
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
  }, [pendingAnchor, mode, mainKind]);

  useEffect(() => {
    // Story 33.5: the bar spacing, on mount and after every zoom (the visible range changes with it).
    const chart = chartRef.current;
    if (!chart) return;
    const timeScale = chart.timeScale();
    let last: number | null = null;
    const report = (): void => {
      const spacing = timeScale.options().barSpacing;
      if (spacing === last) return;
      last = spacing;
      barSpacingCallbackRef.current?.(spacing);
    };
    report();
    timeScale.subscribeVisibleLogicalRangeChange(report);
    return () => timeScale.unsubscribeVisibleLogicalRangeChange(report);
  }, []);

  const menuSpec = menu ? (priceLines.find((l) => l.id === menu.id) ?? drawings.find((d) => d.id === menu.id)) : undefined;
  const menuHasSettings =
    !!menuSpec &&
    "kind" in menuSpec &&
    (menuSpec.kind === "fib" || menuSpec.kind === "position" || menuSpec.kind === "anchored_vp" || menuSpec.kind === "anchored_vwap");
  // An Anchored VP has no single colour: the menu's one colour is its up colour.
  const specColor = menuSpec && "kind" in menuSpec && menuSpec.kind === "anchored_vp" ? menuSpec.up_color : menuSpec?.color;
  const menuColor = /^#[0-9a-f]{6}$/i.test(specColor ?? "") ? specColor! : chartVar("--chart-drawing");
  // Story 33.8: the alert a horizontal line (a price line, no `kind`) or a trendline prefills.
  const menuAlert: AlertCondition | null = !menu || !menuSpec
    ? null
    : !("kind" in menuSpec)
      ? { kind: "price_cross", level: menuSpec.price }
      : menuSpec.kind === "trendline"
        ? { kind: "trendline_cross", drawing_id: menu.id }
        : null;
  const pickScale = (patch: PriceScalePatch): void => {
    setScaleMenu(null);
    onPriceScale?.(patch);
  };
  return (
    <>
      <div ref={containerRef} />
      {scaleMenu && (
        <div
          role="menu"
          aria-label="Price scale"
          data-scale-menu=""
          ref={scaleMenuRef}
          style={{
            position: "fixed",
            right: Math.max(0, window.innerWidth - scaleMenu.x),
            top: Math.max(0, Math.min(scaleMenu.y, window.innerHeight - scaleMenuHeight)),
            zIndex: 1000,
            display: "flex", flexDirection: "column", gap: 4, padding: 6, background: chartVar("--chart-bg"),
            color: chartVar("--chart-text"), border: `1px solid ${chartVar("--chart-border")}`,
          }}
        >
          {PRICE_SCALE_MODES.map((m) => {
            const locked = scaleModesLocked !== null && (m === "normal" || m === "log");
            return (
              <button
                key={m}
                type="button"
                role="menuitemradio"
                aria-checked={priceScale.mode === m}
                className="tabbtn"
                disabled={locked}
                title={locked ? scaleModesLocked : undefined}
                onClick={() => pickScale({ mode: m })}
              >
                {PRICE_SCALE_LABELS[m]}
              </button>
            );
          })}
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={priceScale.autoScale}
            className="tabbtn"
            onClick={() => pickScale({ auto_scale: !priceScale.autoScale })}
          >
            Auto (fits data to screen)
          </button>
          <button
            type="button"
            role="menuitemcheckbox"
            aria-checked={priceScale.invert}
            className="tabbtn"
            onClick={() => pickScale({ invert: !priceScale.invert })}
          >
            Invert scale
          </button>
        </div>
      )}
      {markerTip && (
        <div
          role="tooltip"
          className="chart-marker-tip"
          style={{
            position: "fixed", left: markerTip.x + 12, top: markerTip.y + 12, zIndex: 1000, padding: "4px 6px",
            background: chartVar("--chart-bg"), color: chartVar("--chart-text"),
            border: `1px solid ${chartVar("--chart-border")}`, pointerEvents: "none",
          }}
        >
          {markerTip.lines.map((line) => (
            <div key={line}>{line}</div>
          ))}
        </div>
      )}
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
          {menuHasSettings && onDrawingSettings && (
            <button
              type="button"
              className="tabbtn"
              onClick={() => {
                onDrawingSettings(menu.id);
                setMenu(null);
              }}
            >
              Settings…
            </button>
          )}
          {menuAlert && onDrawingAlert && (
            <button
              type="button"
              className="tabbtn"
              onClick={() => {
                onDrawingAlert(menuAlert);
                setMenu(null);
              }}
            >
              Add alert…
            </button>
          )}
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
