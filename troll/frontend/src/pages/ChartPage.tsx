import type { IChartApi } from "lightweight-charts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router";

import { fetchCoinIndicatorConfig, saveCoinIndicatorConfig } from "../api/client";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import LightweightChart, {
  type ChartMode,
  type DrawingSpec,
  type IndicatorPaneSpec,
  type PriceLineSpec,
} from "../components/chart/LightweightChart";
import type { TrendlineAnchor } from "../components/chart/primitives/TrendlinePrimitive";
import { assignPaneColor, cssVar } from "../components/chart/paneColors";
import type { IndicatorConfigEntry } from "../api/schema";
import { BAR_SECONDS, useCandles } from "../hooks/useCandles";
import { useIndicatorSeries } from "../hooks/useIndicatorSeries";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
import { useSnapshotSeries } from "../hooks/useSnapshotSeries";

// Story 15.4's five default panes (FR-39) -- fixed, untouched by Story 15.6's picker;
// this order is also the color-slot assignment order (AC #7) for exactly these five.
const DEFAULT_PANE_IDS = ["MultiLevelOFI", "MultiLevelOBI", "microprice", "spread", "volume"];

// Story 18.1 (AC #1): the chart's drawing-tool state -- "cursor" is the inert default.
// Stories 18.2/18.3 extend this union with their tools, never a second state variable.
export type ChartTool = "cursor" | "hline" | "trendline";

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
];

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
  const { candles, volume } = useCandles(instrumentId, chart, mode === "candles");
  const snapshotLines = useSnapshotSeries(instrumentId, chart, mode === "lines");
  const indicatorSeries = useIndicatorSeries(instrumentId, chart);
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
        data: pickerValues[key],
        // Combined with DEFAULT_PANE_IDS (not its own separate `pickerSeriesKeys`-only
        // domain) so a picker pane's color slot can never land on the same palette index
        // as one of the five fixed default panes.
        color: assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
      })),
    ],
    [indicatorSeries, volume, pickerSeriesKeys, pickerValues],
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
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

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
    [activeTool, pendingAnchor],
  );

  const selectTool = (tool: ChartTool): void => {
    setActiveTool(tool);
    setPendingAnchor(null);
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
          }}
        >
          Lines
        </button>
      </div>
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
            data={candles}
            linesData={snapshotLines}
            onChartApi={setChart}
            panes={panes}
            priceLines={priceLines}
            onPriceClick={handlePriceClick}
            drawings={drawings}
            onPointClick={handlePointClick}
            onPriceLineDrag={handlePriceLineDrag}
            liveBar={liveBar}
          />
        </div>
      </div>
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
