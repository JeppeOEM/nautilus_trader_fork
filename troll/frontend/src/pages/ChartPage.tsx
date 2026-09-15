import type { IChartApi } from "lightweight-charts";
import { useMemo, useState } from "react";
import { useParams } from "react-router";

import LightweightChart, { type IndicatorPaneSpec } from "../components/chart/LightweightChart";
import { assignPaneColor } from "../components/chart/paneColors";
import { useCandles } from "../hooks/useCandles";
import { useIndicatorSeries } from "../hooks/useIndicatorSeries";

// Story 15.4's five default panes (FR-39) -- fixed order for now (Story 15.6 makes this
// user-configurable); this order is also the color-slot assignment order (AC #7).
const DEFAULT_PANE_IDS = ["MultiLevelOFI", "MultiLevelOBI", "microprice", "spread", "volume"];

function ChartInner({ instrumentId }: { instrumentId: string }) {
  const [chart, setChart] = useState<IChartApi | null>(null);
  const { candles, volume } = useCandles(instrumentId, chart);
  const indicatorSeries = useIndicatorSeries(instrumentId, chart);

  // Declarative pane set fed into LightweightChart's own registry (AD-F4) -- this
  // component never calls chart.addPane()/addSeries() itself. Volume is derived
  // straight from useCandles' own already-fetched `v` field, no new query (Task 1's
  // Dev Note); OFI/OBI/microprice/spread come from the co-paged useIndicatorSeries hook.
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
    ],
    [indicatorSeries, volume],
  );

  return (
    <div>
      <h1>{instrumentId}</h1>
      <LightweightChart data={candles} onChartApi={setChart} panes={panes} />
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
