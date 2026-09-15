// Thin typed fetch helper over the generated OpenAPI schema (AD-F5) -- proves the codegen
// pipeline's output is actually consumed, not just generated and ignored (Story 15.1 AC #3).
import type { CandlesResponse, HealthResponse, RankingsResponse } from "./schema";

export type { CandlesResponse, HealthResponse, RankingsResponse };

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
