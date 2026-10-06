// Thin typed fetch helper over the generated OpenAPI schema (AD-F5) -- proves the codegen
// pipeline's output is actually consumed, not just generated and ignored (Story 15.1 AC #3).
import type { Drawing } from "../lib/drawings";
import { parseDrawings } from "../lib/drawings";
import { type ChartLayout, layoutForSave } from "../lib/chartLayout";
import type {
  AlertCreate,
  AlertResponse,
  ArchiveRun,
  ArchiveRunResponse,
  ArchiveStatusResponse,
  ArchiveStep,
  CandlesResponse,
  DrawingsResponse,
  FootprintItem,
  FootprintResponse,
  FootprintRow,
  FundingItem,
  FundingResponse,
  HealthResponse,
  IndicatorCatalogEntry,
  IndicatorConfigEntry,
  IndicatorValuesResponse,
  LiquidationBarItem,
  LiquidationBarsResponse,
  LiquidationItem,
  LiquidationsResponse,
  MarkIndexItem,
  MarkIndexResponse,
  MetricsHistoryResponse,
  OpenInterestItem,
  OpenInterestResponse,
  RankingModeResponse,
  RankingsResponse,
  SnapshotSeriesResponse,
  TechnicalsColumn,
  TechnicalsValuesResponse,
} from "./schema";

export type {
  AlertCreate,
  AlertResponse,
  ArchiveRun,
  ArchiveRunResponse,
  ArchiveStatusResponse,
  ArchiveStep,
  CandlesResponse,
  FootprintItem,
  FootprintResponse,
  FootprintRow,
  FundingItem,
  FundingResponse,
  HealthResponse,
  IndicatorCatalogEntry,
  IndicatorConfigEntry,
  IndicatorValuesResponse,
  LiquidationBarItem,
  LiquidationBarsResponse,
  LiquidationItem,
  LiquidationsResponse,
  MarkIndexItem,
  MarkIndexResponse,
  MetricsHistoryResponse,
  OpenInterestItem,
  OpenInterestResponse,
  RankingsResponse,
  SnapshotSeriesResponse,
  TechnicalsColumn,
};

/** A non-2xx response, with its status so callers can tell a proxy hiccup (502/503/504) from a
 * deterministic server error (a 500 the data API raised on purpose, DATA-07) that no retry fixes. */
export class HttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`GET /api/health failed: ${res.status}`);
  return (await res.json()) as HealthResponse;
}

// Story 15.2: initial seed for RankingsPage -- React Query's useQuery calls this once
// on mount; useLiveChannel's /ws/live subscription drives every update after that (no
// polling fallback, per platform/CLAUDE.md/epics AC2/AC3).
export async function fetchRankings(): Promise<RankingsResponse> {
  const res = await fetch("/api/rankings");
  if (!res.ok) throw new Error(`GET /api/rankings failed: ${res.status}`);
  return (await res.json()) as RankingsResponse;
}

// Story 25.1a: the one ranking-mode switch (global, last write wins). A 202 only means
// ranking_engine received the request -- the caller shows the mode from rankings:live, never
// from this response, so a switch is visible only once the engine has applied it. A 503 (no
// ranking_engine subscribed, or Redis down) carries the server's detail in the message. The
// request is aborted after SET_RANKING_MODE_TIMEOUT_MS: the page disables its mode buttons while a
// switch is in flight, so a request stalled in transit (SSH tunnel) must fail, not hang them.
export type RankingMode = "volume" | "volatility";

// Well above data_api's own 2 s Redis connect/publish bound, so a slow-but-alive server still answers.
const SET_RANKING_MODE_TIMEOUT_MS = 10_000;

export async function setRankingMode(mode: RankingMode): Promise<RankingModeResponse> {
  const res = await fetch("/api/rankings/mode", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ mode }),
    signal: AbortSignal.timeout(SET_RANKING_MODE_TIMEOUT_MS),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new HttpError(res.status, `PUT /api/rankings/mode failed: ${res.status} ${detail}`);
  }
  return (await res.json()) as RankingModeResponse;
}

// Story 15.3: cursor-paginated candle history (AD-F3) -- `useCandles` calls this once for
// the initial window and once per scroll-back refill, never for a full-range load.
export async function fetchCandles(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
  // Optional: the session pager aborts a page superseded by a newer request (DW-153).
  signal?: AbortSignal,
): Promise<CandlesResponse> {
  const params = new URLSearchParams({
    before_ns: String(beforeNs),
    limit: String(limit),
    bar_seconds: String(barSeconds),
  });
  const url = `/api/candles/${encodeURIComponent(instrumentId)}?${params}`;
  const res = await (signal ? fetch(url, { signal }) : fetch(url));
  if (!res.ok) throw new HttpError(res.status, `GET /api/candles/${instrumentId} failed: ${res.status}`);
  return (await res.json()) as CandlesResponse;
}

// Story 32.8: the volume footprint of the chart's closed bars, on fetchCandles()'s cursor contract
// plus `row_ticks` (price ticks per row; 0 = the server's per-bar auto size). Every price and size in
// the answer is integer units, formatted by `lib/units.ts` at the response's precisions.
export async function fetchFootprint(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
  rowTicks: number,
): Promise<FootprintResponse> {
  const params = new URLSearchParams({
    before_ns: String(beforeNs),
    limit: String(limit),
    bar_seconds: String(barSeconds),
    row_ticks: rowTicks === 0 ? "auto" : String(rowTicks),
  });
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/footprint?${params}`);
  if (!res.ok) throw new HttpError(res.status, `GET /api/coin/${instrumentId}/footprint failed: ${res.status}`);
  return (await res.json()) as FootprintResponse;
}

// Story 33.5: the derivatives read models (Story 33.4, `data_api/routes/derivatives.py`), on
// fetchCandles()'s cursor contract. Bucketed pages (open interest, mark/index, liquidation bars) take
// `bar_seconds`; event pages (funding, liquidations) do not. Each URL is written inline, so
// `data_api/tests/test_frontend_contract.py` checks it against the served routes.
function cursorParams(beforeNs: number, limit: number, barSeconds?: number): URLSearchParams {
  const params = new URLSearchParams({ before_ns: String(beforeNs), limit: String(limit) });
  if (barSeconds !== undefined) params.set("bar_seconds", String(barSeconds));
  return params;
}

async function readPage<T>(res: Response, route: string, instrumentId: string): Promise<T> {
  if (!res.ok) throw new HttpError(res.status, `GET /api/coin/${instrumentId}/${route} failed: ${res.status}`);
  return (await res.json()) as T;
}

export async function fetchFunding(instrumentId: string, beforeNs: number, limit: number): Promise<FundingResponse> {
  const params = cursorParams(beforeNs, limit);
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/funding?${params}`);
  return readPage<FundingResponse>(res, "funding", instrumentId);
}

export async function fetchOpenInterest(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
): Promise<OpenInterestResponse> {
  const params = cursorParams(beforeNs, limit, barSeconds);
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/open-interest?${params}`);
  return readPage<OpenInterestResponse>(res, "open-interest", instrumentId);
}

export async function fetchMarkIndex(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
): Promise<MarkIndexResponse> {
  const params = cursorParams(beforeNs, limit, barSeconds);
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/mark-index?${params}`);
  return readPage<MarkIndexResponse>(res, "mark-index", instrumentId);
}

export async function fetchLiquidations(instrumentId: string, beforeNs: number, limit: number): Promise<LiquidationsResponse> {
  const params = cursorParams(beforeNs, limit);
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/liquidations?${params}`);
  return readPage<LiquidationsResponse>(res, "liquidations", instrumentId);
}

export async function fetchLiquidationBars(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
): Promise<LiquidationBarsResponse> {
  const params = cursorParams(beforeNs, limit, barSeconds);
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/liquidation-bars?${params}`);
  return readPage<LiquidationBarsResponse>(res, "liquidation-bars", instrumentId);
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
// fetchCandles()'s before_ns/limit/bar_seconds contract, plus the
// caller-supplied `entries` (name+params) list of which picker-configured indicators to
// replay over the same bounded window.
export async function fetchIndicatorValues(
  instrumentId: string,
  beforeNs: number,
  limit: number,
  barSeconds: number,
  entries: { name: string; params: Record<string, unknown>; source?: string }[],
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

// Story 17.5: the Technicals tab's screener-wide column list -- one list for every row, a
// different persistence scope from the per-coin picker config above.
export async function fetchTechnicalsColumns(): Promise<TechnicalsColumn[]> {
  const res = await fetch("/api/rankings/technicals-columns");
  if (!res.ok) throw new Error(`GET /api/rankings/technicals-columns failed: ${res.status}`);
  return (await res.json()) as TechnicalsColumn[];
}

export async function saveTechnicalsColumns(entries: TechnicalsColumn[]): Promise<void> {
  const res = await fetch("/api/rankings/technicals-columns", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(entries),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(`PUT /api/rankings/technicals-columns failed: ${res.status} ${JSON.stringify(body)}`);
  }
}

// Latest value of every requested indicator for every ranked instrument, keyed
// instrument_id -> "{entry_index}.{output_attr}" -> value.
export async function fetchTechnicalsValues(
  entries: TechnicalsColumn[],
): Promise<TechnicalsValuesResponse["values"]> {
  const params = new URLSearchParams({
    entries: JSON.stringify(entries.map(({ name, params: p, bar_seconds }) => ({ name, params: p ?? {}, bar_seconds }))),
  });
  const res = await fetch(`/api/rankings/technicals-values?${params}`);
  if (!res.ok) throw new Error(`GET /api/rankings/technicals-values failed: ${res.status}`);
  return ((await res.json()) as TechnicalsValuesResponse).values;
}

// Stories 20.1/20.3: saved alerts (webhook delivery). `status` is derived server-side.
export async function fetchAlerts(): Promise<AlertResponse[]> {
  const res = await fetch("/api/alerts");
  if (!res.ok) throw new Error(`GET /api/alerts failed: ${res.status}`);
  return (await res.json()) as AlertResponse[];
}

export async function createAlert(body: AlertCreate): Promise<AlertResponse> {
  const res = await fetch("/api/alerts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST /api/alerts failed: ${res.status}`);
  return (await res.json()) as AlertResponse;
}

export async function deleteAlert(id: string): Promise<void> {
  const res = await fetch(`/api/alerts/${encodeURIComponent(id)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /api/alerts/${id} failed: ${res.status}`);
}

// Story 25.1b: the nightly-maintenance status the `archive` service publishes. A 503 means no
// status has reached data_api yet (the service is down or still starting) -- the caller shows
// that as "unavailable", never as "never ran".
export async function fetchArchiveStatus(): Promise<ArchiveStatusResponse> {
  const res = await fetch("/api/archive/status");
  if (!res.ok) throw new HttpError(res.status, `GET /api/archive/status failed: ${res.status}`);
  return (await res.json()) as ArchiveStatusResponse;
}

// Well above data_api's own 2 s Redis connect/publish bound (as SET_RANKING_MODE_TIMEOUT_MS).
const RUN_ARCHIVE_TIMEOUT_MS = 10_000;

// Story 25.1b: queue the full nightly sequence for `day` (null = yesterday). A 202 only means the
// scheduler received the command; the run shows up in the next archive status. A 422/503 carries
// the server's detail in the message.
export async function runArchiveNow(day: string | null): Promise<ArchiveRunResponse> {
  const res = await fetch("/api/archive/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ day }),
    signal: AbortSignal.timeout(RUN_ARCHIVE_TIMEOUT_MS),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    throw new HttpError(res.status, `POST /api/archive/run failed: ${res.status} ${detail}`);
  }
  return (await res.json()) as ArchiveRunResponse;
}

// Story 32.5: this coin's drawings (horizontal lines, trendlines, Fibonacci, positions), one
// server-side resource so they follow the operator to another browser. `[]` when nothing has
// been drawn yet (not an error).
export async function fetchCoinDrawings(instrumentId: string): Promise<Drawing[]> {
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/drawings`);
  if (!res.ok) throw new HttpError(res.status, `GET /api/coin/${instrumentId}/drawings failed: ${res.status}`);
  const body = (await res.json()) as DrawingsResponse;
  return parseDrawings(body.items);
}

/** The browser's cap on a `keepalive` request body (the Fetch spec's 64 KiB in-flight quota). */
export const KEEPALIVE_MAX_BYTES = 65_536;

// Story 32.5: replaces this coin's whole drawing list. `unloading` (a save started as the page goes
// away) asks for `keepalive`, so the request outlives the page; an ordinary save never does, so the
// browser's keepalive cap can't refuse it.
// Known limit: a body past `KEEPALIVE_MAX_BYTES` is sent without keepalive even on unload, so the
// browser may cancel it as the page goes (the server keeps the previous list). Upgrade path: chunk
// the list per drawing (a PUT per item) so every unload save fits the cap.
export async function saveCoinDrawings(
  instrumentId: string,
  items: readonly Drawing[],
  unloading = false,
): Promise<void> {
  const body = JSON.stringify({ items });
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/drawings`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body,
    keepalive: unloading && new TextEncoder().encode(body).length <= KEEPALIVE_MAX_BYTES,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new HttpError(res.status, `PUT /api/coin/${instrumentId}/drawings failed: ${res.status} ${JSON.stringify(body)}`);
  }
}

// Story 32.6: this coin's chart layout, one server-side resource per instrument. The OpenAPI schema
// types the body as a free-form object, so the raw layout comes back unparsed: the caller runs it
// through `normalizeLayout` (stale values fall back, never a blank chart). The GET seeds a coin
// opened for the first time from the default template (`seeded`).
export async function fetchCoinLayout(instrumentId: string): Promise<{ layout: unknown; seeded: boolean }> {
  const res = await fetch(`/api/coin/${encodeURIComponent(instrumentId)}/layout`);
  if (!res.ok) throw new HttpError(res.status, `GET /api/coin/${instrumentId}/layout failed: ${res.status}`);
  const body = (await res.json()) as { layout?: unknown; seeded?: unknown };
  return { layout: body.layout, seeded: body.seeded === true };
}

async function failLayoutRequest(method: string, path: string, res: Response): Promise<never> {
  const body = await res.json().catch(() => ({}));
  throw new HttpError(res.status, `${method} ${path} failed: ${res.status} ${JSON.stringify(body)}`);
}

// Replaces this coin's whole layout. `unloading` asks for `keepalive`, as `saveCoinDrawings` does
// (a layout is a few hundred bytes, far under the browser's keepalive cap).
export async function saveCoinLayout(instrumentId: string, layout: ChartLayout, unloading = false): Promise<void> {
  const path = `/api/coin/${encodeURIComponent(instrumentId)}/layout`;
  const res = await fetch(path, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ layout: layoutForSave(layout) }),
    keepalive: unloading,
  });
  if (!res.ok) await failLayoutRequest("PUT", path, res);
}

// Makes this coin's saved layout and indicator list the template every newly opened coin starts from.
export async function saveLayoutAsDefault(instrumentId: string): Promise<void> {
  const path = `/api/coin/${encodeURIComponent(instrumentId)}/layout/save-as-default`;
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) await failLayoutRequest("POST", path, res);
}

// Replaces this coin's layout and indicator list with the template; drawings are untouched.
// Returns the raw layout the server now holds for the coin.
export async function resetLayoutToDefault(instrumentId: string): Promise<unknown> {
  const path = `/api/coin/${encodeURIComponent(instrumentId)}/layout/reset-to-default`;
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) await failLayoutRequest("POST", path, res);
  return ((await res.json()) as { layout?: unknown }).layout;
}
