// Thin typed fetch helper over the generated OpenAPI schema (AD-F5) -- proves the codegen
// pipeline's output is actually consumed, not just generated and ignored (Story 15.1 AC #3).
import type { HealthResponse, RankingsResponse } from "./schema";

export type { HealthResponse, RankingsResponse };

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
