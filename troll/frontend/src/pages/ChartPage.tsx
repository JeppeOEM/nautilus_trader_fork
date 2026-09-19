import { CrosshairMode, type IChartApi } from "lightweight-charts";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { fetchCoinIndicatorConfig, fetchIndicatorCatalog, saveCoinIndicatorConfig } from "../api/client";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import LightweightChart, {
  type ChartMode,
  type IndicatorPaneSpec,
  type PriceLineSpec,
} from "../components/chart/LightweightChart";
import { assignPaneColor, cssVar } from "../components/chart/paneColors";
import type { IndicatorCatalogEntry, IndicatorConfigEntry } from "../api/schema";
import { BAR_SECONDS, useCandles } from "../hooks/useCandles";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
import { useSnapshotSeries } from "../hooks/useSnapshotSeries";

// The default chart is candles + a volume overlay only; every other indicator is added
// from the picker (persisted per coin server-side) and placed by its catalog `panel`.
// Volume is its own pane right under the price pane (spec §A1), before indicator panes.
const DEFAULT_PANE_IDS = ["volume"];

// Spec §A8.1 slot 2: the top toolbar's timeframe selector (+1s, beyond the spec's list). Bars > 1h are served from the
// minute rollup server-side (Story 16.2), so 4H/1D/1W stay cheap.
const TIMEFRAMES = [
  { label: "1s", seconds: 1 }, // the collector stores per-second OHLC, so 1s bars are native
  { label: "1m", seconds: 60 },
  { label: "5m", seconds: 300 },
  { label: "15m", seconds: 900 },
  { label: "1H", seconds: 3600 },
  { label: "4H", seconds: 14400 },
  { label: "1D", seconds: 86400 },
  { label: "1W", seconds: 604800 },
] as const;

function timeframeStorageKey(instrumentId: string): string {
  return `chart-timeframe:${instrumentId}`;
}

function loadTimeframe(instrumentId: string): number {
  try {
    const saved = Number(localStorage.getItem(timeframeStorageKey(instrumentId)));
    return TIMEFRAMES.some((t) => t.seconds === saved) ? saved : BAR_SECONDS;
  } catch {
    return BAR_SECONDS;
  }
}

function hlineStorageKey(instrumentId: string): string {
  return `chart-hlines:${instrumentId}`;
}

// Drawn lines persist per coin in this browser (localStorage may throw/be blocked --
// then the chart just starts empty). ponytail: browser-local, move server-side beside
// the indicator config if lines must follow the user across browsers.
function loadPriceLines(instrumentId: string): PriceLineSpec[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(hlineStorageKey(instrumentId)) ?? "[]");
    return Array.isArray(parsed) ? (parsed as PriceLineSpec[]) : [];
  } catch {
    return [];
  }
}

// `key` is "{indicator_id}.{output_attr}" and indicator_id starts with the catalog name.
// Longest name wins so a name that prefixes another can't claim its keys.
function catalogNameForKey(key: string, catalog: Record<string, IndicatorCatalogEntry>): string | undefined {
  return Object.keys(catalog)
    .filter((n) => key === n || key.startsWith(`${n}_`) || key.startsWith(`${n}.`))
    .sort((x, y) => y.length - x.length)[0];
}

function panelForKey(key: string, catalog: Record<string, IndicatorCatalogEntry>): string {
  const name = catalogNameForKey(key, catalog);
  return name ? catalog[name].panel : "oscillator";
}

// Legend title, TradingView-style: name plus its params, e.g. "RelativeStrengthIndex (14)".
function legendTitle(name: string, entries: IndicatorConfigEntry[]): string {
  const params = Object.values(entries.find((e) => e.name === name)?.params ?? {});
  return params.length ? `${name} (${params.join(", ")})` : name;
}

// Story 18.1 (AC #1): the chart's drawing-tool state -- "cursor" is the inert default.
// Stories 18.2/18.3 extend this union with their tools, never a second state variable.
export type ChartTool = "cursor" | "hline";

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
];

interface ChartInnerProps {
  instrumentId: string;
  barSeconds: number;
  onTimeframeChange: (seconds: number) => void;
}

function ChartInner({ instrumentId, barSeconds, onTimeframeChange }: ChartInnerProps) {
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
  const [priceLines, setPriceLines] = useState<PriceLineSpec[]>(() => loadPriceLines(instrumentId));
  const nextPriceLineIdRef = useRef(
    priceLines.reduce((max, l) => Math.max(max, Number(l.id.replace("hline-", "")) || 0), 0) + 1,
  );
  const [crosshairOn, setCrosshairOn] = useState(true);
  const [indicatorDialogOpen, setIndicatorDialogOpen] = useState(false);
  const [catalog, setCatalog] = useState<Record<string, IndicatorCatalogEntry>>({});
  const { candles, volume, loadFailed } = useCandles(instrumentId, chart, mode === "candles", barSeconds);
  const snapshotLines = useSnapshotSeries(instrumentId, chart, mode === "lines");
  // Story 15.5: the forming right-edge bar, over its own dedicated /ws/live socket
  // (AD-F7) -- same BAR_SECONDS constant useCandles uses, so the two paths can't drift.
  // Lines mode has no live-edge concept of its own (Task 3's Dev Note) -- LightweightChart
  // itself ignores `liveBar` while `mode === "lines"` (its own seriesRef is null there).
  const liveBar = useLiveCandle(instrumentId, barSeconds);

  // Story 15.6: the picker's persisted selection for this coin -- IndicatorPicker owns
  // the GET (initial load)/PUT (every add/remove/param-apply) round trip and reports the
  // resulting list here; this component decides how it becomes panes (AD-F4).
  const [pickerEntries, setPickerEntries] = useState<IndicatorConfigEntry[]>([]);
  const pickerValues = usePickerIndicatorValues(instrumentId, chart, pickerEntries, barSeconds);
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
  // overlay (derived from useCandles' own `v` field); picker entries follow, one series per
  // `{indicator_id}.{output_attr}` key, placed by the catalog's `panel`.
  const panes = useMemo<IndicatorPaneSpec[]>(
    () => [
      {
        id: "volume",
        kind: "Histogram",
        data: volume,
        color: assignPaneColor("volume", DEFAULT_PANE_IDS),
        groupLabel: "Volume",
      },
      ...pickerSeriesKeys.map((key) => {
        const panel = panelForKey(key, catalog);
        const name = catalogNameForKey(key, catalog) ?? key;
        return {
          id: key,
          group: name,
          groupLabel: legendTitle(name, pickerEntries),
          outputLabel: key.slice(key.lastIndexOf(".") + 1),
          kind: panel === "histogram" ? ("Histogram" as const) : ("Line" as const),
          data: pickerValues[key],
          // Combined with DEFAULT_PANE_IDS so a picker series never lands on volume's slot.
          color: assignPaneColor(key, [...DEFAULT_PANE_IDS, ...pickerSeriesKeys]),
          placement: panel === "overlay" ? ("overlay" as const) : ("pane" as const),
        };
      }),
    ],
    [volume, pickerSeriesKeys, pickerValues, catalog, pickerEntries],
  );

  useEffect(() => {
    chart?.applyOptions({ crosshair: { mode: crosshairOn ? CrosshairMode.Normal : CrosshairMode.Hidden } });
  }, [chart, crosshairOn]);

  useEffect(() => {
    fetchIndicatorCatalog()
      .then(setCatalog)
      .catch((err: unknown) => console.error("ChartPage: failed to load indicator catalog", err));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(hlineStorageKey(instrumentId), JSON.stringify(priceLines));
    } catch {
      // storage blocked: lines just won't survive a reload
    }
  }, [instrumentId, priceLines]);

  useEffect(() => {
    // Story 18.1 (AC #5): Esc cancels the active tool from anywhere on the page, not
    // just from a focused chart -- an armed tool with no in-chart escape is exactly
    // the stranded state this prevents. Runs regardless of the current tool: Esc in
    // cursor mode is a harmless no-op.
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") setActiveTool("cursor");
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
      {/* Spec §A8.1 top toolbar, clusters left to right: [symbol + timeframe] [chart type]
          [indicators + fit + jump]. No theme toggle: the visual identity is fixed (15.9).
          The symbol slot is a read-only label + back link -- coins are picked on Rankings. */}
      <div className="chart-topbar" role="toolbar" aria-label="Chart controls">
        <div className="chart-cluster">
          <Link to="/">&larr; Rankings</Link>
          <h1>{instrumentId}</h1>
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
            setActiveTool("cursor");
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
            setActiveTool("cursor");
          }}
        >
          Lines
        </button>
        </div>
        <div className="chart-cluster">
          <button type="button" onClick={() => setIndicatorDialogOpen(true)}>
            Indicators
          </button>
          <button type="button" onClick={() => chart?.timeScale().fitContent()}>
            Fit
          </button>
          <button type="button" onClick={() => chart?.timeScale().scrollToRealTime()}>
            Latest
          </button>
        </div>
      </div>
      <div className="chart-workspace">
        {/* Story 18.1 (AC #1): the left tool rail, generated from CHART_TOOLS --
            .tabbtn's shared visual pattern (theme.css) with the narrow-rail overrides
            in index.css, same scoped-override precedent as .filter-panel .tabbtn. */}
        <div className="chart-toolbar" role="toolbar" aria-label="Chart tools">
          {CHART_TOOLS.map((tool) => (
            <Fragment key={tool.id}>
              {tool.id === "hline" && (
                <>
                  <button
                    type="button"
                    className={crosshairOn ? "tabbtn active" : "tabbtn"}
                    aria-pressed={crosshairOn}
                    aria-label="Crosshair toggle"
                    onClick={() => setCrosshairOn((on) => !on)}
                  >
                    Cross
                  </button>
                  <hr className="chart-toolbar-sep" />
                </>
              )}
            <button
              type="button"
              className={activeTool === tool.id ? "tabbtn active" : "tabbtn"}
              aria-pressed={activeTool === tool.id}
              aria-label={tool.ariaLabel}
              data-tool={tool.id}
              disabled={mode === "lines" && tool.candlesOnly}
              onClick={() => setActiveTool(tool.id)}
            >
              {tool.label}
            </button>
            </Fragment>
          ))}
        </div>
        <div className="term-box" data-label={instrumentId}>
          {loadFailed && (
            <div role="alert" className="chart-load-error">
              Can't reach the data API -- history not loaded, retrying...
            </div>
          )}
          <LightweightChart
            mode={mode}
            data={candles}
            linesData={snapshotLines}
            onChartApi={setChart}
            panes={panes}
            priceLines={priceLines}
            onPriceClick={handlePriceClick}
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
        dialogOpen={indicatorDialogOpen}
        onDialogClose={() => setIndicatorDialogOpen(false)}
      />
    </div>
  );
}

export default function ChartPage() {
  const { iid } = useParams<{ iid: string }>();
  if (!iid) return <p>No instrument specified.</p>;
  return <ChartForCoin key={iid} instrumentId={iid} />;
}

function ChartForCoin({ instrumentId }: { instrumentId: string }) {
  const [barSeconds, setBarSeconds] = useState(() => loadTimeframe(instrumentId));

  const changeTimeframe = useCallback(
    (seconds: number): void => {
      setBarSeconds(seconds);
      try {
        localStorage.setItem(timeframeStorageKey(instrumentId), String(seconds));
      } catch {
        // storage blocked: the choice just won't survive a reload
      }
    },
    [instrumentId],
  );

  // Keyed by instrument AND bar size: a fresh LightweightChart + useCandles set per
  // (coin, timeframe), rather than trying to re-point one long-lived chart instance (see
  // LightweightChart's own docstring -- lightweight-charts has no supported API for that).
  return (
    <ChartInner
      key={`${instrumentId}:${barSeconds}`}
      instrumentId={instrumentId}
      barSeconds={barSeconds}
      onTimeframeChange={changeTimeframe}
    />
  );
}
