import { CrosshairMode, type IChartApi, type Time } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { fetchCoinIndicatorConfig, fetchIndicatorCatalog, saveCoinIndicatorConfig } from "../api/client";
import AlertDialog from "../components/chart/AlertDialog";
import IndicatorPicker, { type IndicatorPickerHandle } from "../components/chart/IndicatorPicker";
import IndicatorSettingsDialog, { type SettingsOutput, type SettingsPatch } from "../components/chart/IndicatorSettingsDialog";
import LiquidationTape from "../components/chart/LiquidationTape";
import CompareControl from "../components/chart/CompareControl";
import ShortcutSheet from "../components/chart/ShortcutSheet";
import SymbolSearch from "../components/chart/SymbolSearch";
import WatchlistRail from "../components/chart/WatchlistRail";
import CompareFeed, { type CompareFeedState } from "../components/chart/CompareFeed";
import { comparePaneSpecs, mainSlots, spreadPaneSpec } from "../components/chart/comparePanes";
import { DERIVATIVE_OUTPUT_TOKENS, DERIVATIVE_PANE_IDS, MARK_INDEX_GROUP } from "../components/chart/derivativePanes";
import type { LegendAction } from "../components/chart/legend";
import LightweightChart, {
  type ChartMode,
  type ChartPoint,
  type DrawingSpec,
  type Placement,
  type LegendExtra,
  type VolumeProfileSpec,
  type IndicatorPaneSpec,
  type IndicatorPlot,
  type PriceLineSpec,
  type PriceScalePatch,
} from "../components/chart/LightweightChart";
import {
  type ShortcutAction,
  TIMEFRAME_BUFFER_IDLE_MS,
  TIMEFRAME_BUFFER_MAX,
  isTypingContext,
  shortcutFor,
  timeframeFromBuffer,
} from "../lib/shortcuts";
import { TIME_ZONES, TIME_ZONE_LABELS, type TimeZoneSetting } from "../lib/time";
import { type Fullscreen, useFullscreen } from "../hooks/useFullscreen";
import type { TrendlineAnchor } from "../components/chart/primitives/TrendlinePrimitive";
import { type MagnetMode, nextDragGesture, nextMagnetMode, replaceDrawing } from "../lib/drawingKit";
import ToolRail from "../components/chart/ToolRail";
import type { AlertCondition } from "../lib/alertConditions";
import { type ChartTool, type ChartToolDef, groupOfTool, toolDef } from "../lib/chartTools";
import VolumeOverlaysDialog, { type SessionSlot, VolumeOverlayNotices } from "../components/chart/VolumeOverlaysDialog";
import {
  DEFAULT_VOLUME_PROFILE_SETTINGS,
  buildRangeProfile,
  type VolumeProfile,
  type VolumeProfileSettings,
} from "../lib/volumeProfile";
import { DEFAULT_AUTO_ANCHOR, anchorBars, anchorTime } from "../lib/autoAnchor";
import { STORED_VWAP_SOURCE, type VwapPoint, anchoredVwap, breakAtGaps, storedVwapPoints } from "../lib/anchoredVwap";
import { formatIndicatorValue, isIndicatorUnit } from "../lib/indicatorFormat";
import { volumeBarColor } from "../lib/volumeColor";
import {
  DEFAULT_IB_MINUTES,
  type InitialBalance,
  TPO_BAR_SECONDS,
  TPO_MAX_BLOCKS_PER_ROW,
  type TpoRow,
  initialBalance,
  tpoRows,
} from "../lib/tpo";
import {
  DEFAULT_SESSION_COUNT,
  SESSION_PRESETS,
  buildSessionProfiles,
  drawableSpan,
  periodEnd,
  periodStartBack,
  sessionBarSeconds,
  timedBars,
  withFormingBar,
  type SessionPeriod,
  type SessionPreset,
  type SessionProfileCache,
  type SessionProfileEntry,
  type SessionProfileSettings,
} from "../lib/sessionProfile";
import { chartVar, fibLevelColor, newDrawingContext } from "../components/chart/chartTheme";
import DrawingSettingsDialog from "../components/chart/DrawingSettingsDialog";
import FootprintSettingsDialog from "../components/chart/FootprintSettingsDialog";
import VolumeSettingsDialog from "../components/chart/VolumeSettingsDialog";
import type { FootprintRenderSpec } from "../components/chart/primitives/FootprintPrimitive";
import {
  type Anchor,
  type Drawing,
  type DragPoint,
  type InstrumentPrecision,
  applyHandleDrag,
  buildDrawing,
  channelPlaceable,
  defaultFibLevels,
  isLineDrawing,
  kindOfTool,
  nextDrawingId,
  placementOf,
  safeDecimal,
  snapIndex,
  storedTime,
} from "../lib/drawings";
import { roundToPrecision } from "../lib/units";
import { type ChartDrawings, useChartDrawings } from "../hooks/useChartDrawings";
import { DEFAULT_SOURCE, entryId, splitSeriesKey } from "../lib/indicatorId";
import { outputStyle } from "../lib/indicatorStyle";
import { buildPatternMarkers, drawsPatternMarkers, PATTERN_INDICATOR, patternReadout } from "../lib/patternMarkers";
import { assignPaneColor } from "../components/chart/paneColors";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../api/schema";
import { useCandles } from "../hooks/useCandles";
import { TIMEFRAMES } from "../timeframes";
import {
  type ChartLayout,
  DERIVATIVE_KEYS,
  DERIVATIVE_LABELS,
  DERIVATIVE_OUTPUTS,
  type DerivativeKey,
  type DerivativesLayout,
  type FootprintSettings,
  type LiquidationMeasure,
  type VolumeColorMode,
  type VolumeProfileLayout,
  type CompareLayout,
  type LastPriceLayout,
  type PriceScaleLayout,
} from "../lib/chartLayout";
import { CHART_TYPES, CHART_TYPE_LABELS, type ChartType, PRICE_SCALE_LABELS, PRICE_SCALE_MODES, type PriceScaleModeName } from "../lib/chartTypes";
import { COMPARE_GROUP_PREFIX, SPREAD_GROUP, withCompareAdded } from "../lib/compare";
import { useChartDerivatives } from "../hooks/useChartDerivatives";
import { useFootprint } from "../hooks/useFootprint";
import { useChartLayout } from "../hooks/useChartLayout";
import { useReplay } from "../hooks/useReplay";
import { useSessionCandles } from "../hooks/useSessionCandles";
import { useVisibleRange } from "../hooks/useVisibleRange";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
import { useStoredAnchoredVwap } from "../hooks/useStoredAnchoredVwap";
import { useSnapshotSeries } from "../hooks/useSnapshotSeries";

// The default chart is candles + a volume pane only; every other indicator is added
// from the picker (persisted per coin server-side) and placed by its catalog `panel`.
// Volume is a pane (not an overlay: an overlay is `placement: "overlay"`), right under the
// price pane (spec §A1), before indicator panes; the Indicators dialog toggles it (Story 32.2).
const DEFAULT_PANE_IDS = ["volume"];

// Story 32.6: the saved volume profile -> the page's profile state, and back. The layout holds ONE
// profile (`kind`); the page can show a visible-range, a fixed-range and a session profile at once.
// Known limit: only one is saved, the highest of session > fixed (the first placed range, with its
// anchors) > visible; the others are not restored. Upgrade path: a list of profiles in the layout
// table (Story 32.7 adds auto-anchored and TPO settings to this table).
const EMPTY_PROFILE: VolumeProfile = { rows: [], poc: 0, vah: 0, val: 0, totalVolume: 0 };

function profileSettings(vp: VolumeProfileLayout): VolumeProfileSettings {
  return {
    rowCount: vp.rows,
    valueAreaPercent: vp.value_area_pct,
    upColor: vp.up_color,
    downColor: vp.down_color,
    showPoc: vp.show_poc,
    showValueArea: vp.show_value_area,
  };
}

// Story 32.7: the one session slot holds svp / svp-hd / pvp (layout kind "session"), the Auto Anchored
// profile ("auto") or the TPO ("tpo"); the three optional layout keys ride along whichever is on.
const SESSION_KINDS: readonly VolumeProfileLayout["kind"][] = ["session", "auto", "tpo"];

function initialSessionConfig(vp: VolumeProfileLayout): SessionConfig | null {
  if (!SESSION_KINDS.includes(vp.kind)) return null;
  const period = vp.session as SessionPeriod; // checked against SESSION_PERIODS by normalizeLayout
  let preset: SessionPreset = vp.hd ? "svp-hd" : period === "daily" ? "svp" : "pvp";
  if (vp.kind === "auto") preset = "auto";
  if (vp.kind === "tpo") preset = "tpo";
  const settings = { ...profileSettings(vp), sessionCount: vp.sessions };
  return {
    preset,
    period,
    settings,
    anchor: vp.anchor,
    ibMinutes: vp.ib_minutes,
    letters: vp.letters,
  };
}

/** The layout kind a session-slot preset is saved as. */
function sessionKindOf(preset: SessionPreset): VolumeProfileLayout["kind"] {
  return preset === "auto" ? "auto" : preset === "tpo" ? "tpo" : "session";
}

/** A profile's settings as the resource stores them (the row and value-area inputs allow more). */
function storable(
  settings: VolumeProfileSettings,
): Pick<VolumeProfileLayout, "rows" | "value_area_pct" | "up_color" | "down_color" | "show_poc" | "show_value_area"> {
  return {
    rows: Math.min(500, Math.max(2, Math.round(settings.rowCount))),
    value_area_pct: Math.min(100, Math.max(1, settings.valueAreaPercent)),
    up_color: settings.upColor,
    down_color: settings.downColor,
    show_poc: settings.showPoc,
    show_value_area: settings.showValueArea,
  };
}

// `key` is "{indicator_id}.{output_attr}" and indicator_id starts with the catalog name.
// Longest name wins so a name that prefixes another can't claim its keys.
function catalogNameForKey(key: string, catalog: Record<string, IndicatorCatalogEntry>): string | undefined {
  return Object.keys(catalog)
    .filter((n) => key === n || key.startsWith(`${n}_`) || key.startsWith(`${n}.`) || key.startsWith(`${n}:`))
    .sort((x, y) => y.length - x.length)[0];
}

function panelForKey(key: string, catalog: Record<string, IndicatorCatalogEntry>): string {
  const name = catalogNameForKey(key, catalog);
  return name ? catalog[name].panel : "oscillator";
}

// Legend title, TradingView-style: name plus its params and, when it is not the close, its
// source, e.g. "RelativeStrengthIndex (14)" or "SimpleMovingAverage (20, hl2)". Per instance:
// RSI(14) and RSI(21) are two titles. Story 33.11: the catalog's `note` follows on the legend
// ("ZigZag (5) · repaints last leg"); the settings dialog's title omits it.
function legendTitle(entry: IndicatorConfigEntry, note?: string | null): string {
  const parts: unknown[] = Object.values(entry.params ?? {});
  if (entry.source && entry.source !== DEFAULT_SOURCE) parts.push(entry.source);
  const title = parts.length ? `${entry.name} (${parts.join(", ")})` : entry.name;
  return note ? `${title} · ${note}` : title;
}

const INDICATOR_PLOTS: readonly IndicatorPlot[] = ["line", "steps", "points", "swing"];

/** The catalog's plot hint for one output; absent or unknown draws a plain line. */
function plotOf(catalogEntry: IndicatorCatalogEntry | undefined, output: string): IndicatorPlot | undefined {
  const plot = catalogEntry?.plot?.[output];
  return (INDICATOR_PLOTS as readonly string[]).includes(plot ?? "") ? (plot as IndicatorPlot) : undefined;
}

/** The pattern a `CandlePattern` entry detects (its `pattern` param, else the catalog default). */
// A CandlePattern instance's pattern: its entry's param, else -- a stale series no entry owns any
// more -- the `pattern=` its instance id carries (`indicatorId`), and only then the catalog default.
function patternOf(
  entry: IndicatorConfigEntry | undefined,
  catalogEntry: IndicatorCatalogEntry | undefined,
  instanceId?: string,
): string {
  const fromId = instanceId?.match(/(?:^|[_,])pattern=([^,:]+)/)?.[1];
  return String(entry?.params?.pattern ?? fromId ?? catalogEntry?.params?.pattern ?? "");
}

type TpoDetail = { rows: TpoRow[]; balance: InitialBalance | null; ibMinutes: number };
type TpoDetailCache = WeakMap<VolumeProfile, TpoDetail>;

// A TPO session's blocks, letters and initial balance, kept per profile object: `buildSessionProfiles`
// hands back the same profile for a session that did not change, so only the forming one is recounted.
function tpoDetail(cache: TpoDetailCache, entry: SessionProfileEntry, ibMinutes: number): TpoDetail {
  const hit = cache.get(entry.profile);
  if (hit && hit.ibMinutes === ibMinutes) return hit;
  const clock = { sessionStart: entry.periodStart, barSeconds: TPO_BAR_SECONDS };
  const detail: TpoDetail = {
    rows: hit?.rows ?? tpoRows(entry.profile, entry.bars, TPO_MAX_BLOCKS_PER_ROW, clock),
    balance: initialBalance(entry.bars, entry.periodStart, ibMinutes),
    ibMinutes,
  };
  cache.set(entry.profile, detail);
  return detail;
}

// Identity-preserving when no replay is active (`cutoff === null`), so an ordinary
// re-render never hands the chart's pane registry a "changed" data reference.
function trimAfter<T extends { time: Time }>(rows: T[], cutoff: number | null): T[] {
  return cutoff === null ? rows : rows.filter((row) => (row.time as number) <= cutoff);
}

// The wanted start of its history is not part of it: that follows the clock (see `sessionNowMs`).
// The anchor, initial balance and letters are kept whichever preset is on, so switching back
// restores them.
type SessionConfig = SessionSlot;

// Story 18.8: each session's longest bar spans this fraction of the session's width.
const SESSION_WIDTH_FRACTION = 0.7;

// DW-152: the rollover timer re-arms at least this often. `setTimeout` keeps its delay in a signed
// 32-bit ms counter (~24.8 days), so a monthly period's delay would overflow into an immediate fire.
const ROLLOVER_TIMER_CAP_MS = 24 * 3600 * 1000;

// Story 18.7: the longest VRVP bar, growing leftward from the price axis -- but never over this
// share of the pane (DW-151), so a narrow pane keeps its candles visible.
const VRVP_WIDTH_PX = 150;
const VRVP_MAX_WIDTH_FRACTION = 0.3;

// Story 18.6: a placed fixed-range profile. The profile is computed once when the range
// is confirmed (drag-release, edge-drag release, or a settings change) and stored -- never
// recomputed by pan/zoom/new data.
interface FrvpEntry {
  id: string;
  startTime: number;
  endTime: number;
  profile: VolumeProfile;
}

interface EdgeGhost {
  id: string;
  edge: "start" | "end";
  time: number;
}

const NO_BARS = (): number | null => null;

// Story 33.5: the Derivatives group's legend groups -> the layout entry each belongs to.
const DERIVATIVE_OF_GROUP: Record<string, DerivativeKey> = {
  deriv_oi: "oi",
  deriv_funding: "funding",
  deriv_basis: "basis",
  [MARK_INDEX_GROUP]: "mark_index",
  deriv_liquidations: "liquidations",
};

/** A copy of `set` with `item` added, or removed when present. */
function toggled(set: ReadonlySet<string>, item: string): ReadonlySet<string> {
  const next = new Set(set);
  if (!next.delete(item)) next.add(item);
  return next;
}

function derivativeOfGroup(group: string): DerivativeKey | null {
  return Object.hasOwn(DERIVATIVE_OF_GROUP, group) ? DERIVATIVE_OF_GROUP[group] : null;
}

/** Why the Liquidation tape button is disabled (its tooltip), or undefined while it is not. */
function tapeUnavailableReason(spot: boolean, candles: boolean, marketKnown: boolean): string | undefined {
  if (spot) return "spot: no derivatives";
  if (!candles) return "Candles mode only";
  if (!marketKnown) return "waiting for the instrument's market";
  return undefined;
}

function derivativeOnStates(layout: DerivativesLayout): Record<DerivativeKey, boolean> {
  return Object.fromEntries(DERIVATIVE_KEYS.map((key) => [key, layout[key].on])) as Record<DerivativeKey, boolean>;
}

// The Style rows of each entry's settings: a histogram output edits its up/down colours, a line its
// colour, width and style. The seeds are the chart tokens the panes draw with while nothing is stored.
const DERIVATIVE_OUTPUT_KINDS: Record<string, "Line" | "Histogram"> = { rate: "Histogram", liquidations: "Histogram" };

function derivativeOutputs(key: DerivativeKey): SettingsOutput[] {
  return DERIVATIVE_OUTPUTS[key].map((label) => {
    const tokens = DERIVATIVE_OUTPUT_TOKENS[label];
    return {
      label,
      kind: DERIVATIVE_OUTPUT_KINDS[label] ?? "Line",
      defaultColor: chartVar(tokens.color),
      defaultUpColor: chartVar(tokens.up),
      defaultDownColor: chartVar(tokens.down),
    };
  });
}

/** An applied settings patch on one entry: its style, and Liquidations' `measure` and `markers`. */
function applyDerivativePatch(prev: DerivativesLayout, key: DerivativeKey, patch: SettingsPatch): DerivativesLayout {
  const style = Object.fromEntries(Object.entries(patch.style).filter(([, s]) => Object.keys(s).length > 0));
  const entry = { ...prev[key], style };
  if (key !== "liquidations") return { ...prev, [key]: entry };
  const measure = patch.params.measure === "notional" ? "notional" : ("size" as LiquidationMeasure);
  const markers = patch.params.markers !== false;
  return { ...prev, liquidations: { ...prev.liquidations, ...entry, measure, markers } };
}

/** The legend gear's modal for one Derivatives entry: the one settings dialog (`IndicatorSettingsDialog`),
 * with Liquidations' `measure` (size / notional) and `markers` (true / false) as its inputs. */
function DerivativeSettingsDialog({
  entryKey,
  settings,
  onApply,
  onRemove,
  onClose,
}: {
  entryKey: DerivativeKey;
  settings: DerivativesLayout;
  onApply: (patch: SettingsPatch) => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  const liquidations = settings.liquidations;
  const params: Record<string, unknown> =
    entryKey === "liquidations" ? { measure: liquidations.measure, markers: liquidations.markers } : {};
  const entry: IndicatorConfigEntry = {
    name: DERIVATIVE_LABELS[entryKey],
    category: "derivatives",
    params,
    style: settings[entryKey].style as IndicatorConfigEntry["style"],
  };
  const catalogEntry: IndicatorCatalogEntry = {
    params,
    panel: "",
    category: "derivatives",
    choices: entryKey === "liquidations" ? { measure: ["size", "notional"], markers: ["true", "false"] } : {},
    outputs: [], // a derivatives pane, not a catalog indicator: no alert reads its outputs
  };
  return (
    <IndicatorSettingsDialog
      title={DERIVATIVE_LABELS[entryKey]}
      entry={entry}
      catalogEntry={catalogEntry}
      outputs={derivativeOutputs(entryKey)}
      disabled={false}
      onApply={(patch) => {
        onApply(patch);
        // The layout saves itself (useChartLayout); a failed save is reported under the chart.
        return Promise.resolve(null);
      }}
      onRemove={onRemove}
      onClose={onClose}
    />
  );
}
const NONE: never[] = [];
// Story 33.10: the drawings on screen while all are hidden (a stable empty list, so no memo churns).
const NONE_DRAWN: Drawing[] = [];

/** A drawing with its `locked` / `hidden` flag set; a cleared flag is removed (absent = false), so an
 * unlocked drawing stores exactly what it did before it was ever locked. */
function withFlag(d: Drawing, flag: "locked" | "hidden", on: boolean): Drawing {
  if ((d[flag] === true) === on) return d;
  if (on) return { ...d, [flag]: true };
  const { [flag]: _cleared, ...rest } = d;
  return rest as Drawing;
}

/** Story 18.4's replay control bar. DW-146: a click that would do nothing is never silently
 * ignored -- a pick on a data gap says so, Play/Step forward at the newest loaded bar are disabled
 * with "End of loaded data" shown, Step back at the start marker is disabled with a tooltip. */
/** Story 33.12: why the Replay button is disabled in Lines mode (the operator's rule, 2026-10-07). */
const REPLAY_LINES_REASON = "Replay is available on candle charts";

function ReplayControls({ replay, onGoTo }: { replay: ReturnType<typeof useReplay>; onGoTo: () => void }) {
  if (replay.mode === "picking") {
    return (
      <span role="status">
        {replay.pickMissed ? "No bar at that time -- click a candle" : "Click a candle to start the replay"}
      </span>
    );
  }
  return (
    <>
      <button type="button" onClick={replay.togglePlay} disabled={!replay.isPlaying && replay.atEnd}>
        {replay.isPlaying ? "Pause" : "Play"}
      </button>
      <button
        type="button"
        aria-label="Step back"
        onClick={() => replay.step(-1)}
        disabled={replay.atStart}
        title={replay.atStart ? "At the replay start bar" : undefined}
      >
        &lt;
      </button>
      <button type="button" aria-label="Step forward" onClick={() => replay.step(1)} disabled={replay.atEnd}>
        &gt;
      </button>
      <button type="button" aria-label="Replay speed" onClick={replay.cycleSpeed}>
        {replay.speed}x
      </button>
      <button type="button" onClick={onGoTo}>
        Go to...
      </button>
      {replay.atEnd && <span role="status">End of loaded data</span>}
    </>
  );
}

interface ChartInnerProps {
  instrumentId: string;
  /** The coin's drawings, held by the page above this component: a timeframe change remounts it. */
  drawingStore: ChartDrawings;
  /** The instrument's decimals once any candles response has carried them, else `null`. */
  heldPrecision: InstrumentPrecision | null;
  onPrecision: (precision: InstrumentPrecision) => void;
  barSeconds: number;
  volumeOn: boolean;
  onVolumeChange: (on: boolean) => void;
  onTimeframeChange: (seconds: number) => void;
  /** Story 32.6: the coin's saved layout. Only its initial values seed this component (it is remounted
   * on a timeframe change and on Reset to default); every later change goes up through `onLayout`. */
  layout: ChartLayout;
  onLayout: (change: (prev: ChartLayout) => ChartLayout) => void;
  onSaveAsDefault: () => void;
  onResetToDefault: () => void;
  /** The tool each rail group last armed, by group id: held above the timeframe and coin remounts. */
  toolMemory: Partial<Record<string, ChartTool>>;
  onToolUsed: (groupId: string, tool: ChartTool) => void;
  /** Story 33.10: the drawing magnet, held beside `toolMemory` (view state, never persisted). */
  magnet: MagnetMode;
  onMagnet: (mode: MagnetMode) => void;
  /** Story 33.12: the stage's fullscreen, held above the timeframe remount (the stage element is). */
  fullscreen: Fullscreen;
  watchlistOpen: boolean;
  onWatchlistToggle: () => void;
}

function ChartInner({
  instrumentId,
  drawingStore,
  heldPrecision,
  onPrecision,
  barSeconds,
  volumeOn,
  onVolumeChange,
  onTimeframeChange,
  layout,
  onLayout,
  onSaveAsDefault,
  onResetToDefault,
  toolMemory,
  onToolUsed,
  magnet,
  onMagnet,
  fullscreen,
  watchlistOpen,
  onWatchlistToggle,
}: ChartInnerProps) {
  const [chart, setChart] = useState<IChartApi | null>(null);
  const patchLayout = useCallback(
    (patch: Partial<ChartLayout>): void => onLayout((prev) => ({ ...prev, ...patch })),
    [onLayout],
  );
  const [layoutMenuOpen, setLayoutMenuOpen] = useState(false);
  // The saved layout's values this component starts from (read once; later saves never re-seed it).
  const [initialLayout] = useState(layout);
  // Story 15.7: Candles/Lines toggle (AC #1) -- `dashboard.py`'s own #btn-candles/
  // #btn-lines pair, carried forward. Only one of useCandles/useSnapshotSeries is ever
  // `enabled` at a time (Task 2): the disabled one issues no requests but keeps whatever
  // it already loaded, so toggling back doesn't re-fetch from scratch.
  const [mode, setMode] = useState<ChartMode>(initialLayout.mode);
  useEffect(() => patchLayout({ mode }), [mode, patchLayout]);
  // Story 18.1 (AC #1): which drawing tool is armed; "cursor" is the do-nothing
  // default. The placed lines' own state lives HERE too, not inside LightweightChart
  // -- that component stays a pure function of its props (AD-F4), this page owns the
  // data. Deterministic counter ids (no uuid) keep specs stable and diffable.
  const [activeTool, setActiveTool] = useState<ChartTool>("cursor");
  // Story 32.5: every drawing (horizontal lines, trendlines, Fibonacci, positions) is one list in
  // one server-side resource, restored on mount and saved on change by `useChartDrawings`.
  const {
    drawings: allDrawings,
    setDrawings: setAllDrawings,
    undo: undoDrawings,
    redo: redoDrawings,
    canUndo,
    canRedo,
    status: drawingsStatus,
    saveError: drawingsSaveError,
    saveNow: saveDrawingsNow,
  } = drawingStore;
  const [settingsId, setSettingsId] = useState<string | null>(null);
  const allDrawingsRef = useRef(allDrawings);
  useEffect(() => {
    allDrawingsRef.current = allDrawings;
  }, [allDrawings]);
  // Story 33.10: Hide all drawings, persisted as the layout's `drawings_hidden`; while on, no drawing
  // is drawn or hit-tested and the drawing tools are off.
  const [drawingsHidden, setDrawingsHidden] = useState(initialLayout.drawings_hidden);
  useEffect(() => patchLayout({ drawings_hidden: drawingsHidden }), [drawingsHidden, patchLayout]);
  // The drawings on screen: none while all are hidden, and never one hidden from its menu.
  const shownDrawings = useMemo(
    () => (drawingsHidden ? NONE_DRAWN : allDrawings.filter((d) => d.hidden !== true)),
    [allDrawings, drawingsHidden],
  );
  const priceLines = useMemo<PriceLineSpec[]>(
    () =>
      shownDrawings.flatMap((d) =>
        d.kind === "hline"
          ? [
              {
                id: d.id,
                price: d.price,
                color: d.color ?? chartVar("--chart-drawing"),
                ...(d.line_width === undefined ? {} : { lineWidth: d.line_width }),
                ...(d.line_style === undefined ? {} : { lineStyle: d.line_style }),
                ...(d.locked ? { locked: true } : {}),
              },
            ]
          : [],
      ),
    [shownDrawings],
  );
  const [crosshairOn, setCrosshairOn] = useState(initialLayout.crosshair);
  useEffect(() => patchLayout({ crosshair: crosshairOn }), [crosshairOn, patchLayout]);
  const [indicatorDialogOpen, setIndicatorDialogOpen] = useState(false);
  const [overlaysDialogOpen, setOverlaysDialogOpen] = useState(false);
  const [alertDialogOpen, setAlertDialogOpen] = useState(false);
  // Story 33.8: the condition the alert dialog opens with (a drawing's "Add alert…"); null = empty.
  const [alertCondition, setAlertCondition] = useState<AlertCondition | null>(null);
  const openAlertDialog = useCallback((condition: AlertCondition | null) => {
    setAlertCondition(condition);
    setAlertDialogOpen(true);
  }, []);
  const [catalog, setCatalog] = useState<Record<string, IndicatorCatalogEntry>>({});
  // Story 18.2 / 33.10: a click-placed tool's points so far (`placementOf(tool)` in all), held until
  // the last click places the drawing (or Esc / a tool change discards them).
  const [placement, setPlacement] = useState<Placement | null>(null);
  const savedProfile = initialLayout.volume_profile;
  // A saved fixed range comes back with an empty profile that the first loaded candles fill (below).
  const [frvps, setFrvps] = useState<FrvpEntry[]>(() =>
    savedProfile.kind === "fixed" && savedProfile.start !== null && savedProfile.end !== null
      ? [{ id: "frvp-1", startTime: savedProfile.start, endTime: savedProfile.end, profile: EMPTY_PROFILE }]
      : [],
  );
  const [frvpSettings, setFrvpSettings] = useState(() =>
    savedProfile.kind === "fixed" ? profileSettings(savedProfile) : DEFAULT_VOLUME_PROFILE_SETTINGS,
  );
  const frvpHydratedRef = useRef(frvps.length === 0);
  const [edgeGhost, setEdgeGhost] = useState<EdgeGhost | null>(null);
  const nextFrvpIdRef = useRef(frvps.length + 1);
  // Story 18.7: the single visible-range profile ("always recompute", unlike FRVP above).
  const [vrvpActive, setVrvpActive] = useState(savedProfile.kind === "visible");
  // Story 18.8: the single session-profile slot (SVP / SVP HD presets).
  const [sessionCfg, setSessionCfg] = useState<SessionConfig | null>(() => initialSessionConfig(savedProfile));
  // DW-152: the clock the session history is anchored to. State, not a read of `Date.now()` in
  // render: set by the handlers that turn a profile on and re-armed by a timer at every period
  // rollover (see below), so a long-open tab moves its wanted start forward instead of growing.
  const [sessionNowMs, setSessionNowMs] = useState(() => Date.now());
  const [sessionCache] = useState<SessionProfileCache>(() => new Map());
  const [tpoCache] = useState<TpoDetailCache>(() => new WeakMap());
  const [vrvpSettings, setVrvpSettings] = useState(() =>
    savedProfile.kind === "visible" ? profileSettings(savedProfile) : DEFAULT_VOLUME_PROFILE_SETTINGS,
  );
  const {
    candles,
    volume: fullVolume,
    venueMarket,
    precision: candlesPrecision,
    loadFailed,
    loadError,
    refreshNewest,
    appendBar,
    openGapTo,
  } = useCandles(instrumentId, chart, mode === "candles", barSeconds);
  // The page above keeps the first response's precision across a timeframe remount.
  const precision = candlesPrecision ?? heldPrecision;
  useEffect(() => {
    if (candlesPrecision) onPrecision(candlesPrecision);
  }, [candlesPrecision, onPrecision]);
  const precisionRef = useRef(precision);
  useEffect(() => {
    precisionRef.current = precision;
  }, [precision]);
  // Story 18.4: replay only trims the NEWEST end of the loaded candles for display
  // (`replay.displayed`); useCandles and its older-history refill are untouched.
  const replay = useReplay(candles);
  const { cancelPick: cancelReplayPick, pick: pickReplayBar, mode: replayMode, cutoffTime } = replay;
  // Volume and every indicator pane are cut at the same replay time as the candles, or
  // they would show the "future" the replay hides.
  const volume = useMemo(() => trimAfter(fullVolume, cutoffTime), [fullVolume, cutoffTime]);

  const snapshotLines = useSnapshotSeries(instrumentId, chart, mode === "lines");
  // Story 15.5: the forming right-edge bar, over its own dedicated /ws/live socket
  // (AD-F7) -- same BAR_SECONDS constant useCandles uses, so the two paths can't drift.
  // Lines mode has no live-edge concept of its own (Task 3's Dev Note) -- LightweightChart
  // itself ignores `liveBar` while `mode === "lines"` (its own seriesRef is null there).
  // A bar the live socket closes is promoted into history state, so a later setData() (a
  // scroll-back prepend, replay, a mode flip) can never wipe bars the chart already showed;
  // bars closed while the socket was down exist only on the server, so a reconnect refetches
  // the newest page and merges it in.
  // Story 33.5: a closed bar and a reconnect also re-read the derivatives routes' newest pages (the
  // route wins for closed slots); the ref is set once `useChartDerivatives` below has run.
  const derivativesRefreshRef = useRef<() => void>(() => {});
  const derivativesAfterCloseRef = useRef<() => void>(() => {});
  const liveBar = useLiveCandle(instrumentId, barSeconds, {
    onReconnect: () => {
      void refreshNewest();
      derivativesRefreshRef.current();
    },
    onBarClosed: (bar) => {
      appendBar(bar);
      derivativesAfterCloseRef.current();
    },
  });
  // Story 32.1: a forming bar that starts more than one bar after history's newest point
  // (the collector came back after a hole) opens the gap run at once, not when it closes.
  // `historyLoaded` re-runs it once the first page lands: the socket may seed the forming bar
  // before REST returns, and openGapTo is a no-op (and idempotent) until history exists.
  const liveTime = liveBar?.time;
  const historyLoaded = candles.length > 0;
  useEffect(() => {
    if (liveTime !== undefined && historyLoaded) openGapTo(liveTime);
  }, [liveTime, historyLoaded, openGapTo]);

  // Known limit: this memo recomputes every anchored profile and VWAP on each bar update and each
  // forming-bar tick (the live socket's ~1/s, O(bars) per drawing). Upgrade path: cache per drawing like `buildSessionProfiles`, and extend the VWAP
  // incrementally from its last point.
  // Story 32.7: every drawing as the chart takes it. The Anchored VP and VWAP are computed here from
  // the candles the chart holds (what a replay has revealed): the profile by the one engine, the
  // line by `lib/anchoredVwap.ts`. Known limit: an anchor older than the oldest loaded bar draws
  // nothing until a scroll-back pages that bar in (the convention of every drawing, `BarGrid.snap`),
  // because a VWAP started mid-history would misstate it. Upgrade path: fetch the bars from the
  // anchor like the session profiles do (`useSessionCandles`). Both read candle bars, so Lines mode
  // (snapshot seconds on the time axis) shows neither.
  // The forming bar is part of "the latest bar" here (not under a replay, which hides it): a drawing
  // placed on it draws at once, and the line and profile include it, like the candle beside them.
  const anchoredLive = replay.mode === "active" ? null : liveBar;
  // Story 33.6: a stored-source Anchored VWAP's line is the server's (`AnchoredStoredVWAP`, the bars'
  // exact stored `pv` / volume), fetched by its own values hook: never a picker pane.
  // Story 33.10: only the drawings on screen: a hidden stored-source VWAP fetches nothing.
  const storedVwap = useStoredAnchoredVwap(instrumentId, chart, shownDrawings, barSeconds, mode === "candles");
  const anchored = useMemo(() => {
    // A coin with no anchored drawing (nearly every one) skips the per-tick copy of its bars. Story
    // 33.10: a hidden one (or every one, while all are hidden) draws neither its profile nor its line.
    if (mode !== "candles" || !shownDrawings.some((d) => d.kind === "anchored_vp" || d.kind === "anchored_vwap")) {
      return { specs: NONE, profiles: NONE, legend: NONE };
    }
    const specs: DrawingSpec[] = [];
    const profiles: VolumeProfileSpec[] = [];
    const legend: LegendExtra[] = [];
    const { candles: chartBars, volume: chartVolume } = withFormingBar(replay.displayed, volume, anchoredLive);
    const bars = timedBars(chartBars, chartVolume);
    // Anchors snap on the chart's real bars (gap slots excluded), volume or not; the primitive is
    // handed the snapped bar, so its anchor line and the profile start on the same bar.
    const times: number[] = [];
    for (const c of chartBars) if ("open" in c) times.push(c.time as number);
    const lastTime = times.at(-1);
    for (const d of shownDrawings) {
      if (d.kind !== "anchored_vp" && d.kind !== "anchored_vwap") continue;
      // An anchor after the newest displayed bar (a replay cut before it) is omitted, not snapped back.
      if (lastTime !== undefined && d.time > lastTime) continue;
      const at = snapIndex(times, d.time);
      const anchorBar = at === null ? null : times[at];
      if (d.kind === "anchored_vp") {
        const profile =
          anchorBar === null || lastTime === undefined
            ? EMPTY_PROFILE
            : buildRangeProfile(chartBars, chartVolume, anchorBar, lastTime, {
                rowCount: d.rows,
                valueAreaPercent: d.value_area_pct,
              });
        specs.push({ ...d, time: anchorBar ?? d.time, anchorPrice: profile.rows.at(-1)?.priceHigh ?? null });
        if (profile.rows.length > 0 && anchorBar !== null && lastTime !== undefined) {
          profiles.push({
            id: `avp-${d.id}`,
            profile,
            xAnchor: { time: anchorBar as Time },
            width: { toTime: lastTime as Time },
            throughEndBar: true,
            upColor: d.up_color,
            downColor: d.down_color,
            showPoc: true,
            showValueArea: true,
          });
        }
        continue;
      }
      const source = d.source;
      const stored = source === STORED_VWAP_SOURCE;
      let points: VwapPoint[] = [];
      // The stored line is the server's values, cut at the replay time like every pane; no bands. A
      // stored line whose request failed on any page draws nothing: the pages that did load would be
      // a partial line passed off as whole (audit D-187); the legend names the error.
      if (anchorBar !== null && stored && storedVwap.errors[d.id] === undefined) {
        points = storedVwapPoints(trimAfter(storedVwap.values[d.id] ?? NONE, cutoffTime), anchorBar);
      } else if (anchorBar !== null && !stored) {
        points = breakAtGaps(anchoredVwap(bars, anchorBar, source), chartBars);
      }
      // The snapped bar, like the Anchored VP: the anchor handle sits on the bar the line starts from.
      specs.push({ ...d, ...(stored ? { bands: false } : {}), time: anchorBar ?? d.time, points });
      const latest = points.at(-1);
      const color = d.color ?? chartVar("--chart-drawing");
      const error = stored ? storedVwap.errors[d.id] : undefined;
      legend.push({
        id: `avwap-${d.id}`,
        label: `AVWAP (${d.source})`,
        color,
        value: latest === undefined || precision === null ? null : latest.vwap,
        format: (value) => (precision === null ? String(value) : safeDecimal(value, precision.price)),
        ...(error === undefined ? {} : { text: `failed: ${error}` }),
      });
    }
    // Shared empty arrays: a coin with none (nearly every one) must not hand the chart a fresh
    // array, hence a "changed" prop, on every bar.
    return { specs: specs.length > 0 ? specs : NONE, profiles: profiles.length > 0 ? profiles : NONE, legend: legend.length > 0 ? legend : NONE };
  }, [shownDrawings, mode, replay.displayed, volume, anchoredLive, precision, storedVwap, cutoffTime]);
  const plainDrawings = useMemo<DrawingSpec[]>(
    () =>
      shownDrawings.flatMap((d): DrawingSpec[] => {
        if (d.kind === "hline" || d.kind === "anchored_vp" || d.kind === "anchored_vwap") return [];
        // The line kinds' spec carries its colour resolved (the trendline's since 18.2).
        if (isLineDrawing(d)) return [{ ...d, color: d.color ?? chartVar("--chart-drawing") }];
        return [d];
      }),
    [shownDrawings],
  );
  const drawings = useMemo<DrawingSpec[]>(
    () => (anchored.specs.length === 0 ? plainDrawings : [...plainDrawings, ...anchored.specs]),
    [plainDrawings, anchored.specs],
  );

  // Story 15.6: the picker's persisted selection for this coin -- IndicatorPicker owns
  // the GET (initial load)/PUT (every add/remove/param-apply) round trip and reports the
  // resulting list here; this component decides how it becomes panes (AD-F4).
  const [pickerEntries, setPickerEntries] = useState<IndicatorConfigEntry[]>([]);
  const [indicatorErrors, setIndicatorErrors] = useState<Record<string, string>>({});
  const pickerValues = usePickerIndicatorValues(instrumentId, chart, pickerEntries, barSeconds, setIndicatorErrors);
  // First-seen order, not a fresh alphabetical sort every render -- assignPaneColor's own
  // invariant ("an id's color never changes while it stays in the list") only holds if
  // this order list is itself stable. Re-sorting Object.keys(pickerValues) on every
  // change shifts every other already-plotted key's index (and therefore color) whenever
  // one indicator is added/removed, even ones the user didn't touch. Computed via React's
  // "adjust state during render" pattern (not a ref mutated in useMemo, which is an
  // anti-pattern React itself warns on) -- bails out on identical `pickerValues` object
  // identity, which only changes when usePickerIndicatorValues actually calls
  // setSeriesByKey (a real data change), never on an unrelated re-render.
  const [seenPickerValues, setSeenPickerValues] = useState(pickerValues);
  const [pickerSeriesKeys, setPickerSeriesKeys] = useState<string[]>([]);
  if (pickerValues !== seenPickerValues) {
    setSeenPickerValues(pickerValues);
    const current = new Set(Object.keys(pickerValues));
    const nextOrder = pickerSeriesKeys.filter((key) => current.has(key));
    for (const key of Object.keys(pickerValues).sort()) {
      if (current.has(key) && !nextOrder.includes(key)) nextOrder.push(key);
    }
    setPickerSeriesKeys(nextOrder);
  }

  // Declarative pane set fed into LightweightChart's own registry (AD-F4) -- this
  // component never calls chart.addPane()/addSeries() itself. Default is just the volume
  // pane (derived from useCandles' own `v` field; absent when toggled off -- `fullVolume`
  // is fetched regardless, for the profiles and the measurement tool); picker entries
  // follow, one series per `{indicator_id}.{output_attr}` key, placed by the catalog's `panel`.
  // Story 32.3: the eye of the Volume row only hides it (collapses its pane); the state is not
  // persisted (it is not a field of Story 32.6's layout). Known limit: a timeframe change remounts
  // this component and shows it again. Upgrade path: a `volume_hidden` field in the layout table.
  const [volumeHidden, setVolumeHidden] = useState(false);
  // Switching volume off also clears the eye, so switching it back on shows it (not collapsed).
  const changeVolumeOn = useCallback(
    (on: boolean): void => {
      if (!on) setVolumeHidden(false);
      onVolumeChange(on);
    },
    [onVolumeChange],
  );
  // Story 33.6: how the Volume pane colours its bars (the layout's `volume_color_by`), set from the
  // Volume legend row's gear. Each bar is painted here, the forming one by the chart with the same
  // mapping (`liveVolumeColor`); the data in state keeps its flow for the next mode change.
  const [volumeColorBy, setVolumeColorBy] = useState<VolumeColorMode>(initialLayout.volume_color_by);
  useEffect(() => patchLayout({ volume_color_by: volumeColorBy }), [volumeColorBy, patchLayout]);
  const [volumeDialogOpen, setVolumeDialogOpen] = useState(false);
  const volumePalette = useMemo(
    () => ({ up: chartVar("--chart-up"), down: chartVar("--chart-down"), neutral: assignPaneColor("volume", DEFAULT_PANE_IDS) }),
    [],
  );
  const paintedVolume = useMemo(
    () =>
      volume.map((d) =>
        "value" in d
          ? { time: d.time, value: d.value, color: volumeBarColor(d, volumeColorBy, volumePalette.up, volumePalette.down, volumePalette.neutral) }
          : d,
      ),
    [volume, volumeColorBy, volumePalette],
  );
  const liveVolumeColor =
    liveBar === null || replay.mode === "active"
      ? undefined
      : volumeBarColor(
          { o: liveBar.open, c: liveBar.close, buy_v: liveBar.buy_v, sell_v: liveBar.sell_v },
          volumeColorBy,
          volumePalette.up,
          volumePalette.down,
          volumePalette.neutral,
        );
  const entriesById = useMemo(() => new Map(pickerEntries.map((e) => [entryId(e), e] as const)), [pickerEntries]);
  // The coin's indicator instance ids: the pane ids a saved height may still belong to.
  const paneIdsRef = useRef<ReadonlySet<string>>(new Set());
  paneIdsRef.current = new Set(entriesById.keys());
  const panes = useMemo<IndicatorPaneSpec[]>(
    () => [
      ...(volumeOn
        ? [
            {
              id: "volume",
              kind: "Histogram" as const,
              data: paintedVolume,
              color: volumePalette.neutral,
              groupLabel: "Volume",
              hidden: volumeHidden,
            },
          ]
        : []),
      ...pickerSeriesKeys.map((key): IndicatorPaneSpec => {
        const panel = panelForKey(key, catalog);
        const { id: instanceId, output } = splitSeriesKey(key);
        const entry = entriesById.get(instanceId);
        const name = entry?.name ?? catalogNameForKey(key, catalog) ?? key;
        const catalogEntry = catalog[catalogNameForKey(key, catalog) ?? ""];
        const style = outputStyle(entry, output);
        // Story 33.6: an output the catalog gives a unit prints at the instrument's decimals (a
        // native entry has none, and keeps the legend's default readout, as does every entry while
        // the precision is unknown).
        const unit = catalogEntry?.units?.[output];
        const format =
          precision !== null && isIndicatorUnit(unit)
            ? (value: number): string => formatIndicatorValue(value, unit, precision)
            : undefined;
        // Story 33.11: a CandlePattern drawn as markers (its default) keeps only a legend row here,
        // on an invisible overlay that never scales the price axis; the markers are `patternMarkers`.
        // A stale series no entry owns follows the same default.
        const markersOnly = name === PATTERN_INDICATOR && (entry === undefined || drawsPatternMarkers(entry));
        const plot = plotOf(catalogEntry, output);
        const shape = markersOnly
          ? { kind: "Line" as const, placement: "overlay" as const, markersOnly: true, format: patternReadout(patternOf(entry, catalogEntry, instanceId)) }
          : {
              kind: panel === "histogram" ? ("Histogram" as const) : ("Line" as const),
              placement: panel === "overlay" ? ("overlay" as const) : ("pane" as const),
              ...(plot ? { plot } : {}),
              ...(format ? { format } : {}),
            };
        return {
          id: key,
          // One legend row, one pane per instance (RSI(14) and RSI(21) are two), even for stale
          // values no entry owns any more -- those get no buttons (the picker finds no entry).
          group: instanceId,
          groupLabel: entry ? legendTitle(entry, catalogEntry?.note) : name,
          outputLabel: output,
          data: trimAfter(pickerValues[key], cutoffTime),
          // Combined with DEFAULT_PANE_IDS so a picker series never lands on volume's slot. A
          // colour the entry stores wins over the palette slot.
          color: style.color ?? assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
          ...shape,
          hidden: entry?.hidden === true,
          actionable: entry !== undefined,
          lineWidth: style.line_width,
          lineStyle: style.line_style,
          upColor: style.up_color,
          downColor: style.down_color,
        };
      }),
    ],
    [volumeOn, volumeHidden, paintedVolume, volumePalette, pickerSeriesKeys, pickerValues, catalog, entriesById, cutoffTime, precision],
  );

  // The time scale's bar spacing (px), reported by the chart: every candle-series marker (pattern
  // and liquidation) hides at or below `MARKER_MIN_BAR_SPACING_PX` (`markersHiddenAt`).
  const [barSpacing, setBarSpacing] = useState(Number.POSITIVE_INFINITY);

  // Story 33.11: each shown CandlePattern entry's hits as markers on the candles, from the values the
  // replay served up to its cursor (`trimAfter`): a hidden entry (the eye) or one drawn as a pane has
  // none, and zoomed out past the marker spacing (`markersHiddenAt`) neither. Coloured by the entry's
  // stored up/down colours, else the candles' own.
  const patternMarkers = useMemo(
    () =>
      pickerSeriesKeys.flatMap((key) => {
        const { id, output } = splitSeriesKey(key);
        const entry = entriesById.get(id);
        if (output !== "value" || !entry || entry.hidden || !drawsPatternMarkers(entry)) return [];
        const style = outputStyle(entry, output);
        return buildPatternMarkers(id, patternOf(entry, catalog[PATTERN_INDICATOR]), trimAfter(pickerValues[key] ?? [], cutoffTime), {
          up: style.up_color ?? chartVar("--chart-up"),
          down: style.down_color ?? chartVar("--chart-down"),
          neutral: style.color ?? assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
        }, barSpacing);
      }),
    [pickerSeriesKeys, pickerValues, entriesById, catalog, cutoffTime, barSpacing],
  );

  // Story 32.8: the volume footprint, a field of the coin's layout (on/off and its settings). It is
  // fetched only while on AND in Candles mode (its primitive draws on the candle series); off, no
  // request is issued and the chart gets no footprint. Only a row-size change refetches.
  const [footprint, setFootprint] = useState<FootprintSettings>(initialLayout.footprint);
  useEffect(() => patchLayout({ footprint }), [footprint, patchLayout]);
  const [footprintDialogOpen, setFootprintDialogOpen] = useState(false);
  const changeFootprintOn = useCallback((on: boolean): void => setFootprint((prev) => ({ ...prev, on })), []);
  const footprintActive = footprint.on && mode === "candles";
  const footprintData = useFootprint(instrumentId, chart, barSeconds, footprintActive, footprint.row_ticks);
  const footprintSpec = useMemo<FootprintRenderSpec | null>(
    () =>
      footprintActive
        ? {
            // Replay hides the bars after its cursor; their footprints with them.
            items: cutoffTime === null ? footprintData.items : footprintData.items.filter((b) => b.t / 1000 <= cutoffTime),
            precision: footprintData.precision,
            settings: footprint,
          }
        : null,
    [footprintActive, footprintData.items, footprintData.precision, footprint, cutoffTime],
  );

  // Story 33.5: the Derivatives group (Open Interest, Funding, Basis, Mark / Index, Liquidations), a
  // field of the coin's layout; the Liquidation tape is view state. Spot (the candles' `market`)
  // disables the group and fetches nothing, its saved on-states kept.
  const [derivatives, setDerivatives] = useState<DerivativesLayout>(initialLayout.derivatives);
  useEffect(() => patchLayout({ derivatives }), [derivatives, patchLayout]);
  const [tapeOn, setTapeOn] = useState(false);
  const [derivativeSettings, setDerivativeSettings] = useState<DerivativeKey | null>(null);
  const changeDerivativeOn = useCallback(
    (key: DerivativeKey, on: boolean): void => setDerivatives((prev) => ({ ...prev, [key]: { ...prev[key], on } })),
    [],
  );
  const derivativesData = useChartDerivatives({
    instrumentId,
    barSeconds,
    chart,
    settings: derivatives,
    market: venueMarket?.market ?? null,
    candlesMode: mode === "candles",
    bars: replay.displayed,
    liveTime: replay.mode === "active" || !liveBar ? null : (liveBar.time as number),
    cutoff: cutoffTime,
    barSpacing,
    tapeOn,
  });
  useEffect(() => {
    derivativesRefreshRef.current = derivativesData.refreshNewest;
    derivativesAfterCloseRef.current = derivativesData.refreshAfterClose;
  }, [derivativesData.refreshNewest, derivativesData.refreshAfterClose]);

  // Story 33.9: the price pane's chart type, right scale and compare symbols, each a field of the
  // coin's layout. Lines mode keeps them stored but draws no compare and no chart type.
  const [chartType, setChartType] = useState<ChartType>(initialLayout.chart_type);
  useEffect(() => patchLayout({ chart_type: chartType }), [chartType, patchLayout]);
  const [priceScale, setPriceScale] = useState<PriceScaleLayout>(initialLayout.price_scale);
  useEffect(() => patchLayout({ price_scale: priceScale }), [priceScale, patchLayout]);
  const [compare, setCompare] = useState<CompareLayout>(initialLayout.compare);
  useEffect(() => patchLayout({ compare }), [compare, patchLayout]);
  const patchPriceScale = useCallback(
    (patch: PriceScalePatch): void => setPriceScale((prev) => ({ ...prev, ...patch })),
    [],
  );
  const [compareFeeds, setCompareFeeds] = useState<Record<string, CompareFeedState>>({});
  const onCompareFeed = useCallback((iid: string, state: CompareFeedState | null): void => {
    setCompareFeeds((prev) => {
      if (state !== null) return { ...prev, [iid]: state };
      const { [iid]: _gone, ...rest } = prev;
      return rest;
    });
  }, []);
  // The legend eye hides a compare line in place; view state, never saved.
  const [compareHidden, setCompareHidden] = useState<ReadonlySet<string>>(() => new Set());
  const comparesDrawn = mode === "candles" && compare.symbols.length > 0;
  // A compare draws on the Percent scale (each line from 0 % at the left edge); Indexed to 100 is the
  // other scale that compares series. Only an explicit pick writes `price_scale.mode`, so removing the
  // last compare brings back the operator's own mode.
  const effectiveScaleMode: PriceScaleModeName = comparesDrawn
    ? priceScale.mode === "indexed"
      ? "indexed"
      : "percent"
    : priceScale.mode;
  const scaleLockReason = comparesDrawn ? "A compare symbol draws on the percent scale: remove it to use Normal or Log" : null;
  const spreadAvailable = mode === "candles" && compare.symbols.length === 1;
  const mainLiveShown = replay.mode === "active" ? null : liveBar;
  const comparePanes = useMemo<IndicatorPaneSpec[]>(() => {
    if (!comparesDrawn) return NONE;
    const slots = mainSlots(replay.displayed, mainLiveShown);
    const liveShown = mainLiveShown !== null;
    const specs = comparePaneSpecs(compare.symbols, compareFeeds, slots, liveShown, compareHidden);
    if (!compare.spread || !spreadAvailable) return specs;
    const [only] = compare.symbols;
    return [...specs, spreadPaneSpec(instrumentId, only, replay.displayed, mainLiveShown, compareFeeds[only], slots, liveShown)];
  }, [comparesDrawn, compare, compareFeeds, compareHidden, replay.displayed, mainLiveShown, spreadAvailable, instrumentId]);
  // The add re-checks duplicate and maximum against the latest state, not the render the control saw.
  const addCompare = useCallback(
    (iid: string): void => setCompare((prev) => withCompareAdded(prev, iid, instrumentId)),
    [instrumentId],
  );
  const removeCompare = useCallback((iid: string): void => {
    setCompare((prev) => ({ ...prev, symbols: prev.symbols.filter((s) => s !== iid) }));
    // A re-added symbol starts shown: its hidden flag goes with it.
    setCompareHidden((prev) => {
      if (!prev.has(iid)) return prev;
      const next = new Set(prev);
      next.delete(iid);
      return next;
    });
  }, []);

  // Story 33.12: how times print, the session breaks, the bar countdown and the last-price line and
  // label, each a field of the coin's layout (fullscreen is not: it is view state, never saved).
  const [timeZone, setTimeZone] = useState<TimeZoneSetting>(initialLayout.time_zone);
  useEffect(() => patchLayout({ time_zone: timeZone }), [timeZone, patchLayout]);
  const [sessionBreaks, setSessionBreaks] = useState(initialLayout.session_breaks);
  useEffect(() => patchLayout({ session_breaks: sessionBreaks }), [sessionBreaks, patchLayout]);
  const [barCountdownOn, setBarCountdownOn] = useState(initialLayout.bar_countdown);
  useEffect(() => patchLayout({ bar_countdown: barCountdownOn }), [barCountdownOn, patchLayout]);
  const [lastPrice, setLastPrice] = useState<LastPriceLayout>(initialLayout.last_price);
  useEffect(() => patchLayout({ last_price: lastPrice }), [lastPrice, patchLayout]);
  // The countdown counts the forming bar: there is none in Lines mode or under a replay.
  const countdown = useMemo(
    () => ({ barSeconds, enabled: barCountdownOn && mode === "candles" && replay.mode !== "active" }),
    [barSeconds, barCountdownOn, mode, replay.mode],
  );

  // Story 33.12: the symbol search (navigate), the compare search (`CompareControl`, Alt+C) and the
  // `?` sheet, and the timeframe typed on the keyboard so far (shown in a chip until Enter).
  const navigate = useNavigate();
  const [searchOpen, setSearchOpen] = useState(false);
  const [compareSearchOpen, setCompareSearchOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [timeframeBuffer, setTimeframeBuffer] = useState("");
  useEffect(() => {
    if (timeframeBuffer === "") return;
    const id = setTimeout(() => setTimeframeBuffer(""), TIMEFRAME_BUFFER_IDLE_MS);
    return () => clearTimeout(id);
  }, [timeframeBuffer]);

  const chartPanes = useMemo(
    () =>
      derivativesData.panes.length === 0 && comparePanes.length === 0
        ? panes
        : [...panes, ...comparePanes, ...derivativesData.panes],
    [panes, comparePanes, derivativesData.panes],
  );

  // The legend's eye / gear / x. Picker indicators go through the picker's own persist path (the
  // same one an add uses); Volume is page state (eye) and the Indicators dialog's toggle (x); the
  // Footprint row has the gear (its settings modal) and the x (off).
  const pickerRef = useRef<IndicatorPickerHandle>(null);
  const handleLegendAction = useCallback(
    (action: LegendAction, group: string): void => {
      // Story 33.9: a compare's x removes it from the layout, its eye hides the line; the Spread pane's
      // eye and x both turn the spread off (the Spread toggle brings it back).
      if (group.startsWith(COMPARE_GROUP_PREFIX)) {
        const iid = group.slice(COMPARE_GROUP_PREFIX.length);
        if (action === "remove") removeCompare(iid);
        else if (action === "hide") setCompareHidden((prev) => toggled(prev, iid));
        return;
      }
      if (group === SPREAD_GROUP) {
        if (action !== "settings") setCompare((prev) => ({ ...prev, spread: false }));
        return;
      }
      const derivative = derivativeOfGroup(group);
      if (derivative !== null) {
        // The eye and the x both turn the entry off in the layout (its pane goes; the Indicators
        // dialog brings it back); the gear opens its settings.
        if (action === "settings") setDerivativeSettings(derivative);
        else changeDerivativeOn(derivative, false);
        return;
      }
      if (group === "footprint") {
        if (action === "settings") setFootprintDialogOpen(true);
        else if (action === "remove") changeFootprintOn(false);
        return;
      }
      if (group === "volume") {
        if (action === "hide") setVolumeHidden((h) => !h);
        else if (action === "settings") setVolumeDialogOpen(true);
        else if (action === "remove") {
          changeVolumeOn(false);
        }
        return;
      }
      const picker = pickerRef.current;
      if (action === "hide") picker?.toggleHidden(group);
      else if (action === "settings") picker?.openSettings(group);
      else picker?.remove(group);
    },
    [changeVolumeOn, changeFootprintOn, changeDerivativeOn, removeCompare],
  );
  // Seeded with what the chart draws while the entry stores nothing: the palette colour, which a
  // histogram also paints both signs with (and the side whose colour is not set keeps).
  const settingsOutputs = (id: string): SettingsOutput[] =>
    pickerSeriesKeys
      .filter((key) => splitSeriesKey(key).id === id)
      .map((key) => {
        const palette = assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]);
        // A CandlePattern drawn as markers paints its hits in the candles' colours while none is stored.
        const markers = drawsPatternMarkers(entriesById.get(id));
        return {
          label: splitSeriesKey(key).output,
          kind: panelForKey(key, catalog) === "histogram" ? "Histogram" : "Line",
          defaultColor: palette,
          defaultUpColor: markers ? chartVar("--chart-up") : palette,
          defaultDownColor: markers ? chartVar("--chart-down") : palette,
        };
      });

  useEffect(() => {
    chart?.applyOptions({ crosshair: { mode: crosshairOn ? CrosshairMode.Normal : CrosshairMode.Hidden } });
  }, [chart, crosshairOn]);

  useEffect(() => {
    fetchIndicatorCatalog()
      .then(setCatalog)
      .catch((err: unknown) => console.error("ChartPage: failed to load indicator catalog", err));
  }, []);

  useEffect(() => {
    // Story 18.1 (AC #5): Esc cancels the active tool from anywhere on the page, not
    // just from a focused chart -- an armed tool with no in-chart escape is exactly
    // the stranded state this prevents. Runs regardless of the current tool: Esc in
    // cursor mode is a harmless no-op.
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setActiveTool("cursor");
        setPlacement(null);
        cancelReplayPick();
        setTimeframeBuffer("");
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [cancelReplayPick]);

  // Story 33.10: undo and redo act only on drawings the operator can see and edit: loaded, not all
  // hidden, and no placement half done (its points would refer to a list that just changed).
  const historyActive = drawingsStatus === "ready" && !drawingsHidden && placement === null;
  // Story 33.12: the page's one shortcut handler (`lib/shortcuts.ts`), drawing undo / redo included,
  // subscribed once and reading the latest render through this ref. Nothing acts while the operator
  // types in a field or a dialog is open (`isTypingContext`); Esc keeps its own handler above.
  const shortcutRef = useRef<(event: KeyboardEvent) => void>(() => {});
  shortcutRef.current = (event: KeyboardEvent): void => {
    if (isTypingContext(event)) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === "z" || key === "y")) {
      // Ctrl/Cmd+Z undoes a drawing edit, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes it.
      if (!historyActive) return;
      event.preventDefault();
      if (key === "y" || event.shiftKey) redoDrawings();
      else undoDrawings();
      return;
    }
    const action = shortcutFor(event);
    if (action === null) return;
    if (action.kind === "timeframe_enter" && timeframeBuffer === "") return; // Enter on a button stays a click
    event.preventDefault();
    // A held key's auto-repeat is one press: it must not flip fullscreen, log or replay back and forth.
    if (event.repeat) return;
    runShortcut(action);
  };
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent): void => shortcutRef.current(event);
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // useCallback (not inline arrows) so LightweightChart's interaction effects don't
  // tear down and re-attach their subscriptions on every render of this page -- the
  // drag machinery specifically must not be resubscribed mid-drag by an unrelated
  // re-render.
  const roundPrice = useCallback((price: number): number => {
    const places = precisionRef.current?.price;
    return places === undefined ? price : roundToPrecision(price, places);
  }, []);

  const handlePriceClick = useCallback(
    (price: number): void => {
      // Story 18.1 (AC #2): single-click-and-done -- only an armed hline tool places
      // a line, and the placement itself disarms it (the tool's interaction model,
      // not a persistent multi-click mode).
      if (activeTool !== "hline") return;
      // The resource stores only a price above zero: a click at or below it places nothing (the
      // tool stays armed) rather than a line that would make every save of the coin a 422.
      const placed = roundPrice(price);
      if (!(placed > 0)) return;
      setAllDrawings((all) => [
        ...all,
        { kind: "hline", id: nextDrawingId(all, "hline"), price: placed, color: chartVar("--chart-drawing") },
      ]);
      setActiveTool("cursor");
    },
    [activeTool, roundPrice, setAllDrawings],
  );

  // Story 33.10: places a finished click placement's drawing (`buildDrawing`) and reports whether it
  // did: a degenerate one (the second point on the first) or a position before the precision is
  // known places nothing. A text note opens its dialog at once, to be typed.
  const placeDrawing = useCallback(
    (tool: ChartTool, points: Anchor[]): boolean => {
      const kind = kindOfTool(tool);
      if (kind === null) return false;
      const id = nextDrawingId(allDrawingsRef.current, kind);
      const drawing = buildDrawing(tool, id, points, newDrawingContext(precisionRef.current));
      if (drawing === null) return false;
      setAllDrawings((all) => [...all, drawing]);
      if (drawing.kind === "text") setSettingsId(id);
      return true;
    },
    [setAllDrawings],
  );

  const handlePointClick = useCallback(
    (clicked: ChartPoint): void => {
      // Story 18.4 (AC #2): while picking a replay start, a click selects that bar and
      // nothing else -- drawing tools are disarmed on entry, this guards the same click.
      // A click on a gap slot stays in picking; the hook's `pickMissed` drives the hint (DW-146).
      if (replayMode === "picking") {
        pickReplayBar(clicked.time as number);
        return;
      }
      // Story 18.2 / 33.10: a click-placed tool collects `placementOf(tool)` points; the last one
      // places the drawing and disarms the tool. A point on the previous one (time and price, on the
      // grid) would make a zero-size drawing, so it is ignored and the tool stays armed.
      const need = placementOf(activeTool);
      if (need === 0) return;
      const point: Anchor = { time: clicked.time as number, price: clicked.price };
      const points = placement?.tool === activeTool ? placement.points : [];
      const previous = points.at(-1);
      if (previous && storedTime(previous.time) === storedTime(point.time) && roundPrice(previous.price) === roundPrice(point.price)) {
        return;
      }
      const next = [...points, point];
      // A channel's B on A's bar (vertical) or a C giving it no width is ignored the same way.
      if (activeTool === "channel" && !channelPlaceable(next, precisionRef.current?.price ?? null)) return;
      if (next.length < need) {
        setPlacement({ tool: activeTool, points: next });
        return;
      }
      if (!placeDrawing(activeTool, next)) return;
      setPlacement(null);
      setActiveTool("cursor");
    },
    [activeTool, placement, replayMode, pickReplayBar, roundPrice, placeDrawing],
  );

  // Story 32.5: the Fibonacci drag's release. Anchor A is the press, B the release; a drag whose
  // two prices are one price on the grid (a horizontal drag, or one under half a tick) would draw
  // every level on one line and is ignored (the tool stays armed).
  const handleFibPlace = useCallback(
    (a: TrendlineAnchor, b: TrendlineAnchor): void => {
      if (roundPrice(a.price) === roundPrice(b.price)) return;
      setAllDrawings((all) => [
        ...all,
        {
          kind: "fib",
          id: nextDrawingId(all, "fib"),
          anchors: [
            { time: storedTime(a.time as number), price: roundPrice(a.price) },
            { time: storedTime(b.time as number), price: roundPrice(b.price) },
          ],
          levels: defaultFibLevels(fibLevelColor),
          extend_right: true,
          label_side: "left",
          line_width: 1,
        },
      ]);
      setActiveTool("cursor");
    },
    [roundPrice, setAllDrawings],
  );

  // Story 18.6 (AC #2): one calculation per confirmed range, over the FULL loaded data so the
  // stored profile is right once a replay ends; while a replay is active the display
  // rebuilds from the revealed bars only (see `volumeProfiles`), never showing the future.
  const handleRangeSelect = (start: TrendlineAnchor, end: TrendlineAnchor): void => {
    const startTime = Math.min(start.time as number, end.time as number);
    const endTime = Math.max(start.time as number, end.time as number);
    const profile = buildRangeProfile(candles, fullVolume, startTime, endTime, frvpSettings);
    if (profile.rows.length === 0) return;
    const id = `frvp-${nextFrvpIdRef.current++}`;
    setFrvps((all) => [...all, { id, startTime, endTime, profile }]);
    setActiveTool("cursor");
  };

  // The ghost only moves the edge visually while dragging; the profile itself is rebuilt
  // once, on release (AC #3, Task 4).
  const handleEdgeDrag = useCallback((id: string, edge: "start" | "end", time: Time): void => {
    setEdgeGhost({ id, edge, time: time as number });
  }, []);

  // DW-150: the chart tore its edge drag down before the release (a tool armed mid-drag): the
  // ghost goes, the profile stays as it was.
  const handleEdgeCancel = useCallback((): void => setEdgeGhost(null), []);

  const handleEdgeCommit = (id: string, edge: "start" | "end", time: Time): void => {
    setEdgeGhost(null);
    setFrvps((all) =>
      all.map((f) => {
        if (f.id !== id) return f;
        const a = edge === "start" ? (time as number) : f.startTime;
        const b = edge === "end" ? (time as number) : f.endTime;
        const startTime = Math.min(a, b);
        const endTime = Math.max(a, b);
        const profile = buildRangeProfile(candles, fullVolume, startTime, endTime, frvpSettings);
        return profile.rows.length === 0 ? f : { ...f, startTime, endTime, profile };
      }),
    );
  };

  const handleFrvpSettings = (next: typeof frvpSettings): void => {
    setFrvpSettings(next);
    // A settings change is an explicit user action, so it rebuilds the placed profiles from
    // their stored ranges (row count / value area change the calculation itself).
    setFrvps((all) =>
      all.map((f) => ({ ...f, profile: buildRangeProfile(candles, fullVolume, f.startTime, f.endTime, next) })),
    );
  };

  const volumeProfiles = useMemo<VolumeProfileSpec[]>(
    () =>
      frvps.map((f) => {
        const ghost = edgeGhost?.id === f.id ? edgeGhost : null;
        const a = ghost?.edge === "start" ? ghost.time : f.startTime;
        const b = ghost?.edge === "end" ? ghost.time : f.endTime;
        const startTime = Math.min(a, b) as Time;
        const endTime = Math.max(a, b) as Time;
        return {
          id: f.id,
          // Replay hides the future: rebuild from the revealed bars while it is active.
          profile:
            cutoffTime === null
              ? f.profile
              : buildRangeProfile(replay.displayed, volume, f.startTime, f.endTime, frvpSettings),
          xAnchor: { time: startTime },
          width: { toTime: endTime },
          upColor: frvpSettings.upColor,
          downColor: frvpSettings.downColor,
          showPoc: frvpSettings.showPoc,
          showValueArea: frvpSettings.showValueArea,
          edges: { startTime, endTime },
        };
      }),
    [frvps, edgeGhost, frvpSettings, cutoffTime, replay.displayed, volume],
  );

  // Story 18.7 (AC #2/#3): rebuilt from whatever is visible on EVERY visible-range change
  // (and when the candles/settings change) -- the mirror image of FRVP's confirm-once
  // entries, kept as its own single-instance state, not folded into `frvps`. Candles mode
  // only (Lines mode's time axis is snapshot seconds, not the candle bars profiled here).
  // Subscribed only while the VRVP is on (Task 2: unsubscribe when removed).
  // DW-151: the range is read once per animation frame, however many pan events the frame held.
  const visibleRange = useVisibleRange(vrvpActive && mode === "candles" ? chart : null);
  const visibleFrom = visibleRange?.from ?? null;
  const visibleTo = visibleRange?.to ?? null;
  const vrvpProfile = useMemo(
    () =>
      vrvpActive && mode === "candles" && visibleFrom !== null && visibleTo !== null
        ? buildRangeProfile(replay.displayed, volume, visibleFrom, visibleTo, vrvpSettings)
        : null,
    [vrvpActive, mode, visibleFrom, visibleTo, replay.displayed, volume, vrvpSettings],
  );

  // Story 18.8: one independent profile per session, from its own finest-timeframe fetch;
  // only the newest session is rebuilt as bars arrive (buildSessionProfiles' cache).
  const sessionActive = sessionCfg !== null && mode === "candles";
  // The Auto Anchored profile has no period of its own (its anchor follows the chart's bars).
  const sessionPeriod = sessionActive && sessionCfg.preset !== "auto" ? sessionCfg.period : null;
  // While replaying, the sessions end at the replay cutoff, so the history is wanted back from there.
  const sessionAnchor = cutoffTime ?? Math.floor(sessionNowMs / 1000);
  const sessionSince = sessionCfg
    ? periodStartBack(sessionAnchor, sessionCfg.period, sessionCfg.settings.sessionCount - 1)
    : 0;
  // The clock keeps ticking through a replay, so leaving it never resumes from a stale period.
  useEffect(() => {
    if (sessionPeriod === null) return;
    const untilRollover = periodEnd(Math.floor(sessionNowMs / 1000), sessionPeriod) * 1000 - Date.now();
    const id = setTimeout(
      () => setSessionNowMs(Date.now()),
      Math.min(Math.max(0, untilRollover), ROLLOVER_TIMER_CAP_MS),
    );
    return () => clearTimeout(id);
  }, [sessionPeriod, sessionNowMs]);
  // Story 32.7: the Auto Anchored profile re-resolves its anchor from the chart's bars on every change
  // (a bar that crosses a session boundary, a timeframe change remounts this component), so the
  // anchor, its marker and its profile always follow the latest bar. A calendar anchor (session /
  // week / month) is profiled from its own fetch, like the session profiles, because the chart's
  // loaded window may start after the period does; the extreme anchors read the chart's loaded bars.
  const autoPreset = sessionCfg?.preset === "auto" ? sessionCfg.anchor : null;
  const chartAnchorBars = useMemo(() => anchorBars(replay.displayed), [replay.displayed]);
  const autoAnchor = useMemo(
    () => (autoPreset !== null && mode === "candles" ? anchorTime(autoPreset, chartAnchorBars, barSeconds) : null),
    [autoPreset, mode, chartAnchorBars, barSeconds],
  );
  const calendarAnchor = useMemo(
    () => (autoAnchor?.period ? { time: autoAnchor.time, period: autoAnchor.period } : null),
    [autoAnchor],
  );
  // What the session fetch loads: the session profiles' own windows, the TPO's 30-minute bars, or the
  // calendar anchor's span at its period's bar size.
  let fetchBarSeconds = sessionCfg ? sessionBarSeconds(sessionCfg.period) : 60;
  let fetchSince = sessionSince;
  let fetchActive = sessionActive;
  if (sessionCfg?.preset === "tpo") fetchBarSeconds = TPO_BAR_SECONDS;
  if (sessionCfg?.preset === "auto") {
    fetchActive = sessionActive && calendarAnchor !== null;
    fetchSince = calendarAnchor?.time ?? 0;
    fetchBarSeconds = calendarAnchor ? sessionBarSeconds(calendarAnchor.period) : 60;
  }
  const sessionData = useSessionCandles(instrumentId, fetchActive, fetchSince, fetchBarSeconds);
  const sessionSpecs = useMemo<VolumeProfileSpec[]>(() => {
    if (!sessionCfg || !sessionActive || sessionCfg.preset === "auto") return [];
    const tpo = sessionCfg.preset === "tpo";
    const entries = buildSessionProfiles(
      trimAfter(sessionData.candles, cutoffTime),
      trimAfter(sessionData.volume, cutoffTime),
      sessionCfg.period,
      sessionCfg.settings.sessionCount,
      sessionCfg.settings,
      sessionCache,
      sessionData.completeFrom,
      tpo ? "time" : "volume",
    );
    const preset = SESSION_PRESETS[sessionCfg.preset];
    // The bar size the session was fetched at: its last bar closes this long after it opens.
    const sessionBar = tpo ? TPO_BAR_SECONDS : sessionBarSeconds(sessionCfg.period);
    return entries.flatMap((entry) => {
      const span = drawableSpan(replay.displayed, entry.startTime, entry.endTime);
      if (!span) return [];
      const detail = tpo ? tpoDetail(tpoCache, entry, sessionCfg.ibMinutes) : null;
      const balance = detail?.balance ?? null;
      // DW-152: a session spans from its first bar to the end of its elapsed period, not just the
      // chart bars that fall in it -- so a session partly before the oldest loaded bar, or held in
      // a single chart bar, keeps its true width. The edges are offsets from the drawable bars,
      // converted by the chart's bar spacing; the first bar (not the period start) keeps a coin
      // listed mid-period from spanning empty space, and the forming session ends at its newest
      // bar, never in the future.
      const extentEnd = Math.min(entry.periodEnd, entry.endTime + sessionBar);
      return [
        {
          id: `session-${entry.periodStart}`,
          profile: entry.profile,
          xAnchor: { time: span.startTime as Time, offsetSeconds: entry.startTime - span.startTime },
          width: { toTime: span.endTime as Time, offsetSeconds: extentEnd - span.endTime },
          barSeconds,
          widthFraction: SESSION_WIDTH_FRACTION,
          respondsToZoom: preset.respondsToZoom,
          upColor: sessionCfg.settings.upColor,
          downColor: sessionCfg.settings.downColor,
          showPoc: sessionCfg.settings.showPoc,
          showValueArea: sessionCfg.settings.showValueArea,
          ...(detail ? { tpo: { rows: detail.rows, letters: sessionCfg.letters } } : {}),
          ...(balance ? { initialBalance: { high: balance.high, low: balance.low } } : {}),
        },
      ];
    });
  }, [sessionCfg, sessionActive, sessionData, cutoffTime, sessionCache, tpoCache, replay.displayed, barSeconds]);

  const autoView = useMemo<{ specs: VolumeProfileSpec[]; markerTime: Time | null }>(() => {
    if (!sessionCfg || sessionCfg.preset !== "auto" || !autoAnchor) return { specs: [], markerTime: null };
    const settings = sessionCfg.settings;
    const end = Number.MAX_SAFE_INTEGER;
    let profile: VolumeProfile = EMPTY_PROFILE;
    if (calendarAnchor === null) {
      profile = buildRangeProfile(replay.displayed, volume, autoAnchor.time, end, settings);
    } else if (sessionData.completeFrom === null || autoAnchor.time >= sessionData.completeFrom) {
      // A period the fetch did not fully cover is not profiled: a truncated profile misstates it.
      profile = buildRangeProfile(
        trimAfter(sessionData.candles, cutoffTime),
        trimAfter(sessionData.volume, cutoffTime),
        autoAnchor.time,
        end,
        settings,
      );
    }
    // The span starts at the bar holding the anchor: a calendar anchor inside a coarse bar (the 1st of
    // the month inside a 1W bar) starts on that bar, not on the first bar opening after it.
    const span = drawableSpan(replay.displayed, autoAnchor.time - barSeconds + 1, end);
    if (!span) return { specs: [], markerTime: null };
    // The marker only where the chart holds the anchor's bar itself (not a later bar standing in).
    const markerTime = span.startTime <= autoAnchor.time ? (span.startTime as Time) : null;
    if (profile.rows.length === 0) return { specs: [], markerTime };
    return {
      markerTime,
      specs: [
        {
          id: "auto-anchored",
          profile,
          xAnchor: { time: span.startTime as Time },
          width: { toTime: span.endTime as Time },
          throughEndBar: true,
          widthFraction: SESSION_WIDTH_FRACTION,
          upColor: settings.upColor,
          downColor: settings.downColor,
          showPoc: settings.showPoc,
          showValueArea: settings.showValueArea,
        },
      ],
    };
  }, [sessionCfg, autoAnchor, calendarAnchor, sessionData, cutoffTime, replay.displayed, volume, barSeconds]);

  const addSessionProfile = (preset: SessionPreset): void => {
    const { defaultPeriod, rowCount } = SESSION_PRESETS[preset];
    // Switching presets keeps the user's colors/toggles/session count; only the row count
    // takes the new preset's default.
    const settings: SessionProfileSettings = {
      ...(sessionCfg?.settings ?? { ...DEFAULT_VOLUME_PROFILE_SETTINGS, sessionCount: DEFAULT_SESSION_COUNT }),
      rowCount,
    };
    const saved = layout.volume_profile;
    setSessionNowMs(Date.now());
    setSessionCfg({
      preset,
      period: defaultPeriod,
      settings,
      anchor: sessionCfg?.anchor ?? saved.anchor ?? DEFAULT_AUTO_ANCHOR,
      ibMinutes: sessionCfg?.ibMinutes ?? saved.ib_minutes ?? DEFAULT_IB_MINUTES,
      letters: sessionCfg?.letters ?? saved.letters ?? false,
    });
  };

  const changeSessionOptions = (options: Partial<Pick<SessionConfig, "anchor" | "ibMinutes" | "letters">>): void =>
    setSessionCfg((cfg) => (cfg ? { ...cfg, ...options } : cfg));

  const changeSessionPeriod = (period: SessionPeriod): void => {
    setSessionNowMs(Date.now());
    setSessionCfg((cfg) => (cfg ? { ...cfg, period } : cfg));
  };

  const changeSessionSettings = (settings: SessionProfileSettings): void =>
    setSessionCfg((cfg) => (cfg ? { ...cfg, settings } : cfg));

  // Story 32.6: a restored fixed range is profiled once the candles it needs are loaded. A range
  // outside the loaded window stays empty (drawn as nothing, removable) rather than dropped, so the
  // saved layout is not rewritten behind the operator's back, and is profiled again on every candle
  // load (a scroll-back) until it has rows.
  useEffect(() => {
    if (frvpHydratedRef.current || candles.length === 0 || fullVolume.length === 0) return;
    const filled = (f: FrvpEntry): boolean => f.profile.rows.length > 0;
    if (frvps.every(filled)) {
      frvpHydratedRef.current = true;
      return;
    }
    setFrvps((all) => {
      const next = all.map((f) =>
        filled(f) ? f : { ...f, profile: buildRangeProfile(candles, fullVolume, f.startTime, f.endTime, frvpSettings) },
      );
      // Unchanged unless a range gained rows: an empty recompute must not re-render (and re-run this).
      return next.some((f, i) => f !== all[i] && filled(f)) ? next : all;
    });
  }, [candles, fullVolume, frvpSettings, frvps]);

  // Story 32.6: the volume-profile state, reported as the layout's one profile (see the Known limit
  // at `EMPTY_PROFILE`). The saved layout's own table is the base, so an inactive kind keeps its values.
  const savedProfileRef = useRef(layout.volume_profile);
  savedProfileRef.current = layout.volume_profile;
  const firstFrvp = frvps[0];
  useEffect(() => {
    const base = savedProfileRef.current;
    let next: VolumeProfileLayout;
    if (sessionCfg) {
      next = {
        ...base,
        ...storable(sessionCfg.settings),
        kind: sessionKindOf(sessionCfg.preset),
        session: sessionCfg.period,
        hd: sessionCfg.preset === "svp-hd",
        sessions: sessionCfg.settings.sessionCount,
        anchor: sessionCfg.anchor,
        ib_minutes: sessionCfg.ibMinutes,
        letters: sessionCfg.letters,
        start: null,
        end: null,
      };
    } else if (firstFrvp) {
      next = { ...base, ...storable(frvpSettings), kind: "fixed", start: Math.round(firstFrvp.startTime), end: Math.round(firstFrvp.endTime) };
    } else if (vrvpActive) {
      next = { ...base, ...storable(vrvpSettings), kind: "visible", start: null, end: null };
    } else {
      next = { ...base, kind: "off", start: null, end: null };
    }
    patchLayout({ volume_profile: next });
    // The first range is keyed by its anchors, the part that is saved: its entry is replaced every
    // time its profile is refilled (a restored range hydrating), which changes nothing saved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionCfg, firstFrvp?.startTime, firstFrvp?.endTime, frvpSettings, vrvpActive, vrvpSettings, patchLayout]);

  // Story 32.6: dragged pane heights and the zoom, merged into the layout as the chart reports them.
  // A saved height whose indicator is no longer on the coin is dropped with the next drag, so the
  // table never outgrows the panes that can exist (`price`, `volume`, one per indicator instance).
  const handlePaneHeights = useCallback(
    (heights: Record<string, number>): void =>
      onLayout((prev) => {
        const kept = Object.entries(prev.pane_heights).filter(
          ([id]) =>
            id === "price" || id === "volume" || paneIdsRef.current.has(id) || (DERIVATIVE_PANE_IDS as readonly string[]).includes(id),
        );
        return { ...prev, pane_heights: { ...Object.fromEntries(kept), ...heights } };
      }),
    [onLayout],
  );
  const handleVisibleBars = useCallback((bars: number): void => patchLayout({ visible_bars: bars }), [patchLayout]);

  const allVolumeProfiles = useMemo<VolumeProfileSpec[]>(
    () =>
      vrvpProfile
        ? [
            ...volumeProfiles,
            {
              id: "vrvp",
              profile: vrvpProfile,
              xAnchor: "right",
              width: VRVP_WIDTH_PX,
              maxWidthFraction: VRVP_MAX_WIDTH_FRACTION,
              upColor: vrvpSettings.upColor,
              downColor: vrvpSettings.downColor,
              showPoc: vrvpSettings.showPoc,
              showValueArea: vrvpSettings.showValueArea,
            },
          ]
        : volumeProfiles,
    [volumeProfiles, vrvpProfile, vrvpSettings],
  );
  const chartVolumeProfiles = useMemo(
    () =>
      sessionSpecs.length + autoView.specs.length + anchored.profiles.length === 0
        ? allVolumeProfiles
        : [...allVolumeProfiles, ...sessionSpecs, ...autoView.specs, ...anchored.profiles],
    [allVolumeProfiles, sessionSpecs, autoView.specs, anchored.profiles],
  );

  // Stable identity: LightweightChart's measure effect must not re-subscribe mid-drag.
  const handleMeasureEnd = useCallback((): void => setActiveTool("cursor"), []);

  const selectTool = (tool: ChartTool): void => {
    // The tool's rail group shows it from now on (TradingView's last-used rule); a one-tool group
    // has nothing to remember.
    const group = groupOfTool(tool);
    if (group && group.tools.length > 1) onToolUsed(group.id, tool);
    setActiveTool(tool);
    setPlacement(null);
    replay.cancelPick();
  };

  const startReplayPick = (): void => {
    setActiveTool("cursor");
    setPlacement(null);
    replay.startPicking();
  };

  const handleDrawingColor = useCallback(
    (id: string, color: string): void => {
      // A Fibonacci has a colour per level: the menu's one colour recolours them all. The colour it
      // already has is no edit (`replaceDrawing`).
      setAllDrawings((all) =>
        replaceDrawing(all, id, (d) =>
          d.kind === "fib" || d.kind === "fib_extension"
            ? { ...d, color, levels: d.levels.map((l) => ({ ...l, color })) }
            : d.kind === "anchored_vp"
              ? { ...d, up_color: color } // a profile has no single colour: the menu's is its up colour
              : { ...d, color },
        ),
      );
    },
    [setAllDrawings],
  );

  const handleDrawingDelete = useCallback(
    (id: string): void => {
      // A deleted drawing's settings dialog closes with it: a dangling id would reopen the dialog
      // when the counter reuses it. (The list only changes through these handlers.)
      setSettingsId((open) => (open === id ? null : open));
      setAllDrawings((all) => all.filter((d) => d.id !== id));
    },
    [setAllDrawings],
  );

  // Story 33.10: every move of one handle drag is one gesture (`drag:<n>`, a fresh n per grab), so
  // the whole drag is one undo step; `nextDragGesture` keeps n unique across this component's
  // timeframe remounts.
  const dragGestureRef = useRef<string | undefined>(undefined);
  const handleDragStart = useCallback((): void => {
    dragGestureRef.current = nextDragGesture();
  }, []);
  const applyDrag = useCallback(
    (id: string, handle: string, point: DragPoint): void => {
      // Story 18.1 (AC #3) / 32.5: LightweightChart only reports the drag (it never mutates this
      // state); `applyHandleDrag` decides what the pointer means for each kind, and the updater
      // form needs no closure state, so these callbacks stay identity-stable. A drag that changes
      // nothing (a locked drawing, a refused move) returns the list itself: no edit, no undo step.
      setAllDrawings((all) => {
        const index = all.findIndex((d) => d.id === id);
        if (index === -1) return all;
        const moved = applyHandleDrag(all[index], handle, point, precisionRef.current?.price ?? null);
        return moved === all[index] ? all : all.map((d, i) => (i === index ? moved : d));
      }, dragGestureRef.current);
    },
    [setAllDrawings],
  );
  const handlePriceLineDrag = useCallback(
    (id: string, price: number): void => applyDrag(id, "price", { price, time: null, barsSince: NO_BARS }),
    [applyDrag],
  );

  const settingsDrawing = allDrawings.find((d) => d.id === settingsId);
  // Story 33.10: an undo or redo that took the dialog's drawing away closes the dialog (adjusting
  // state during render): a later redo bringing it back must not pop the dialog open again.
  if (settingsId !== null && settingsDrawing === undefined && drawingsStatus === "ready") setSettingsId(null);
  const requestSettings = useCallback((id: string): void => {
    // No position dialog without the instrument's precision (its fields are labelled and rounded by
    // it): the request is dropped, not parked to pop open when the precision arrives. Every other
    // kind's dialog prints no price, so it opens without one.
    const kind = allDrawingsRef.current.find((d) => d.id === id)?.kind;
    if (precisionRef.current === null && kind === "position") return;
    setSettingsId(id);
  }, []);

  // Story 33.10: the menu's Lock / Unlock and Hide, and the rail's Show hidden and Delete all. Each
  // is one edit (one undo step), saved like any other.
  const handleDrawingLock = useCallback(
    (id: string, locked: boolean): void =>
      setAllDrawings((all) => replaceDrawing(all, id, (d) => withFlag(d, "locked", locked))),
    [setAllDrawings],
  );
  const handleDrawingHide = useCallback(
    (id: string): void => {
      setSettingsId((open) => (open === id ? null : open));
      setAllDrawings((all) => replaceDrawing(all, id, (d) => withFlag(d, "hidden", true)));
    },
    [setAllDrawings],
  );
  const hiddenCount = useMemo(() => allDrawings.filter((d) => d.hidden === true).length, [allDrawings]);
  const showHidden = useCallback(
    (): void => setAllDrawings((all) => (all.some((d) => d.hidden) ? all.map((d) => withFlag(d, "hidden", false)) : all)),
    [setAllDrawings],
  );
  const deleteAllDrawings = (): void => {
    const count = allDrawings.length;
    if (count === 0) return;
    const ok = window.confirm(
      `Delete all ${safeDecimal(count, 0)} drawing${count === 1 ? "" : "s"} of ${instrumentId}? Ctrl+Z (Undo) brings them back while this page is open.`,
    );
    if (!ok) return;
    setSettingsId(null);
    setAllDrawings((all) => (all.length === 0 ? all : []));
  };
  const toggleDrawingsHidden = (): void => {
    // Hiding every drawing turns the drawing tools off: an armed one is disarmed with its points.
    if (!drawingsHidden) {
      setActiveTool("cursor");
      setPlacement(null);
    }
    setDrawingsHidden((hidden) => !hidden);
  };
  // A dialog's Apply with nothing changed records no undo step and sends no save (`replaceDrawing`).
  const applyDrawing = useCallback(
    (next: Drawing): void => setAllDrawings((all) => replaceDrawing(all, next.id, () => next)),
    [setAllDrawings],
  );

  const sessionRenderedCount = sessionCfg?.preset === "auto" ? autoView.specs.length : sessionSpecs.length;

  const isToolDisabled = (tool: ChartToolDef): boolean =>
    (mode === "lines" && tool.candlesOnly) ||
    (tool.placesDrawing === true && (drawingsStatus !== "ready" || drawingsHidden)) ||
    (tool.needsPrecision === true && precision === null);

  // Story 33.12: what a shortcut (`lib/shortcuts.ts`) does, each exactly what its button does, and
  // nothing where its button would be disabled.
  const runShortcut = (action: ShortcutAction): void => {
    switch (action.kind) {
      case "tool":
        if (!isToolDisabled(toolDef(action.tool))) selectTool(action.tool);
        return;
      case "replay":
        // Replay is a candle-chart feature: Alt+R in Lines mode does nothing, like the disabled button.
        if (mode === "lines") return;
        if (replay.mode === "off") startReplayPick();
        else replay.exit();
        return;
      case "log_scale":
        // A compare forces the percent scale: Normal and Log are locked (`scaleLockReason`).
        if (scaleLockReason === null) patchPriceScale({ mode: priceScale.mode === "log" ? "normal" : "log" });
        return;
      case "fullscreen":
        fullscreen.toggle();
        return;
      case "compare":
        if (mode === "candles") setCompareSearchOpen(true);
        return;
      case "search":
        setSearchOpen(true);
        return;
      case "sheet":
        setSheetOpen(true);
        return;
      case "timeframe_char":
        // The timeframe buttons are disabled in Lines mode; so is typing one.
        if (mode === "candles") setTimeframeBuffer((buffer) => (buffer + action.char).slice(-TIMEFRAME_BUFFER_MAX));
        return;
      case "timeframe_enter": {
        const seconds = timeframeFromBuffer(timeframeBuffer);
        setTimeframeBuffer("");
        // Lines mode has no timeframe: a buffer typed before the switch is dropped, never applied.
        if (mode !== "candles") return;
        if (seconds !== null && seconds !== barSeconds) onTimeframeChange(seconds);
        return;
      }
    }
  };

  return (
    <div>
      {/* Spec §A8.1 top toolbar, clusters left to right: [symbol + timeframe] [chart type]
          [indicators + fit + jump]. No theme toggle: the visual identity is fixed (15.9).
          The symbol slot is a read-only label + back link -- coins are picked on Rankings. */}
      <div className="chart-topbar" role="toolbar" aria-label="Chart controls">
        <div className="chart-cluster">
          <Link to="/">&larr; Rankings</Link>
          {/* Story 33.12: the symbol opens the symbol search (also `/` and Ctrl/Cmd+K). */}
          <h1>
            <button type="button" className="chart-symbol" aria-haspopup="dialog" title="Search markets (/ or Ctrl+K)" onClick={() => setSearchOpen(true)}>
              {instrumentId}
            </button>
          </h1>
          {venueMarket && (
            <span className="venue-badge" aria-label="Venue and market">
              {venueMarket.venue} · {venueMarket.market}
            </span>
          )}
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf.label}
              type="button"
              className={barSeconds === tf.seconds ? "tabbtn active" : "tabbtn"}
              aria-pressed={barSeconds === tf.seconds}
              aria-label={`Timeframe ${tf.label}`}
              disabled={mode === "lines"}
              onClick={() => onTimeframeChange(tf.seconds)}
            >
              {tf.label}
            </button>
          ))}
          {timeframeBuffer !== "" && (
            <span className="chart-timeframe-buffer" role="status" aria-label="Typed timeframe">
              {timeframeBuffer}
            </span>
          )}
        </div>
        <div className="chart-cluster">
        <button
          id="btn-candles"
          type="button"
          disabled={mode === "candles"}
          onClick={() => {
            setMode("candles");
            selectTool("cursor");
          }}
        >
          Candles
        </button>
        <button
          id="btn-lines"
          type="button"
          disabled={mode === "lines"}
          onClick={() => {
            setMode("lines");
            selectTool("cursor");
            replay.exit();
            setCompareSearchOpen(false);
            setTimeframeBuffer("");
          }}
        >
          Lines
        </button>
        {/* Story 33.9: how the price pane draws: the chart type, the right scale and compare symbols. */}
        <select
          aria-label="Chart type"
          value={chartType}
          disabled={mode === "lines"}
          title={mode === "lines" ? "Chart types draw candle bars: switch to Candles" : undefined}
          onChange={(e) => setChartType(e.target.value as ChartType)}
        >
          {CHART_TYPES.map((t) => (
            <option key={t} value={t}>
              {CHART_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
        <select
          aria-label="Price scale"
          value={effectiveScaleMode}
          title={scaleLockReason ?? undefined}
          onChange={(e) => patchPriceScale({ mode: e.target.value as PriceScaleModeName })}
        >
          {PRICE_SCALE_MODES.map((m) => (
            <option key={m} value={m} disabled={scaleLockReason !== null && (m === "normal" || m === "log")}>
              {PRICE_SCALE_LABELS[m]}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={priceScale.auto_scale ? "tabbtn active" : "tabbtn"}
          aria-pressed={priceScale.auto_scale}
          title="Auto-scale the price axis to the visible bars"
          onClick={() => patchPriceScale({ auto_scale: !priceScale.auto_scale })}
        >
          Auto
        </button>
        <button
          type="button"
          className={priceScale.invert ? "tabbtn active" : "tabbtn"}
          aria-pressed={priceScale.invert}
          onClick={() => patchPriceScale({ invert: !priceScale.invert })}
        >
          Invert
        </button>
        <CompareControl
          instrumentId={instrumentId}
          symbols={compare.symbols}
          disabled={mode === "lines"}
          open={compareSearchOpen}
          onOpenChange={setCompareSearchOpen}
          onAdd={addCompare}
        />
        <button
          type="button"
          className={compare.spread && spreadAvailable ? "tabbtn active" : "tabbtn"}
          aria-pressed={compare.spread && spreadAvailable}
          disabled={!spreadAvailable}
          title={spreadAvailable ? "The main close over the compare close, in bps" : "Spread needs exactly one compare symbol"}
          onClick={() => setCompare((prev) => ({ ...prev, spread: !prev.spread }))}
        >
          Spread
        </button>
        </div>
        <div className="chart-cluster">
          <button type="button" onClick={() => setIndicatorDialogOpen(true)}>
            Indicators
          </button>
          <button type="button" aria-haspopup="dialog" onClick={() => setOverlaysDialogOpen(true)}>
            Volume overlays
          </button>
          <button
            type="button"
            className={tapeOn ? "tabbtn active" : "tabbtn"}
            aria-pressed={tapeOn}
            disabled={!derivativesData.available}
            title={tapeUnavailableReason(derivativesData.spot, mode === "candles", venueMarket !== null)}
            onClick={() => setTapeOn((on) => !on)}
          >
            Liquidation tape
          </button>
          {/* Story 32.6: the coin's layout is saved as the default new coins start from, or reset to it. */}
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={layoutMenuOpen}
            onClick={() => setLayoutMenuOpen((open) => !open)}
          >
            Layout
          </button>
          {layoutMenuOpen && (
            <span role="menu" aria-label="Layout">
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setLayoutMenuOpen(false);
                  onSaveAsDefault();
                }}
              >
                Save as default
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setLayoutMenuOpen(false);
                  onResetToDefault();
                }}
              >
                Reset to default
              </button>
            </span>
          )}
          <button type="button" onClick={() => openAlertDialog(null)}>
            Alert
          </button>
          <button type="button" onClick={() => chart?.timeScale().fitContent()}>
            Fit
          </button>
          <button type="button" onClick={() => chart?.timeScale().scrollToRealTime()}>
            Latest
          </button>
          {/* Story 18.4 (AC #1): candles-only, like the tools that need the candle array. Story 33.12
              (operator, 2026-10-07): Replay steps whole bars, so it runs on every candle chart type
              and never in Lines mode, where the disabled button says why. */}
          <button
            id="btn-replay"
            type="button"
            disabled={mode === "lines" || replay.mode !== "off"}
            title={mode === "lines" ? REPLAY_LINES_REASON : "Bar Replay (Alt+R)"}
            onClick={startReplayPick}
          >
            Replay
          </button>
          <button
            type="button"
            className={fullscreen.active ? "tabbtn active" : "tabbtn"}
            aria-pressed={fullscreen.active}
            title="The chart with every pane, the tools and this bar (Shift+F; Esc leaves)"
            onClick={fullscreen.toggle}
          >
            Fullscreen
          </button>
          {fullscreen.error !== null && (
            <span role="alert" className="chart-load-error">
              {fullscreen.error}
            </span>
          )}
          <button
            type="button"
            className={watchlistOpen ? "tabbtn active" : "tabbtn"}
            aria-pressed={watchlistOpen}
            onClick={onWatchlistToggle}
          >
            Watchlist
          </button>
          <button type="button" aria-label="Keyboard shortcuts" title="Keyboard shortcuts (?)" onClick={() => setSheetOpen(true)}>
            ?
          </button>
        </div>
        {/* Story 33.12: how times print and what the price pane adds, saved in the coin's layout. */}
        <div className="chart-cluster" role="group" aria-label="Chart settings">
          <select aria-label="Time zone" value={timeZone} onChange={(e) => setTimeZone(e.target.value as TimeZoneSetting)}>
            {TIME_ZONES.map((z) => (
              <option key={z} value={z}>
                {TIME_ZONE_LABELS[z]}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={sessionBreaks ? "tabbtn active" : "tabbtn"}
            aria-pressed={sessionBreaks}
            title="A dashed line at the first bar of each UTC day (intraday bars)"
            onClick={() => setSessionBreaks((on) => !on)}
          >
            Session breaks
          </button>
          <button
            type="button"
            className={barCountdownOn ? "tabbtn active" : "tabbtn"}
            aria-pressed={barCountdownOn}
            title="Time to the bar's close under the last price (Candles mode, not during a replay)"
            onClick={() => setBarCountdownOn((on) => !on)}
          >
            Countdown
          </button>
          <button
            type="button"
            className={lastPrice.line ? "tabbtn active" : "tabbtn"}
            aria-pressed={lastPrice.line}
            onClick={() => setLastPrice((prev) => ({ ...prev, line: !prev.line }))}
          >
            Last price line
          </button>
          <button
            type="button"
            className={lastPrice.label ? "tabbtn active" : "tabbtn"}
            aria-pressed={lastPrice.label}
            onClick={() => setLastPrice((prev) => ({ ...prev, label: !prev.label }))}
          >
            Last price label
          </button>
        </div>
      </div>
      {replay.mode !== "off" && (
        <div role="group" aria-label="Replay controls">
          <ReplayControls replay={replay} onGoTo={startReplayPick} />
          <button type="button" onClick={replay.exit}>
            Exit
          </button>
        </div>
      )}
      <div className="chart-workspace">
        {/* Story 18.1 (AC #1): the left tool rail, generated from `TOOL_GROUPS` (ToolRail.tsx) --
            .tabbtn's shared visual pattern (theme.css) with the narrow-rail overrides in index.css. */}
        <ToolRail
          activeTool={activeTool}
          lastUsed={toolMemory}
          isDisabled={isToolDisabled}
          onPick={selectTool}
          crosshairOn={crosshairOn}
          onCrosshairToggle={() => setCrosshairOn((on) => !on)}
          actions={{
            magnet,
            onMagnet: () => onMagnet(nextMagnetMode(magnet)),
            canUndo: canUndo && historyActive,
            canRedo: canRedo && historyActive,
            onUndo: undoDrawings,
            onRedo: redoDrawings,
            allHidden: drawingsHidden,
            onHideAll: toggleDrawingsHidden,
            hiddenCount,
            onShowHidden: showHidden,
            deleteAllDisabled: drawingsStatus !== "ready" || allDrawings.length === 0,
            hiddenAllReason: drawingsHidden ? "Drawings are hidden (Hide all): show them first" : null,
            onDeleteAll: deleteAllDrawings,
          }}
        />
        <div className="term-box" data-label={instrumentId}>
          {loadFailed && (
            <div role="alert" className="chart-load-error">
              {loadError}
            </div>
          )}
          <LightweightChart
            mode={mode}
            data={replay.displayed}
            linesData={snapshotLines}
            onChartApi={setChart}
            panes={chartPanes}
            priceLines={priceLines}
            onPriceClick={handlePriceClick}
            drawings={drawings}
            placement={placement}
            magnet={magnet}
            onDrawingDragStart={handleDragStart}
            onDrawingLock={handleDrawingLock}
            onDrawingHide={handleDrawingHide}
            drawEditable={activeTool === "cursor" && replayMode !== "picking"}
            onDrawingColor={handleDrawingColor}
            onDrawingDelete={handleDrawingDelete}
            onDrawingSettings={requestSettings}
            onDrawingAlert={openAlertDialog}
            precision={precision}
            onDrawingDrag={applyDrag}
            fibActive={activeTool === "fib"}
            onFibPlace={handleFibPlace}
            onPointClick={handlePointClick}
            volumeProfiles={chartVolumeProfiles}
            rangeSelectActive={activeTool === "frvp"}
            profileEdgesEditable={activeTool === "cursor" && replayMode !== "picking"}
            onRangeSelect={handleRangeSelect}
            onProfileEdgeDrag={handleEdgeDrag}
            onProfileEdgeCommit={handleEdgeCommit}
            onProfileEdgeCancel={handleEdgeCancel}
            measureActive={activeTool === "measure"}
            volume={volume}
            onMeasureEnd={handleMeasureEnd}
            onPriceLineDrag={handlePriceLineDrag}
            onLegendAction={handleLegendAction}
            initialPaneHeights={initialLayout.pane_heights}
            onPaneHeights={handlePaneHeights}
            initialVisibleBars={initialLayout.visible_bars}
            onVisibleBars={handleVisibleBars}
            // Story 18.4: the real-time forming bar would reveal "future" price action.
            liveBar={replay.mode === "active" ? null : liveBar}
            liveVolumeColor={liveVolumeColor}
            markerTime={replay.markerTime}
            // DW-145: keep the replay head in view as it advances.
            followNewest={replay.mode === "active"}
            anchorMarkerTime={autoView.markerTime}
            legendExtras={anchored.legend}
            footprint={footprintSpec}
            liquidationMarkers={derivativesData.markers}
            patternMarkers={patternMarkers}
            onBarSpacing={setBarSpacing}
            chartType={chartType}
            priceScale={{ mode: effectiveScaleMode, autoScale: priceScale.auto_scale, invert: priceScale.invert }}
            onPriceScale={patchPriceScale}
            scaleModesLocked={scaleLockReason}
            timeZone={timeZone}
            sessionBreaks={sessionBreaks}
            barSeconds={barSeconds}
            countdown={countdown}
            lastPrice={lastPrice}
            fullscreen={fullscreen.active}
          />
          {mode === "candles" &&
            compare.symbols.map((iid) => (
              <CompareFeed key={iid} instrumentId={iid} chart={chart} barSeconds={barSeconds} onState={onCompareFeed} />
            ))}
        </div>
        {tapeOn && derivativesData.available && <LiquidationTape {...derivativesData.tape} timeZone={timeZone} />}
      </div>
      <VolumeOverlayNotices
        candlesMode={mode === "candles"}
        vrvp={{ active: vrvpActive, pastOldest: visibleRange?.pastOldest ?? false }}
        session={{
          active: sessionCfg,
          renderedCount: sessionRenderedCount,
          loading: sessionData.loading,
        }}
      />
      {drawingsStatus === "failed" && (
        <p role="alert" className="chart-load-error">
          Drawings could not be loaded, so the drawing tools are off (see the error bar). The load is retried every few seconds; the tools come back once it succeeds.
        </p>
      )}
      {drawingsSaveError !== null && (
        <p role="alert" className="chart-load-error">
          {drawingsSaveError}
        </p>
      )}
      {settingsDrawing && (precision || settingsDrawing.kind !== "position") && (
        <DrawingSettingsDialog
          key={settingsDrawing.id}
          drawing={settingsDrawing}
          precision={precision}
          onApply={applyDrawing}
          onRemove={() => handleDrawingDelete(settingsDrawing.id)}
          onClose={() => setSettingsId(null)}
        />
      )}
      {footprintActive && footprintData.error !== null && (
        <p role="alert" className="chart-load-error">
          Footprint: {footprintData.error}
        </p>
      )}
      {derivativeSettings !== null && (
        <DerivativeSettingsDialog
          entryKey={derivativeSettings}
          settings={derivatives}
          onApply={(patch) => setDerivatives((prev) => applyDerivativePatch(prev, derivativeSettings, patch))}
          onRemove={() => changeDerivativeOn(derivativeSettings, false)}
          onClose={() => setDerivativeSettings(null)}
        />
      )}
      {volumeDialogOpen && (
        <VolumeSettingsDialog
          colorBy={volumeColorBy}
          onApply={setVolumeColorBy}
          onRemove={() => changeVolumeOn(false)}
          onClose={() => setVolumeDialogOpen(false)}
        />
      )}
      {footprintDialogOpen && (
        <FootprintSettingsDialog
          settings={footprint}
          onApply={setFootprint}
          onRemove={() => changeFootprintOn(false)}
          onClose={() => setFootprintDialogOpen(false)}
        />
      )}
      {Object.entries(indicatorErrors).map(([id, message]) => (
        <p key={id} role="alert" className="chart-load-error">
          Indicator {id} failed: {message}
          {/* A failed instance draws no series, so it has no legend row: its settings and remove
              live here, or it could not be fixed or removed from the chart page. */}
          {entriesById.has(id) && (
            <>
              {" "}
              <button type="button" aria-label={`Settings for ${id}`} onClick={() => pickerRef.current?.openSettings(id)}>
                Settings
              </button>{" "}
              <button type="button" aria-label={`Remove ${id}`} onClick={() => pickerRef.current?.remove(id)}>
                Remove
              </button>
            </>
          )}
        </p>
      ))}
      {overlaysDialogOpen && (
        <VolumeOverlaysDialog
          candlesMode={mode === "candles"}
          timeZone={timeZone}
          onClose={() => setOverlaysDialogOpen(false)}
          vrvp={{
            active: vrvpActive,
            settings: vrvpSettings,
            pastOldest: visibleRange?.pastOldest ?? false,
            onAdd: () => setVrvpActive(true),
            onRemove: () => setVrvpActive(false),
            onSettingsChange: setVrvpSettings,
          }}
          session={{
            active: sessionCfg,
            renderedCount: sessionRenderedCount,
            loading: sessionData.loading,
            onAdd: addSessionProfile,
            onRemove: () => setSessionCfg(null),
            onPeriodChange: changeSessionPeriod,
            onOptionsChange: changeSessionOptions,
            onSettingsChange: changeSessionSettings,
          }}
          // DW-150: the FRVP settings show as soon as the tool is armed, so the first range is placed
          // with them already set.
          frvp={{
            ranges: frvps,
            settings: frvpSettings,
            armed: activeTool === "frvp",
            drawDisabled: isToolDisabled(toolDef("frvp")),
            onDraw: () => selectTool("frvp"),
            onRemove: (id) => setFrvps((all) => all.filter((x) => x.id !== id)),
            onSettingsChange: handleFrvpSettings,
          }}
        />
      )}
      {searchOpen && (
        <SymbolSearch
          mode="navigate"
          instrumentId={instrumentId}
          onPick={(iid) => navigate(`/chart/${encodeURIComponent(iid)}`)}
          onClose={() => setSearchOpen(false)}
        />
      )}
      {sheetOpen && <ShortcutSheet onClose={() => setSheetOpen(false)} />}
      <AlertDialog
        open={alertDialogOpen}
        onClose={() => setAlertDialogOpen(false)}
        instrumentId={instrumentId}
        barSeconds={barSeconds}
        priceLines={priceLines}
        initialCondition={alertCondition}
        saveDrawings={saveDrawingsNow}
      />
      <IndicatorPicker
        ref={pickerRef}
        showEntryList={false}
        titleFor={legendTitle}
        outputsFor={settingsOutputs}
        fetchConfig={() => fetchCoinIndicatorConfig(instrumentId)}
        saveConfig={(entries) => saveCoinIndicatorConfig(instrumentId, entries)}
        reloadKey={instrumentId}
        onEntriesChange={setPickerEntries}
        dialogOpen={indicatorDialogOpen}
        onDialogClose={() => setIndicatorDialogOpen(false)}
        multiInstance
        volumeOn={volumeOn}
        onVolumeChange={changeVolumeOn}
        footprintOn={footprint.on}
        onFootprintChange={changeFootprintOn}
        footprintCandlesOnly={mode !== "candles"}
        derivatives={derivativeOnStates(derivatives)}
        onDerivativeChange={changeDerivativeOn}
        derivativesDisabled={derivativesData.spot}
        derivativesCandlesOnly={mode !== "candles"}
      />
    </div>
  );
}

export default function ChartPage() {
  const { iid } = useParams<{ iid: string }>();
  // The tool each rail group last armed (TradingView's rule: a group's button shows the tool used
  // last). Held here, above the per-coin and per-timeframe remounts, so it follows the operator from
  // coin to coin. Known limit: not persisted, so a page reload shows each group's first tool again.
  // Upgrade path: a per-viewer UI preference beside the coin layout (not the layout table itself,
  // whose shape is the server's).
  const [toolMemory, setToolMemory] = useState<Partial<Record<string, ChartTool>>>({});
  const rememberTool = useCallback(
    (groupId: string, tool: ChartTool): void =>
      setToolMemory((prev) => (prev[groupId] === tool ? prev : { ...prev, [groupId]: tool })),
    [],
  );
  // Story 33.10: the drawing magnet follows the operator from coin to coin like the tool memory. Not
  // persisted (the spec's choice): a reload starts with it off.
  const [magnet, setMagnet] = useState<MagnetMode>("off");
  // Story 33.12: the watchlist rail, beside the per-coin chart rather than inside it, so it stays open
  // (and keeps its list and socket) when a row opens another coin. View state, not persisted.
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const toggleWatchlist = useCallback((): void => setWatchlistOpen((open) => !open), []);
  if (!iid) return <p>No instrument specified.</p>;
  return (
    <div className="chart-page">
      <ChartForCoin
        key={iid}
        instrumentId={iid}
        toolMemory={toolMemory}
        onToolUsed={rememberTool}
        magnet={magnet}
        onMagnet={setMagnet}
        watchlistOpen={watchlistOpen}
        onWatchlistToggle={toggleWatchlist}
      />
      {watchlistOpen && <WatchlistRail instrumentId={iid} />}
    </div>
  );
}

function ChartForCoin({
  instrumentId,
  toolMemory,
  onToolUsed,
  magnet,
  onMagnet,
  watchlistOpen,
  onWatchlistToggle,
}: {
  instrumentId: string;
  toolMemory: Partial<Record<string, ChartTool>>;
  onToolUsed: (groupId: string, tool: ChartTool) => void;
  magnet: MagnetMode;
  onMagnet: (mode: MagnetMode) => void;
  watchlistOpen: boolean;
  onWatchlistToggle: () => void;
}) {
  // Story 33.12: the one fullscreen element, `.chart-stage`: the top bar (so the button that leaves
  // stays on screen), the replay controls, the tool rail, the chart with every pane and its legends,
  // and the Liquidation tape. Held here, above the per-timeframe remount of ChartInner, so a timeframe
  // change (a click or a typed one) keeps fullscreen. The watchlist rail stays outside it.
  const stageRef = useRef<HTMLDivElement | null>(null);
  const fullscreen = useFullscreen(stageRef);
  // Story 32.6: the coin's saved layout (timeframe, volume, mode, crosshair, pane heights, zoom, volume
  // profile) is loaded BEFORE the chart mounts, so its first candle request already uses the saved
  // timeframe. Held here, not in ChartInner (remounted on every timeframe change).
  const layoutStore = useChartLayout(instrumentId);
  const { layout, update: updateLayout } = layoutStore;

  // Held here for the same reason: the drawings, so they stay in place across the remount instead of
  // reloading, and the instrument's decimals from the first candles response, so a remounted chart
  // labels from its first paint.
  const drawingStore = useChartDrawings(instrumentId);
  const [precision, setPrecision] = useState<InstrumentPrecision | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const changeVolume = useCallback(
    (on: boolean): void => updateLayout((prev) => ({ ...prev, volume: on })),
    [updateLayout],
  );
  const changeTimeframe = useCallback(
    (seconds: number): void => updateLayout((prev) => ({ ...prev, bar_seconds: seconds })),
    [updateLayout],
  );

  const { saveAsDefault, resetToDefault } = layoutStore;
  const handleSaveAsDefault = useCallback((): void => {
    // The confirm names what is overwritten: every coin opened for the first time starts from it.
    const ok = window.confirm(
      `Save ${instrumentId}'s layout and indicators as the default? This overwrites the current default that every coin opened for the first time starts from. Drawings are not part of it.`,
    );
    if (!ok) return;
    setActionError(null);
    saveAsDefault().catch((err: unknown) => {
      console.error(`ChartPage: save as default failed for ${instrumentId}`, err);
      setActionError("The layout could not be saved as the default (see the error bar).");
    });
  }, [instrumentId, saveAsDefault]);
  const handleResetToDefault = useCallback((): void => {
    const ok = window.confirm(
      `Reset ${instrumentId} to the default layout? This replaces its layout and indicators with the default. Its drawings are kept.`,
    );
    if (!ok) return;
    setActionError(null);
    resetToDefault().catch((err: unknown) => {
      console.error(`ChartPage: reset to default failed for ${instrumentId}`, err);
      setActionError("The layout could not be reset to the default (see the error bar).");
    });
  }, [instrumentId, resetToDefault]);

  if (layout === null) {
    return (
      <div className="chart-stage" ref={stageRef}>
        {layoutStore.status === "failed" ? (
          <p role="alert" className="chart-load-error">
            The layout of {instrumentId} could not be loaded, so the chart is not drawn (see the error bar). A server or network failure is retried every few seconds.
          </p>
        ) : (
          <p>Loading the layout of {instrumentId}...</p>
        )}
      </div>
    );
  }

  // Keyed by instrument, bar size AND reset: a fresh LightweightChart + useCandles set per
  // (coin, timeframe), rather than trying to re-point one long-lived chart instance (see
  // LightweightChart's own docstring -- lightweight-charts has no supported API for that).
  return (
    <div className="chart-stage" ref={stageRef}>
      <ChartInner
        // `revision` is bumped by Reset to default in the same render as the reset layout: ChartInner
        // remounts once and re-reads every field (and its picker refetches the reset indicator list).
        key={`${instrumentId}:${layout.bar_seconds}:${layoutStore.revision}`}
        instrumentId={instrumentId}
        drawingStore={drawingStore}
        heldPrecision={precision}
        onPrecision={setPrecision}
        barSeconds={layout.bar_seconds}
        volumeOn={layout.volume}
        onVolumeChange={changeVolume}
        onTimeframeChange={changeTimeframe}
        layout={layout}
        onLayout={updateLayout}
        onSaveAsDefault={handleSaveAsDefault}
        onResetToDefault={handleResetToDefault}
        toolMemory={toolMemory}
        onToolUsed={onToolUsed}
        magnet={magnet}
        onMagnet={onMagnet}
        fullscreen={fullscreen}
        watchlistOpen={watchlistOpen}
        onWatchlistToggle={onWatchlistToggle}
      />
      {layoutStore.saveError !== null && (
        <p role="alert" className="chart-load-error">
          {layoutStore.saveError}
        </p>
      )}
      {actionError !== null && (
        <p role="alert" className="chart-load-error">
          {actionError}
        </p>
      )}
    </div>
  );
}
