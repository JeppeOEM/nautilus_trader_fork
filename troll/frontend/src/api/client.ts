// Thin typed fetch helper over the generated OpenAPI schema (AD-F5) -- proves the codegen
// pipeline's output is actually consumed, not just generated and ignored (Story 15.1 AC #3).
import type {
  CandlesResponse,
  HealthResponse,
  IndicatorCatalogEntry,
  IndicatorConfigEntry,
  IndicatorSeriesResponse,
  IndicatorValuesResponse,
  MetricsHistoryResponse,
  RankingsResponse,
  SnapshotSeriesResponse,
} from "./schema";

export type {
  CandlesResponse,
  HealthResponse,
  IndicatorCatalogEntry,
  IndicatorConfigEntry,
  IndicatorSeriesResponse,
  IndicatorValuesResponse,
  MetricsHistoryResponse,
  RankingsResponse,
  SnapshotSeriesResponse,
};

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`GET /api/health failed: ${res.status}`);
  return (await res.json()) as HealthResponse;
}

// Story 15.2: initial seed for RankingsPage -- React Query's useQuery calls this once
// on mount; useLiveChannel's /ws/live subscription drives every update after that (no
// polling fallback, per troll/CLAUDE.md/epics AC2/AC3).
export async function fetchRankings(): Promise<RankingsResponse> {
  const res = await fetch("/api/rankings");
  if (!res.ok) throw new Error(`GET /api/rankings failed: ${res.status}`);
  return (await res.json()) as RankingsResponse;
}

// Story 15.3: cursor-paginated candle history (AD-F3) -- `useCandles` calls this once for
// the initial window and once per scroll-back refill, never for a full-range load.
export async function fetchCandles(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
): Promise<CandlesResponse> {
  const params = new URLSearchParams({
    before_ns: String(beforeNs),
    limit: String(limit),
    bar_seconds: String(barSeconds),
  });
  const res = await fetch(`/api/candles/${encodeURIComponent(instrumentId)}?${params}`);
  if (!res.ok) throw new Error(`GET /api/candles/${instrumentId} failed: ${res.status}`);
  return (await res.json()) as CandlesResponse;
}

// Story 15.4: cursor-paginated OFI/OBI/microprice/spread history (AD-F3) -- mirrors
// fetchCandles()'s exact shape, so `useIndicatorSeries` can co-page with `useCandles`
// using the identical before_ns/limit/bar_seconds tuple.
export async function fetchIndicatorSeries(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
): Promise<IndicatorSeriesResponse> {
  const params = new URLSearchParams({
    before_ns: String(beforeNs),
    limit: String(limit),
    bar_seconds: String(barSeconds),
  });
  const res = await fetch(`/api/indicator-series/${encodeURIComponent(instrumentId)}?${params}`);
  if (!res.ok) throw new Error(`GET /api/indicator-series/${instrumentId} failed: ${res.status}`);
  return (await res.json()) as IndicatorSeriesResponse;
}

// Story 15.7: cursor-paginated bid/ask/mid/micro/price history (AD-F3) for Lines mode --
// no `bar_seconds` (snapshots are per-second rows, no bar/aggregation concept). `useSnapshotSeries`
// calls this once for the initial window and once per scroll-back refill, mirroring
// `fetchCandles()`'s exact shape.
export async function fetchSnapshotSeries(
  instrumentId: string,
  beforeNs: number,
  limit: number,
): Promise<SnapshotSeriesResponse> {
  const params = new URLSearchParams({ before_ns: String(beforeNs), limit: String(limit) });
  const res = await fetch(`/api/snapshots/${encodeURIComponent(instrumentId)}?${params}`);
  if (!res.ok) throw new Error(`GET /api/snapshots/${instrumentId} failed: ${res.status}`);
  return (await res.json()) as SnapshotSeriesResponse;
}

// Story 17.2/15.8: the trailing 31-day metrics-store window for HistoryPage's small-
// multiples charts -- no before_ns/limit cursor contract (AD-F3's documented exception,
// see that story's Dev Notes): metrics_store.history() is already a small, fixed-size
// window, fetched once per page load, not scroll-back.
export async function fetchMetricsHistory(instrumentId: string, days = 31): Promise<MetricsHistoryResponse> {
  const params = new URLSearchParams({ days: String(days) });
  const res = await fetch(`/api/metrics/history/${encodeURIComponent(instrumentId)}?${params}`);
  if (!res.ok) throw new Error(`GET /api/metrics/history/${instrumentId} failed: ${res.status}`);
  return (await res.json()) as MetricsHistoryResponse;
}

// Story 15.6: the merged native+custom indicator catalog -- IndicatorPicker's list always
// comes from here, never a hand-duplicated frontend copy (spec's "Never" list).
export async function fetchIndicatorCatalog(): Promise<Record<string, IndicatorCatalogEntry>> {
  const res = await fetch("/api/indicators/catalog");
  if (!res.ok) throw new Error(`GET /api/indicators/catalog failed: ${res.status}`);
  return (await res.json()) as Record<string, IndicatorCatalogEntry>;
}

// Story 15.6: this coin's persisted picker selection -- `[]` when nothing has been saved
// yet (not an error).
export async function fetchCoinIndicatorConfig(instrumentId: string): Promise<IndicatorConfigEntry[]> {
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/indicators`);
  if (!res.ok) throw new Error(`GET /api/coin/${instrumentId}/indicators failed: ${res.status}`);
  return (await res.json()) as IndicatorConfigEntry[];
}

// Story 15.6: persists the FULL updated list on any picker change (add/remove/param-apply)
// -- never auto-save-per-keystroke, matching the relocated handler's explicit-Save contract.
export async function saveCoinIndicatorConfig(
  instrumentId: string,
  entries: IndicatorConfigEntry[],
): Promise<{ ok: boolean }> {
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/indicators`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(entries),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(`PUT /api/coin/${instrumentId}/indicators failed: ${res.status} ${JSON.stringify(body)}`);
  }
  return (await res.json()) as { ok: boolean };
}

// Story 15.6: cursor-paginated indicator-values history (AD-F3) -- mirrors
// fetchCandles()/fetchIndicatorSeries()'s before_ns/limit/bar_seconds contract, plus the
// caller-supplied `entries` (name+params) list of which picker-configured indicators to
// replay over the same bounded window.
export async function fetchIndicatorValues(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
  entries: { name: string; params: Record<string, unknown> }[],
): Promise<IndicatorValuesResponse> {
  const params = new URLSearchParams({
    before_ns: String(beforeNs),
    limit: String(limit),
    bar_seconds: String(barSeconds),
    entries: JSON.stringify(entries),
  });
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/indicator-values?${params}`);
  if (!res.ok) throw new Error(`GET /api/coin/${instrumentId}/indicator-values failed: ${res.status}`);
  return (await res.json()) as IndicatorValuesResponse;
}
