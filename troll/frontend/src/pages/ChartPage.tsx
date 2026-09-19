import type { IChartApi, Time } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";

import { fetchCoinIndicatorConfig, saveCoinIndicatorConfig } from "../api/client";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import LightweightChart, {
  type ChartMode,
  type DrawingSpec,
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
import {
  DEFAULT_SESSION_COUNT,
  SESSION_PRESETS,
  buildSessionProfiles,
  drawableSpan,
  periodStartBack,
  sessionBarSeconds,
  type SessionPeriod,
  type SessionPreset,
  type SessionProfileCache,
  type SessionProfileSettings,
} from "../lib/sessionProfile";
import { assignPaneColor, cssVar } from "../components/chart/paneColors";
import type { IndicatorConfigEntry } from "../api/schema";
import { BAR_SECONDS, useCandles } from "../hooks/useCandles";
import { useIndicatorSeries } from "../hooks/useIndicatorSeries";
import { useReplay } from "../hooks/useReplay";
import { useSessionCandles } from "../hooks/useSessionCandles";
import { useVisibleRange } from "../hooks/useVisibleRange";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
import { useSnapshotSeries } from "../hooks/useSnapshotSeries";

// Story 15.4's five default panes (FR-39) -- fixed, untouched by Story 15.6's picker;
// this order is also the color-slot assignment order (AC #7) for exactly these five.
const DEFAULT_PANE_IDS = ["MultiLevelOFI", "MultiLevelOBI", "microprice", "spread", "volume"];

// Story 18.1 (AC #1): the chart's drawing-tool state -- "cursor" is the inert default.
// Stories 18.2/18.3 extend this union with their tools, never a second state variable.
export type ChartTool = "cursor" | "hline" | "trendline" | "measure" | "frvp";

interface ChartToolDef {
  id: ChartTool;
  label: string;
  ariaLabel: string;
  /** No meaning in Lines mode (no single main series to attach to -- spec Task 2's
   * MVP scope decision): disables the button there and disarms an armed tool (see the
   * mode-guard effect in ChartInner). */
  candlesOnly: boolean;
}

// The left tool rail's tools, as data -- Stories 18.2/18.3 append entries here and
// the toolbar markup below never changes shape.
const CHART_TOOLS: readonly ChartToolDef[] = [
  { id: "cursor", label: "Cursor", ariaLabel: "Cursor tool", candlesOnly: false },
  { id: "hline", label: "HLine", ariaLabel: "Horizontal line tool", candlesOnly: true },
  { id: "trendline", label: "Trend", ariaLabel: "Trendline tool", candlesOnly: false },
  { id: "measure", label: "Measure", ariaLabel: "Measurement tool", candlesOnly: true },
  { id: "frvp", label: "FRVP", ariaLabel: "Fixed range volume profile tool", candlesOnly: true },
];

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

function ChartInner({ instrumentId }: { instrumentId: string }) {
  const [chart, setChart] = useState<IChartApi | null>(null);
  // Story 15.7: Candles/Lines toggle (AC #1) -- `dashboard.py`'s own #btn-candles/
  // #btn-lines pair, carried forward. Only one of useCandles/useSnapshotSeries is ever
  // `enabled` at a time (Task 2): the disabled one issues no requests but keeps whatever
  // it already loaded, so toggling back doesn't re-fetch from scratch.
  const [mode, setMode] = useState<ChartMode>("candles");
  // Story 18.1 (AC #1): which drawing tool is armed; "cursor" is the do-nothing
  // default. The placed lines' own state lives HERE too, not inside LightweightChart
  // -- that component stays a pure function of its props (AD-F4), this page owns the
  // data. Deterministic counter ids (no uuid) keep specs stable and diffable.
  const [activeTool, setActiveTool] = useState<ChartTool>("cursor");
  const [priceLines, setPriceLines] = useState<PriceLineSpec[]>([]);
  const nextPriceLineIdRef = useRef(1);
  // Story 18.2: the trendline's first click, held until the second click completes it
  // (or Esc / a tool change discards it); `drawings` is the placed set.
  const [pendingAnchor, setPendingAnchor] = useState<TrendlineAnchor | null>(null);
  const [drawings, setDrawings] = useState<DrawingSpec[]>([]);
  const nextDrawingIdRef = useRef(1);
  const [frvps, setFrvps] = useState<FrvpEntry[]>([]);
  const [frvpSettings, setFrvpSettings] = useState(DEFAULT_VOLUME_PROFILE_SETTINGS);
  const [edgeGhost, setEdgeGhost] = useState<EdgeGhost | null>(null);
  const nextFrvpIdRef = useRef(1);
  // Story 18.7: the single visible-range profile ("always recompute", unlike FRVP above).
  const [vrvpActive, setVrvpActive] = useState(false);
  // Story 18.8: the single session-profile slot (SVP / SVP HD presets). `sinceSeconds` is
  // fixed when the config is set (an event handler), so render stays pure.
  const [sessionCfg, setSessionCfg] = useState<SessionConfig | null>(null);
  const [sessionCache] = useState<SessionProfileCache>(() => new Map());
  const [vrvpSettings, setVrvpSettings] = useState(DEFAULT_VOLUME_PROFILE_SETTINGS);
  const { candles, volume: fullVolume } = useCandles(instrumentId, chart, mode === "candles");
  // Story 18.4: replay only trims the NEWEST end of the loaded candles for display
  // (`replay.displayed`); useCandles and its older-history refill are untouched.
  const replay = useReplay(candles);
  const { cancelPick: cancelReplayPick, pick: pickReplayBar, mode: replayMode, cutoffTime } = replay;
  // Volume and every indicator pane are cut at the same replay time as the candles, or
  // they would show the "future" the replay hides.
  const volume = useMemo(() => trimAfter(fullVolume, cutoffTime), [fullVolume, cutoffTime]);
  const snapshotLines = useSnapshotSeries(instrumentId, chart, mode === "lines");
  const fullIndicatorSeries = useIndicatorSeries(instrumentId, chart);
  const indicatorSeries = useMemo(
    () => ({
      ofi: trimAfter(fullIndicatorSeries.ofi, cutoffTime),
      obi: trimAfter(fullIndicatorSeries.obi, cutoffTime),
      microprice: trimAfter(fullIndicatorSeries.microprice, cutoffTime),
      spread: trimAfter(fullIndicatorSeries.spread, cutoffTime),
    }),
    [fullIndicatorSeries, cutoffTime],
  );
  // Story 15.5: the forming right-edge bar, over its own dedicated /ws/live socket
  // (AD-F7) -- same BAR_SECONDS constant useCandles uses, so the two paths can't drift.
  // Lines mode has no live-edge concept of its own (Task 3's Dev Note) -- LightweightChart
  // itself ignores `liveBar` while `mode === "lines"` (its own seriesRef is null there).
  const liveBar = useLiveCandle(instrumentId, BAR_SECONDS);

  // Story 15.6: the picker's persisted selection for this coin -- IndicatorPicker owns
  // the GET (initial load)/PUT (every add/remove/param-apply) round trip and reports the
  // resulting list here; this component decides how it becomes panes (AD-F4).
  const [pickerEntries, setPickerEntries] = useState<IndicatorConfigEntry[]>([]);
  const pickerValues = usePickerIndicatorValues(instrumentId, chart, pickerEntries);
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
  // component never calls chart.addPane()/addSeries() itself. Volume is derived
  // straight from useCandles' own already-fetched `v` field, no new query (Task 1's
  // Dev Note); OFI/OBI/microprice/spread come from the co-paged useIndicatorSeries hook;
  // Story 15.6's picker entries come from usePickerIndicatorValues, one pane per
  // `{indicator_id}.{output_attr}` key -- untouched five fixed panes stay first so their
  // own color slots never shift when a picker pane is added/removed.
  const panes = useMemo<IndicatorPaneSpec[]>(
    () => [
      {
        id: "MultiLevelOFI",
        kind: "Line",
        data: indicatorSeries.ofi,
        color: assignPaneColor("MultiLevelOFI", DEFAULT_PANE_IDS),
      },
      {
        id: "MultiLevelOBI",
        kind: "Line",
        data: indicatorSeries.obi,
        color: assignPaneColor("MultiLevelOBI", DEFAULT_PANE_IDS),
      },
      {
        id: "microprice",
        kind: "Line",
        data: indicatorSeries.microprice,
        color: assignPaneColor("microprice", DEFAULT_PANE_IDS),
      },
      {
        id: "spread",
        kind: "Line",
        data: indicatorSeries.spread,
        color: assignPaneColor("spread", DEFAULT_PANE_IDS),
      },
      {
        id: "volume",
        kind: "Histogram",
        data: volume,
        color: assignPaneColor("volume", DEFAULT_PANE_IDS),
      },
      ...pickerSeriesKeys.map((key) => ({
        id: key,
        kind: "Line" as const,
        data: trimAfter(pickerValues[key], cutoffTime),
        // Combined with DEFAULT_PANE_IDS (not its own separate `pickerSeriesKeys`-only
        // domain) so a picker pane's color slot can never land on the same palette index
        // as one of the five fixed default panes.
        color: assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
      })),
    ],
    [indicatorSeries, volume, pickerSeriesKeys, pickerValues, cutoffTime],
  );

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
  const handlePriceClick = useCallback(
    (price: number): void => {
      // Story 18.1 (AC #2): single-click-and-done -- only an armed hline tool places
      // a line, and the placement itself disarms it (the tool's interaction model,
      // not a persistent multi-click mode).
      if (activeTool !== "hline") return;
      const id = `hline-${nextPriceLineIdRef.current++}`;
      setPriceLines((lines) => [
        ...lines,
        { id, price, color: cssVar("--color-active", "#55ffff") },
      ]);
      setActiveTool("cursor");
    },
    [activeTool],
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
      if (activeTool !== "trendline") return;
      if (!pendingAnchor) {
        setPendingAnchor(point);
        return;
      }
      if (pendingAnchor.time === point.time && pendingAnchor.price === point.price) return;
      const id = `trendline-${nextDrawingIdRef.current++}`;
      setDrawings((all) => [
        ...all,
        { id, kind: "trendline", anchors: [pendingAnchor, point], color: cssVar("--color-active", "#55ffff") },
      ]);
      setPendingAnchor(null);
      setActiveTool("cursor");
    },
    [activeTool, pendingAnchor, replayMode, pickReplayBar],
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
  const sessionData = useSessionCandles(
    instrumentId,
    sessionActive,
    sessionCfg?.sinceSeconds ?? 0,
    sessionCfg ? sessionBarSeconds(sessionCfg.period) : 60,
  );
  const sessionSpecs = useMemo<VolumeProfileSpec[]>(() => {
    if (!sessionCfg || !sessionActive) return [];
    const entries = buildSessionProfiles(
      trimAfter(sessionData.candles, cutoffTime),
      trimAfter(sessionData.volume, cutoffTime),
      sessionCfg.period,
      sessionCfg.settings.sessionCount,
      sessionCfg.settings,
      sessionCache,
      sessionData.completeFrom,
    );
    const preset = SESSION_PRESETS[sessionCfg.preset];
    return entries.flatMap((entry) => {
      const span = drawableSpan(replay.displayed, entry.startTime, entry.endTime);
      if (!span) return [];
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
        },
      ];
    });
  }, [sessionCfg, sessionActive, sessionData, cutoffTime, sessionCache, replay.displayed]);

  const addSessionProfile = (preset: SessionPreset): void => {
    const { period, rowCount } = SESSION_PRESETS[preset];
    // Switching presets keeps the user's colors/toggles/session count; only the row count
    // takes the new preset's default.
    const settings: SessionProfileSettings = {
      ...(sessionCfg?.settings ?? { ...DEFAULT_VOLUME_PROFILE_SETTINGS, sessionCount: DEFAULT_SESSION_COUNT }),
      rowCount,
    };
    setSessionCfg({ preset, period, settings, sinceSeconds: sessionSince(period, settings.sessionCount) });
  };

  const changeSessionSettings = (settings: SessionProfileSettings): void =>
    setSessionCfg((cfg) =>
      cfg ? { ...cfg, settings, sinceSeconds: sessionSince(cfg.period, settings.sessionCount) } : cfg,
    );

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
    () => (sessionSpecs.length > 0 ? [...allVolumeProfiles, ...sessionSpecs] : allVolumeProfiles),
    [allVolumeProfiles, sessionSpecs],
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

  const handlePriceLineDrag = useCallback(
    (id: string, price: number): void => {
      // Story 18.1 (AC #3): LightweightChart only reports the drag (it never mutates
      // this state); the setState updater form needs no closure state, so this stays
      // identity-stable for the component's whole lifetime.
      setPriceLines((lines) => lines.map((line) => (line.id === id ? { ...line, price } : line)));
    },
    [],
  );

  return (
    <div>
      <h1>{instrumentId}</h1>
      <div>
        {/* The mode buttons double as the candles-only-tool disarm point (Story
            18.1): both buttons are disabled while their mode is already active, so
            these handlers only ever run on a real mode CHANGE -- and any mode change
            disarms the tool, in the handler itself rather than a state-syncing
            effect (react/set-state-in-effect). */}
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
          {CHART_TOOLS.map((tool) => (
            <button
              key={tool.id}
              type="button"
              className={activeTool === tool.id ? "tabbtn active" : "tabbtn"}
              aria-pressed={activeTool === tool.id}
              aria-label={tool.ariaLabel}
              data-tool={tool.id}
              disabled={mode === "lines" && tool.candlesOnly}
              onClick={() => selectTool(tool.id)}
            >
              {tool.label}
            </button>
          ))}
        </div>
        <div className="term-box" data-label={instrumentId}>
          <LightweightChart
            mode={mode}
            data={replay.displayed}
            linesData={snapshotLines}
            onChartApi={setChart}
            panes={panes}
            priceLines={priceLines}
            onPriceClick={handlePriceClick}
            drawings={drawings}
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
            // Story 18.4: the real-time forming bar would reveal "future" price action.
            liveBar={replay.mode === "active" ? null : liveBar}
            markerTime={replay.markerTime}
          />
        </div>
      </div>
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
      <VrvpControl
        active={vrvpActive}
        candlesMode={mode === "candles"}
        settings={vrvpSettings}
        onAdd={() => setVrvpActive(true)}
        onRemove={() => setVrvpActive(false)}
        onSettingsChange={setVrvpSettings}
      />
      <SessionProfileControl
        active={sessionCfg ? { preset: sessionCfg.preset, settings: sessionCfg.settings } : null}
        candlesMode={mode === "candles"}
        onAdd={addSessionProfile}
        onRemove={() => setSessionCfg(null)}
        onSettingsChange={changeSessionSettings}
      />
      <IndicatorPicker
        fetchConfig={() => fetchCoinIndicatorConfig(instrumentId)}
        saveConfig={(entries) => saveCoinIndicatorConfig(instrumentId, entries)}
        reloadKey={instrumentId}
        onEntriesChange={setPickerEntries}
      />
    </div>
  );
}

export default function ChartPage() {
  const { iid } = useParams<{ iid: string }>();
  if (!iid) return <p>No instrument specified.</p>;

  // Keyed by instrument id: a fresh LightweightChart + useCandles/useIndicatorSeries set
  // per coin, rather than trying to re-point one long-lived chart instance (see
  // LightweightChart's own docstring -- lightweight-charts has no supported API for that).
  return <ChartInner key={iid} instrumentId={iid} />;
}
