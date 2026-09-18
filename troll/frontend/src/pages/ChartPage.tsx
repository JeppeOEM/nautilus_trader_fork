import type { IChartApi } from "lightweight-charts";
import { useMemo, useState } from "react";
import { useParams } from "react-router";

import { fetchCoinIndicatorConfig, saveCoinIndicatorConfig } from "../api/client";
import IndicatorPicker from "../components/chart/IndicatorPicker";
import LightweightChart, { type ChartMode, type IndicatorPaneSpec } from "../components/chart/LightweightChart";
import { assignPaneColor } from "../components/chart/paneColors";
import type { IndicatorConfigEntry } from "../api/schema";
import { BAR_SECONDS, useCandles } from "../hooks/useCandles";
import { useIndicatorSeries } from "../hooks/useIndicatorSeries";
import { useLiveCandle } from "../hooks/useLiveCandle";
import { usePickerIndicatorValues } from "../hooks/usePickerIndicatorValues";
import { useSnapshotSeries } from "../hooks/useSnapshotSeries";

// Story 15.4's five default panes (FR-39) -- fixed, untouched by Story 15.6's picker;
// this order is also the color-slot assignment order (AC #7) for exactly these five.
const DEFAULT_PANE_IDS = ["MultiLevelOFI", "MultiLevelOBI", "microprice", "spread", "volume"];

function ChartInner({ instrumentId }: { instrumentId: string }) {
  const [chart, setChart] = useState<IChartApi | null>(null);
  // Story 15.7: Candles/Lines toggle (AC #1) -- `dashboard.py`'s own #btn-candles/
  // #btn-lines pair, carried forward. Only one of useCandles/useSnapshotSeries is ever
  // `enabled` at a time (Task 2): the disabled one issues no requests but keeps whatever
  // it already loaded, so toggling back doesn't re-fetch from scratch.
  const [mode, setMode] = useState<ChartMode>("candles");
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

  return (
    <div>
      <h1>{instrumentId}</h1>
      <div>
        <button
          id="btn-candles"
          type="button"
          disabled={mode === "candles"}
          onClick={() => setMode("candles")}
        >
          Candles
        </button>
        <button id="btn-lines" type="button" disabled={mode === "lines"} onClick={() => setMode("lines")}>
          Lines
        </button>
      </div>
      <div className="term-box" data-label={instrumentId}>
        <LightweightChart
          mode={mode}
          data={candles}
          linesData={snapshotLines}
          onChartApi={setChart}
          panes={panes}
          liveBar={liveBar}
        />
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
