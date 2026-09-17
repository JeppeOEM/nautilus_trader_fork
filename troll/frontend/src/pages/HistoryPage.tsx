import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useParams } from "react-router";
import type { UTCTimestamp } from "lightweight-charts";

import { fetchMetricsHistory } from "../api/client";
import type { MetricHistoryItem } from "../api/schema";
import MetricTile, { type MetricDatum } from "../components/chart/MetricTile";

// `metrics_store.COLS` minus `rank` (not one of the "ranking-input metrics" AC #2 names) --
// order here is also render order. Extend alongside `ranking_engine/metrics_store.py`'s
// `COLS` if that ever changes.
type MetricColumnKey = Exclude<keyof Omit<MetricHistoryItem, "ts">, "rank">;

const METRIC_COLUMNS: { key: MetricColumnKey; label: string }[] = [
  { key: "price", label: "Price" },
  { key: "pct_1h", label: "1h %" },
  { key: "pct_24h", label: "24h %" },
  { key: "volatility", label: "Volatility" },
  { key: "ofi", label: "OFI" },
  { key: "microprice", label: "Microprice" },
  { key: "spread", label: "Spread" },
  { key: "volume24h", label: "Volume 24h" },
];

// Stable empty-array identity (mirrors useSnapshotSeries.ts's own EMPTY_LINES precedent)
// -- a fresh `[]` literal every render (e.g. `data?.items ?? []`) would otherwise change
// identity on every render while `data` is still undefined, defeating the `tiles`
// useMemo below (react-hooks/exhaustive-deps).
const EMPTY_ITEMS: MetricHistoryItem[] = [];

function toMetricDatum(tsNs: number, value: number | null | undefined): MetricDatum {
  const time = (tsNs / 1_000_000_000) as UTCTimestamp; // wire is ns, lightweight-charts wants seconds
  // A `None` value (a real gap, DATA-01/AD-F6) is passed straight through as native
  // whitespace data -- never `0`, never an omitted row (which a line series would
  // otherwise interpolate across), same discipline as useSnapshotSeries.ts's toDatum().
  return value == null ? { time } : { time, value };
}

function HistoryInner({ instrumentId }: { instrumentId: string }) {
  const { data, isError } = useQuery({
    queryKey: ["metrics-history", instrumentId],
    queryFn: () => fetchMetricsHistory(instrumentId),
  });
  const items = data?.items ?? EMPTY_ITEMS;

  // A column that is None for every row in the response is skipped entirely (matches
  // dashboard.py's own `_history_page_from_rows`' "skip if all values are None"
  // behavior) -- computed once per fetch, not on every render.
  const tiles = useMemo(
    () =>
      METRIC_COLUMNS.map((col) => ({
        ...col,
        data: items.map((row) => toMetricDatum(row.ts, row[col.key])),
      })).filter((tile) => tile.data.some((d) => "value" in d)),
    [items],
  );

  // isError first: a failed fetch must never render as an indefinite "Loading…" (review
  // finding) -- data stays undefined forever in that case, so the loading check alone
  // would otherwise hang with no feedback.
  if (isError) return <p>Failed to load history for {instrumentId}.</p>;
  if (data === undefined) return <p className="term-loading">Loading history…</p>;

  return (
    <div>
      <h1>{instrumentId} — 31-day history</h1>
      {tiles.length === 0 ? (
        <p>No history yet.</p>
      ) : (
        tiles.map((tile) => <MetricTile key={tile.key} label={tile.label} data={tile.data} />)
      )}
    </div>
  );
}

export default function HistoryPage() {
  const { iid } = useParams<{ iid: string }>();
  if (!iid) return <p>No instrument specified.</p>;

  // Keyed by instrument id, same "fresh hook set per coin" convention ChartPage/ChartInner
  // already established -- a fresh useQuery cache entry per instrument rather than trying
  // to re-point one long-lived query.
  return <HistoryInner key={iid} instrumentId={iid} />;
}
