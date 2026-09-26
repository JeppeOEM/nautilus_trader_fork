import { useQuery } from "@tanstack/react-query";

import { fetchArchiveStatus } from "../api/client";
import type { ArchiveRun, ArchiveStatusResponse } from "../api/client";

// Story 25.1b: the `archive` service republishes its status after every step and on a 30 s
// heartbeat, so polling faster than that shows nothing new.
export const ARCHIVE_STATUS_POLL_MS = 30_000;

export const ARCHIVE_STATUS_QUERY_KEY = ["archive-status"] as const;

/** Latest `GET /api/archive/status`, polled. A 503 surfaces as `error` (status unavailable). */
export function useArchiveStatus() {
  return useQuery<ArchiveStatusResponse, Error>({
    queryKey: ARCHIVE_STATUS_QUERY_KEY,
    queryFn: fetchArchiveStatus,
    refetchInterval: ARCHIVE_STATUS_POLL_MS,
    retry: false,
  });
}

export type ArchiveOutcome = "ok" | "findings" | "FAILED";

/**
 * A run's verdict from its steps' exit codes, the archive tools' convention: 0 is clean, 2 is
 * "ran and reported findings" (e.g. a reconcile mismatch), anything else is a failure.
 */
export function archiveOutcome(run: ArchiveRun): ArchiveOutcome {
  const exits = run.steps.map((s) => s.exit);
  if (exits.every((e) => e === 0)) return "ok";
  if (exits.every((e) => e === 0 || e === 2)) return "findings";
  return "FAILED";
}

/** The non-zero steps, e.g. "BYBIT reconcile=2, backup=1". */
export function nonZeroSteps(run: ArchiveRun): string {
  return run.steps
    .filter((s) => s.exit !== 0)
    .map((s) => `${s.venue ? `${s.venue} ` : ""}${s.name}=${s.exit}`)
    .join(", ");
}

/** "2026-09-27T03:07:00Z" -> "2026-09-27 03:07Z"; an unparseable value is shown as sent. */
export function shortUtc(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")}Z`;
}
