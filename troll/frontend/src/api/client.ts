// Thin typed fetch helper over the generated OpenAPI schema (AD-F5) -- proves the codegen
// pipeline's output is actually consumed, not just generated and ignored (Story 15.1 AC #3).
// Real data-fetching hooks (React Query) land in later Epic 15 stories; this is the pilot.
import type { HealthResponse } from "./schema";

export type { HealthResponse };

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`GET /api/health failed: ${res.status}`);
  return (await res.json()) as HealthResponse;
}
