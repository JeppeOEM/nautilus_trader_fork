import { CrosshairMode, type IChartApi, type Time } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { fetchCoinIndicatorConfig, fetchIndicatorCatalog, saveCoinIndicatorConfig } from "../api/client";
import AlertDialog from "../components/chart/AlertDialog";
import IndicatorPicker, { type IndicatorPickerHandle } from "../components/chart/IndicatorPicker";
import type { SettingsOutput } from "../components/chart/IndicatorSettingsDialog";
import type { LegendAction } from "../components/chart/legend";
import LightweightChart, {
  type ChartMode,
  type DrawingSpec,
  type LegendExtra,
  type VolumeProfileSpec,
  type IndicatorPaneSpec,
  type PriceLineSpec,
} from "../components/chart/LightweightChart";
import type { TrendlineAnchor } from "../components/chart/primitives/TrendlinePrimitive";
import SessionProfileControl from "../components/chart/SessionProfileControl";
import VrvpControl from "../components/chart/VrvpControl";
import VolumeProfileSettingsPanel from "../components/chart/VolumeProfileSettings";
import {
  DEFAULT_VOLUME_PROFILE_SETTINGS,
  buildRangeProfile,
  type VolumeProfile,
} from "../lib/volumeProfile";
import { type AutoAnchorPreset, DEFAULT_AUTO_ANCHOR, anchorBars, anchorTime } from "../lib/autoAnchor";
import { anchoredVwap, breakAtGaps } from "../lib/anchoredVwap";
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
import { chartVar, fibLevelColor } from "../components/chart/chartTheme";
import DrawingSettingsDialog from "../components/chart/DrawingSettingsDialog";
import {
  type Drawing,
  type DragPoint,
  type InstrumentPrecision,
  type PositionSide,
  applyHandleDrag,
  defaultFibLevels,
  nextDrawingId,
  newAnchoredVp,
  newAnchoredVwap,
  newPosition,
  safeDecimal,
  snapIndex,
  storedTime,
} from "../lib/drawings";
import { roundToPrecision } from "../lib/units";
import { type ChartDrawings, useChartDrawings } from "../hooks/useChartDrawings";
import { DEFAULT_SOURCE, entryId, splitSeriesKey } from "../lib/indicatorId";
import { outputStyle } from "../lib/indicatorStyle";
import { assignPaneColor } from "../components/chart/paneColors";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../api/schema";
import { useCandles } from "../hooks/useCandles";
import { TIMEFRAMES } from "../timeframes";
import { type ChartLayout, type VolumeProfileLayout } from "../lib/chartLayout";
import { useChartLayout } from "../hooks/useChartLayout";
import { useReplay } from "../hooks/useReplay";
import { useSessionCandles } from "../hooks/useSessionCandles";
import { useVisibleRange } from "../hooks/useVisibleRange";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
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

function profileSettings(vp: VolumeProfileLayout): typeof DEFAULT_VOLUME_PROFILE_SETTINGS {
  return { ...DEFAULT_VOLUME_PROFILE_SETTINGS, rowCount: vp.rows, valueAreaPercent: vp.value_area_pct };
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
  const settings = { ...profileSettings(vp), sessionCount: DEFAULT_SESSION_COUNT };
  return {
    preset,
    period,
    settings,
    sinceSeconds: sessionSince(period, settings.sessionCount),
    anchor: vp.anchor,
    ibMinutes: vp.ib_minutes,
    letters: vp.letters,
  };
}

/** The layout kind a session-slot preset is saved as. */
function sessionKindOf(preset: SessionPreset): VolumeProfileLayout["kind"] {
  return preset === "auto" ? "auto" : preset === "tpo" ? "tpo" : "session";
}

/** The row count and value-area percent as the resource stores them (the inputs allow more). */
function storable(settings: { rowCount: number; valueAreaPercent: number }): { rows: number; value_area_pct: number } {
  return {
    rows: Math.min(500, Math.max(2, Math.round(settings.rowCount))),
    value_area_pct: Math.min(100, Math.max(1, settings.valueAreaPercent)),
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
// RSI(14) and RSI(21) are two titles.
function legendTitle(entry: IndicatorConfigEntry): string {
  const parts: unknown[] = Object.values(entry.params ?? {});
  if (entry.source && entry.source !== DEFAULT_SOURCE) parts.push(entry.source);
  return parts.length ? `${entry.name} (${parts.join(", ")})` : entry.name;
}

// Story 18.1 (AC #1): the chart's drawing-tool state -- "cursor" is the inert default.
// Stories 18.2/18.3 extend this union with their tools, never a second state variable.
export type ChartTool =
  | "cursor"
  | "hline"
  | "trendline"
  | "fib"
  | "long"
  | "short"
  | "measure"
  | "frvp"
  | "avp"
  | "avwap";

interface ChartToolDef {
  id: ChartTool;
  label: string;
  ariaLabel: string;
  /** Hover tooltip, where the label alone does not say what the button does. */
  title?: string;
  /** No meaning in Lines mode (no single main series to attach to -- spec Task 2's
   * MVP scope decision): disables the button there and disarms an armed tool (see the
   * mode-guard effect in ChartInner). */
  candlesOnly: boolean;
  /** Places a drawing that is saved with the coin: off until the coin's drawings have loaded (a
   * placement before then would be overwritten by the load, or overwrite the server's list). */
  placesDrawing?: boolean;
  /** Labels its prices at the instrument's precision: off until the first candles response has
   * carried it, never at a guessed one. */
  needsPrecision?: boolean;
}

// The left tool rail's tools, as data -- Stories 18.2/18.3 append entries here and
// the toolbar markup below never changes shape.
// Story 18.10 (spec §A8.1): two clusters, top to bottom -- [cursor, crosshair toggle]
// then the drawing tools [line, horizontal line, measurement, + the FRVP profile tool].
// The crosshair toggle is not an exclusive tool (it is a view option), so it is rendered
// between the clusters rather than living in this list.
const SELECT_TOOLS: readonly ChartToolDef[] = [
  {
    id: "cursor",
    label: "Cursor",
    ariaLabel: "Cursor tool",
    // The disarm, and the one mode in which drawings can be edited (Story 32.3).
    title: "Select / edit drawings (Esc)",
    candlesOnly: false,
  },
];
const DRAWING_TOOLS: readonly ChartToolDef[] = [
  { id: "trendline", label: "Trend", ariaLabel: "Trendline tool", candlesOnly: false, placesDrawing: true },
  { id: "hline", label: "HLine", ariaLabel: "Horizontal line tool", candlesOnly: true, placesDrawing: true },
  {
    id: "fib",
    label: "Fib",
    ariaLabel: "Fibonacci retracement tool",
    title: "Fibonacci retracement: drag from anchor A to anchor B",
    candlesOnly: false,
    placesDrawing: true,
    needsPrecision: true,
  },
  {
    id: "long",
    label: "Long",
    ariaLabel: "Long position tool",
    title: "Long position: click the entry price",
    candlesOnly: false,
    placesDrawing: true,
    needsPrecision: true,
  },
  {
    id: "short",
    label: "Short",
    ariaLabel: "Short position tool",
    title: "Short position: click the entry price",
    candlesOnly: false,
    placesDrawing: true,
    needsPrecision: true,
  },
  { id: "measure", label: "Measure", ariaLabel: "Measurement tool", candlesOnly: true },
  { id: "frvp", label: "FRVP", ariaLabel: "Fixed range volume profile tool", candlesOnly: true },
  // Story 32.7: single-click drawings, saved with the coin's drawings; both read the candle bars.
  {
    id: "avp",
    label: "AVP",
    ariaLabel: "Anchored volume profile tool",
    title: "Anchored volume profile: click the bar it starts at",
    candlesOnly: true,
    placesDrawing: true,
  },
  {
    id: "avwap",
    label: "AVWAP",
    ariaLabel: "Anchored VWAP tool",
    title: "Anchored VWAP: click the bar it starts at",
    candlesOnly: true,
    placesDrawing: true,
    needsPrecision: true,
  },
];

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

interface SessionConfig {
  preset: SessionPreset;
  period: SessionPeriod;
  settings: SessionProfileSettings;
  sinceSeconds: number;
  /** Story 32.7: the Auto Anchored preset, the TPO's initial balance (minutes) and letters switch;
   * kept whichever preset is on, so switching back restores them. */
  anchor: AutoAnchorPreset;
  ibMinutes: number;
  letters: boolean;
}

const sessionSince = (period: SessionPeriod, count: number): number =>
  periodStartBack(Math.floor(Date.now() / 1000), period, count - 1);

// Story 18.8: each session's longest bar spans this fraction of the session's width.
const SESSION_WIDTH_FRACTION = 0.7;

// Story 18.7: the longest VRVP bar, growing leftward from the price axis.
const VRVP_WIDTH_PX = 150;

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
const NONE: never[] = [];

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
  const { drawings: allDrawings, setDrawings: setAllDrawings, status: drawingsStatus, saveError: drawingsSaveError } = drawingStore;
  const [settingsId, setSettingsId] = useState<string | null>(null);
  const priceLines = useMemo<PriceLineSpec[]>(
    () =>
      allDrawings.flatMap((d) =>
        d.kind === "hline" ? [{ id: d.id, price: d.price, color: d.color ?? chartVar("--chart-drawing") }] : [],
      ),
    [allDrawings],
  );
  const [crosshairOn, setCrosshairOn] = useState(initialLayout.crosshair);
  useEffect(() => patchLayout({ crosshair: crosshairOn }), [crosshairOn, patchLayout]);
  const [indicatorDialogOpen, setIndicatorDialogOpen] = useState(false);
  const [alertDialogOpen, setAlertDialogOpen] = useState(false);
  const [catalog, setCatalog] = useState<Record<string, IndicatorCatalogEntry>>({});
  // Story 18.2: the trendline's first click, held until the second click completes it
  // (or Esc / a tool change discards it); `drawings` is the placed set.
  const [pendingAnchor, setPendingAnchor] = useState<TrendlineAnchor | null>(null);
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
  // Story 18.8: the single session-profile slot (SVP / SVP HD presets). `sinceSeconds` is
  // fixed when the config is set (an event handler), so render stays pure.
  const [sessionCfg, setSessionCfg] = useState<SessionConfig | null>(() => initialSessionConfig(savedProfile));
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
  const liveBar = useLiveCandle(instrumentId, barSeconds, { onReconnect: refreshNewest, onBarClosed: appendBar });
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
  const anchored = useMemo(() => {
    // A coin with no anchored drawing (nearly every one) skips the per-tick copy of its bars.
    if (mode !== "candles" || !allDrawings.some((d) => d.kind === "anchored_vp" || d.kind === "anchored_vwap")) {
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
    for (const d of allDrawings) {
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
      const points = anchorBar === null ? [] : breakAtGaps(anchoredVwap(bars, anchorBar, d.source), chartBars);
      // The snapped bar, like the Anchored VP: the anchor handle sits on the bar the line starts from.
      specs.push({ ...d, time: anchorBar ?? d.time, points });
      const latest = points.at(-1);
      const color = d.color ?? chartVar("--chart-drawing");
      legend.push({
        id: `avwap-${d.id}`,
        label: `AVWAP (${d.source})`,
        color,
        value: latest === undefined || precision === null ? null : latest.vwap,
        format: (value) => (precision === null ? String(value) : safeDecimal(value, precision.price)),
      });
    }
    // Shared empty arrays: a coin with none (nearly every one) must not hand the chart a fresh
    // array, hence a "changed" prop, on every bar.
    return { specs: specs.length > 0 ? specs : NONE, profiles: profiles.length > 0 ? profiles : NONE, legend: legend.length > 0 ? legend : NONE };
  }, [allDrawings, mode, replay.displayed, volume, anchoredLive, precision]);
  const plainDrawings = useMemo<DrawingSpec[]>(
    () =>
      allDrawings.flatMap((d): DrawingSpec[] => {
        if (d.kind === "hline" || d.kind === "anchored_vp" || d.kind === "anchored_vwap") return [];
        return [d.kind === "trendline" ? { ...d, color: d.color ?? chartVar("--chart-drawing") } : d];
      }),
    [allDrawings],
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
              data: volume,
              color: assignPaneColor("volume", DEFAULT_PANE_IDS),
              groupLabel: "Volume",
              hidden: volumeHidden,
              configurable: false,
            },
          ]
        : []),
      ...pickerSeriesKeys.map((key) => {
        const panel = panelForKey(key, catalog);
        const { id: instanceId, output } = splitSeriesKey(key);
        const entry = entriesById.get(instanceId);
        const name = entry?.name ?? catalogNameForKey(key, catalog) ?? key;
        const style = outputStyle(entry, output);
        return {
          id: key,
          // One legend row, one pane per instance (RSI(14) and RSI(21) are two), even for stale
          // values no entry owns any more -- those get no buttons (the picker finds no entry).
          group: instanceId,
          groupLabel: entry ? legendTitle(entry) : name,
          outputLabel: output,
          // Known limit: pattern hits are ±100 histogram spikes, not on-candle markers. Upgrade
          // path: lightweight-charts `createSeriesMarkers`. (Story 27.7's `CandlePattern` is a
          // "histogram" catalog entry like any other: no special case here.)
          kind: panel === "histogram" ? ("Histogram" as const) : ("Line" as const),
          data: trimAfter(pickerValues[key], cutoffTime),
          // Combined with DEFAULT_PANE_IDS so a picker series never lands on volume's slot. A
          // colour the entry stores wins over the palette slot.
          color: style.color ?? assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
          placement: panel === "overlay" ? ("overlay" as const) : ("pane" as const),
          hidden: entry?.hidden === true,
          actionable: entry !== undefined,
          lineWidth: style.line_width,
          lineStyle: style.line_style,
          upColor: style.up_color,
          downColor: style.down_color,
        };
      }),
    ],
    [volumeOn, volumeHidden, volume, pickerSeriesKeys, pickerValues, catalog, entriesById, cutoffTime],
  );

  // The legend's eye / gear / x. Picker indicators go through the picker's own persist path (the
  // same one an add uses); Volume is page state (eye) and the Indicators dialog's toggle (x).
  const pickerRef = useRef<IndicatorPickerHandle>(null);
  const handleLegendAction = useCallback(
    (action: LegendAction, group: string): void => {
      if (group === "volume") {
        if (action === "hide") setVolumeHidden((h) => !h);
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
    [changeVolumeOn],
  );
  // Seeded with what the chart draws while the entry stores nothing: the palette colour, which a
  // histogram also paints both signs with (and the side whose colour is not set keeps).
  const settingsOutputs = (id: string): SettingsOutput[] =>
    pickerSeriesKeys
      .filter((key) => splitSeriesKey(key).id === id)
      .map((key) => {
        const palette = assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]);
        return {
          label: splitSeriesKey(key).output,
          kind: panelForKey(key, catalog) === "histogram" ? "Histogram" : "Line",
          defaultColor: palette,
          defaultUpColor: palette,
          defaultDownColor: palette,
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
        setPendingAnchor(null);
        cancelReplayPick();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [cancelReplayPick]);

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

  const handlePointClick = useCallback(
    (point: TrendlineAnchor): void => {
      // Story 18.2 (AC #2/#5): first click stores the start anchor, the second completes
      // the line and disarms the tool. A second click on the exact same point would make
      // an invisible zero-length line, so it is ignored (the tool stays armed).
      // Story 18.4 (AC #2): while picking a replay start, a click selects that bar and
      // nothing else -- drawing tools are disarmed on entry, this guards the same click.
      if (replayMode === "picking") {
        pickReplayBar(point.time as number);
        return;
      }
      if (activeTool === "long" || activeTool === "short") {
        // Story 32.5: one click places a position at the clicked price on the clicked bar. Its
        // prices sit on the instrument's grid, so the tool is off until that precision is known.
        const places = precisionRef.current?.price;
        if (places === undefined) return;
        const side: PositionSide = activeTool;
        setAllDrawings((all) => [
          ...all,
          newPosition(nextDrawingId(all, "position"), side, point.time as number, point.price, places),
        ]);
        setActiveTool("cursor");
        return;
      }
      if (activeTool === "avp" || activeTool === "avwap") {
        // Story 32.7: one click at a bar places an anchored drawing; its colours are chart tokens.
        const time = point.time as number;
        setAllDrawings((all) => [
          ...all,
          activeTool === "avp"
            ? newAnchoredVp(nextDrawingId(all, "anchored_vp"), time, chartVar("--chart-up"), chartVar("--chart-down"))
            : newAnchoredVwap(nextDrawingId(all, "anchored_vwap"), time, chartVar("--chart-drawing"), chartVar("--chart-pane-4")),
        ]);
        setActiveTool("cursor");
        return;
      }
      if (activeTool !== "trendline") return;
      if (!pendingAnchor) {
        setPendingAnchor(point);
        return;
      }
      if (pendingAnchor.time === point.time && pendingAnchor.price === point.price) return;
      setAllDrawings((all) => [
        ...all,
        {
          kind: "trendline",
          id: nextDrawingId(all, "trendline"),
          anchors: [
            { time: storedTime(pendingAnchor.time as number), price: roundPrice(pendingAnchor.price) },
            { time: storedTime(point.time as number), price: roundPrice(point.price) },
          ],
          color: chartVar("--chart-drawing"),
        },
      ]);
      setPendingAnchor(null);
      setActiveTool("cursor");
    },
    [activeTool, pendingAnchor, replayMode, pickReplayBar, roundPrice, setAllDrawings],
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
  const visibleRange = useVisibleRange(vrvpActive && mode === "candles" ? chart : null);
  const vrvpProfile = useMemo(
    () =>
      vrvpActive && mode === "candles" && visibleRange
        ? buildRangeProfile(replay.displayed, volume, visibleRange.from, visibleRange.to, vrvpSettings)
        : null,
    [vrvpActive, mode, visibleRange, replay.displayed, volume, vrvpSettings],
  );

  // Story 18.8: one independent profile per session, from its own finest-timeframe fetch;
  // only the newest session is rebuilt as bars arrive (buildSessionProfiles' cache).
  const sessionActive = sessionCfg !== null && mode === "candles";
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
  let fetchSince = sessionCfg?.sinceSeconds ?? 0;
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
    return entries.flatMap((entry) => {
      const span = drawableSpan(replay.displayed, entry.startTime, entry.endTime);
      if (!span) return [];
      const detail = tpo ? tpoDetail(tpoCache, entry, sessionCfg.ibMinutes) : null;
      const balance = detail?.balance ?? null;
      return [
        {
          id: `session-${entry.periodStart}`,
          profile: entry.profile,
          xAnchor: { time: span.startTime as Time },
          width: { toTime: span.endTime as Time },
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
  }, [sessionCfg, sessionActive, sessionData, cutoffTime, sessionCache, tpoCache, replay.displayed]);

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
    const { period, rowCount } = SESSION_PRESETS[preset];
    // Switching presets keeps the user's colors/toggles/session count; only the row count
    // takes the new preset's default.
    const settings: SessionProfileSettings = {
      ...(sessionCfg?.settings ?? { ...DEFAULT_VOLUME_PROFILE_SETTINGS, sessionCount: DEFAULT_SESSION_COUNT }),
      rowCount,
    };
    const saved = layout.volume_profile;
    setSessionCfg({
      preset,
      period,
      settings,
      sinceSeconds: sessionSince(period, settings.sessionCount),
      anchor: sessionCfg?.anchor ?? saved.anchor ?? DEFAULT_AUTO_ANCHOR,
      ibMinutes: sessionCfg?.ibMinutes ?? saved.ib_minutes ?? DEFAULT_IB_MINUTES,
      letters: sessionCfg?.letters ?? saved.letters ?? false,
    });
  };

  const changeSessionOptions = (options: Partial<Pick<SessionConfig, "anchor" | "ibMinutes" | "letters">>): void =>
    setSessionCfg((cfg) => (cfg ? { ...cfg, ...options } : cfg));

  const changeSessionPeriod = (period: SessionPeriod): void =>
    setSessionCfg((cfg) =>
      cfg ? { ...cfg, period, sinceSeconds: sessionSince(period, cfg.settings.sessionCount) } : cfg,
    );

  const changeSessionSettings = (settings: SessionProfileSettings): void =>
    setSessionCfg((cfg) =>
      cfg ? { ...cfg, settings, sinceSeconds: sessionSince(cfg.period, settings.sessionCount) } : cfg,
    );

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
  const sessionKey = sessionCfg
    ? [
        sessionCfg.preset,
        sessionCfg.period,
        sessionCfg.settings.rowCount,
        sessionCfg.settings.valueAreaPercent,
        sessionCfg.anchor,
        sessionCfg.ibMinutes,
        sessionCfg.letters,
      ].join("|")
    : "";
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
    // `sessionCfg` is keyed by what is saved from it: its `sinceSeconds` changes on every period tick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey, firstFrvp?.startTime, firstFrvp?.endTime, frvpSettings, vrvpActive, vrvpSettings, patchLayout]);

  // Story 32.6: dragged pane heights and the zoom, merged into the layout as the chart reports them.
  // A saved height whose indicator is no longer on the coin is dropped with the next drag, so the
  // table never outgrows the panes that can exist (`price`, `volume`, one per indicator instance).
  const handlePaneHeights = useCallback(
    (heights: Record<string, number>): void =>
      onLayout((prev) => {
        const kept = Object.entries(prev.pane_heights).filter(
          ([id]) => id === "price" || id === "volume" || paneIdsRef.current.has(id),
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
    setActiveTool(tool);
    setPendingAnchor(null);
    replay.cancelPick();
  };

  const startReplayPick = (): void => {
    setActiveTool("cursor");
    setPendingAnchor(null);
    replay.startPicking();
  };

  const handleDrawingColor = useCallback(
    (id: string, color: string): void => {
      // A Fibonacci has a colour per level: the menu's one colour recolours them all.
      setAllDrawings((all) =>
        all.map((d) =>
          d.id !== id
            ? d
            : d.kind === "fib"
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

  const applyDrag = useCallback(
    (id: string, handle: string, point: DragPoint): void => {
      // Story 18.1 (AC #3) / 32.5: LightweightChart only reports the drag (it never mutates this
      // state); `applyHandleDrag` decides what the pointer means for each kind, and the updater
      // form needs no closure state, so these callbacks stay identity-stable.
      setAllDrawings((all) =>
        all.map((d) => (d.id === id ? applyHandleDrag(d, handle, point, precisionRef.current?.price ?? null) : d)),
      );
    },
    [setAllDrawings],
  );
  const handlePriceLineDrag = useCallback(
    (id: string, price: number): void => applyDrag(id, "price", { price, time: null, barsSince: NO_BARS }),
    [applyDrag],
  );

  const settingsDrawing = allDrawings.find((d) => d.id === settingsId);
  const allDrawingsRef = useRef(allDrawings);
  useEffect(() => {
    allDrawingsRef.current = allDrawings;
  }, [allDrawings]);
  const requestSettings = useCallback((id: string): void => {
    // No dialog without the instrument's precision (its fields are labelled and rounded by it):
    // the request is dropped, not parked to pop open when the precision arrives. The Anchored VP's
    // and VWAP's dialogs print no price, so they open without one.
    const kind = allDrawingsRef.current.find((d) => d.id === id)?.kind;
    if (precisionRef.current === null && kind !== "anchored_vp" && kind !== "anchored_vwap") return;
    setSettingsId(id);
  }, []);
  const applyDrawing = useCallback(
    (next: Drawing): void => setAllDrawings((all) => all.map((d) => (d.id === next.id ? next : d))),
    [setAllDrawings],
  );

  const renderTool = (tool: ChartToolDef) => (
    <button
      key={tool.id}
      type="button"
      className={activeTool === tool.id ? "tabbtn active" : "tabbtn"}
      aria-pressed={activeTool === tool.id}
      aria-label={tool.ariaLabel}
      data-tool={tool.id}
      title={tool.title}
      disabled={
        (mode === "lines" && tool.candlesOnly) ||
        (tool.placesDrawing === true && drawingsStatus !== "ready") ||
        (tool.needsPrecision === true && precision === null)
      }
      onClick={() => selectTool(tool.id)}
    >
      {tool.label}
    </button>
  );

  return (
    <div>
      {/* Spec §A8.1 top toolbar, clusters left to right: [symbol + timeframe] [chart type]
          [indicators + fit + jump]. No theme toggle: the visual identity is fixed (15.9).
          The symbol slot is a read-only label + back link -- coins are picked on Rankings. */}
      <div className="chart-topbar" role="toolbar" aria-label="Chart controls">
        <div className="chart-cluster">
          <Link to="/">&larr; Rankings</Link>
          <h1>{instrumentId}</h1>
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
          }}
        >
          Lines
        </button>
        </div>
        <div className="chart-cluster">
          <button type="button" onClick={() => setIndicatorDialogOpen(true)}>
            Indicators
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
          <button type="button" onClick={() => setAlertDialogOpen(true)}>
            Alert
          </button>
          <button type="button" onClick={() => chart?.timeScale().fitContent()}>
            Fit
          </button>
          <button type="button" onClick={() => chart?.timeScale().scrollToRealTime()}>
            Latest
          </button>
          {/* Story 18.4 (AC #1): candles-only, like the tools that need the candle array. */}
          <button
            id="btn-replay"
            type="button"
            disabled={mode === "lines" || replay.mode !== "off"}
            onClick={startReplayPick}
          >
            Replay
          </button>
        </div>
      </div>
      {replay.mode !== "off" && (
        <div role="group" aria-label="Replay controls">
          {replay.mode === "picking" ? (
            <span>Click a candle to start the replay</span>
          ) : (
            <>
              <button type="button" onClick={replay.togglePlay}>
                {replay.isPlaying ? "Pause" : "Play"}
              </button>
              <button type="button" aria-label="Step back" onClick={() => replay.step(-1)}>
                &lt;
              </button>
              <button type="button" aria-label="Step forward" onClick={() => replay.step(1)}>
                &gt;
              </button>
              <button type="button" aria-label="Replay speed" onClick={replay.cycleSpeed}>
                {replay.speed}x
              </button>
              <button type="button" onClick={startReplayPick}>
                Go to...
              </button>
            </>
          )}
          <button type="button" onClick={replay.exit}>
            Exit
          </button>
        </div>
      )}
      <div className="chart-workspace">
        {/* Story 18.1 (AC #1): the left tool rail, generated from CHART_TOOLS --
            .tabbtn's shared visual pattern (theme.css) with the narrow-rail overrides
            in index.css, same scoped-override precedent as .filter-panel .tabbtn. */}
        <div className="chart-toolbar" role="toolbar" aria-label="Chart tools">
          {SELECT_TOOLS.map(renderTool)}
          <button
            type="button"
            className={crosshairOn ? "tabbtn active" : "tabbtn"}
            aria-pressed={crosshairOn}
            aria-label="Crosshair toggle"
            onClick={() => setCrosshairOn((on) => !on)}
          >
            Cross
          </button>
          <hr className="chart-toolbar-divider" />
          {DRAWING_TOOLS.map(renderTool)}
        </div>
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
            panes={panes}
            priceLines={priceLines}
            onPriceClick={handlePriceClick}
            drawings={drawings}
            pendingAnchor={pendingAnchor}
            drawEditable={activeTool === "cursor" && replayMode !== "picking"}
            onDrawingColor={handleDrawingColor}
            onDrawingDelete={handleDrawingDelete}
            onDrawingSettings={requestSettings}
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
            markerTime={replay.markerTime}
            anchorMarkerTime={autoView.markerTime}
            legendExtras={anchored.legend}
          />
        </div>
      </div>
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
      {settingsDrawing && settingsDrawing.kind !== "hline" && settingsDrawing.kind !== "trendline" && (precision || settingsDrawing.kind === "anchored_vp" || settingsDrawing.kind === "anchored_vwap") && (
        <DrawingSettingsDialog
          key={settingsDrawing.id}
          drawing={settingsDrawing}
          precision={precision}
          onApply={applyDrawing}
          onRemove={() => handleDrawingDelete(settingsDrawing.id)}
          onClose={() => setSettingsId(null)}
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
      {frvps.length > 0 && (
        <div role="group" aria-label="Volume profiles">
          {frvps.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-label={`Remove volume profile ${f.id}`}
              onClick={() => setFrvps((all) => all.filter((x) => x.id !== f.id))}
            >
              {f.id} x
            </button>
          ))}
          <VolumeProfileSettingsPanel
            title="Fixed range volume profile settings"
            value={frvpSettings}
            onChange={handleFrvpSettings}
          />
        </div>
      )}
      {/* Anchor target of the top bar's "Indicators" entry point: the picker plus the chart-only
          overlay controls. */}
      <div id="indicators" tabIndex={-1}>
      <VrvpControl
        active={vrvpActive}
        candlesMode={mode === "candles"}
        settings={vrvpSettings}
        onAdd={() => setVrvpActive(true)}
        onRemove={() => setVrvpActive(false)}
        onSettingsChange={setVrvpSettings}
      />
      <SessionProfileControl
        active={
          sessionCfg
            ? {
                preset: sessionCfg.preset,
                period: sessionCfg.period,
                settings: sessionCfg.settings,
                anchor: sessionCfg.anchor,
                ibMinutes: sessionCfg.ibMinutes,
                letters: sessionCfg.letters,
              }
            : null
        }
        candlesMode={mode === "candles"}
        renderedCount={sessionCfg?.preset === "auto" ? autoView.specs.length : sessionSpecs.length}
        onAdd={addSessionProfile}
        onRemove={() => setSessionCfg(null)}
        onPeriodChange={changeSessionPeriod}
        onOptionsChange={changeSessionOptions}
        onSettingsChange={changeSessionSettings}
      />
      <AlertDialog
        open={alertDialogOpen}
        onClose={() => setAlertDialogOpen(false)}
        instrumentId={instrumentId}
        barSeconds={barSeconds}
        priceLines={priceLines}
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
      />
      </div>
    </div>
  );
}

export default function ChartPage() {
  const { iid } = useParams<{ iid: string }>();
  if (!iid) return <p>No instrument specified.</p>;
  return <ChartForCoin key={iid} instrumentId={iid} />;
}

function ChartForCoin({ instrumentId }: { instrumentId: string }) {
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
    return layoutStore.status === "failed" ? (
      <p role="alert" className="chart-load-error">
        The layout of {instrumentId} could not be loaded, so the chart is not drawn (see the error bar). A server or network failure is retried every few seconds.
      </p>
    ) : (
      <p>Loading the layout of {instrumentId}...</p>
    );
  }

  // Keyed by instrument, bar size AND reset: a fresh LightweightChart + useCandles set per
  // (coin, timeframe), rather than trying to re-point one long-lived chart instance (see
  // LightweightChart's own docstring -- lightweight-charts has no supported API for that).
  return (
    <>
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
    </>
  );
}
