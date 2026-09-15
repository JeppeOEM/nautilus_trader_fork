import type { IChartApi } from "lightweight-charts";
import { useState } from "react";
import { useParams } from "react-router";

import LightweightChart from "../components/chart/LightweightChart";
import { useCandles } from "../hooks/useCandles";

function ChartInner({ instrumentId }: { instrumentId: string }) {
  const [chart, setChart] = useState<IChartApi | null>(null);
  const { candles } = useCandles(instrumentId, chart);

  return (
    <div>
      <h1>{instrumentId}</h1>
      <LightweightChart data={candles} onChartApi={setChart} />
    </div>
  );
}

export default function ChartPage() {
  const { iid } = useParams<{ iid: string }>();
  if (!iid) return <p>No instrument specified.</p>;

  // Keyed by instrument id: a fresh LightweightChart + useCandles pair per coin,
  // rather than trying to re-point one long-lived chart instance (see
  // LightweightChart's own docstring -- lightweight-charts has no supported API for that).
  return <ChartInner key={iid} instrumentId={iid} />;
}
