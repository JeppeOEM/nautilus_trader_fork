import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { HttpError, runArchiveNow } from "../api/client";
import type { ArchiveRun, ArchiveStatusResponse } from "../api/client";
import {
  ARCHIVE_STATUS_QUERY_KEY,
  archiveOutcome,
  nonZeroSteps,
  shortUtc,
  useArchiveStatus,
} from "../hooks/useArchiveStatus";

const OUTCOME_COLOR = {
  ok: "var(--color-positive)",
  findings: "var(--color-warn)",
  FAILED: "var(--color-danger)",
} as const;

function RunVerdict({ label, run }: { label: string; run: ArchiveRun }) {
  const outcome = archiveOutcome(run);
  const detail = outcome === "ok" ? "" : ` (${nonZeroSteps(run)})`;
  return (
    <span>
      {label} {run.day ?? run.days?.join(",") ?? "?"}{" "}
      <span style={{ color: OUTCOME_COLOR[outcome] }}>
        {outcome}
        {detail}
      </span>
      {run.finished && <> · {shortUtc(run.finished)}</>}
    </span>
  );
}

function StatusText({ status }: { status: ArchiveStatusResponse }) {
  const { running, last_run: lastRun, last_intraday: lastIntraday } = status;
  // An intraday merge that went fine is routine; one that did not must not hide behind the nightly.
  const showIntraday = lastIntraday && archiveOutcome(lastIntraday) !== "ok";
  return (
    <>
      {running ? (
        <span style={{ color: "var(--color-active)" }}>
          running {running.kind} {running.day ?? running.days?.join(",") ?? ""} (step{" "}
          {running.steps.length + 1})…
        </span>
      ) : lastRun ? (
        <RunVerdict label="last" run={lastRun} />
      ) : (
        <span>no run yet</span>
      )}
      {showIntraday && (
        <>
          {" · "}
          <RunVerdict label="intraday" run={lastIntraday} />
        </>
      )}
      <> · next {shortUtc(status.next_run)}</>
      {status.backup === "disabled" && (
        <span
          style={{ color: "var(--color-warn)" }}
          title="Off-site backup is disabled (backup_enabled = false in archive/config.toml): the catalog has no copy off this host."
        >
          {" · backup off"}
        </span>
      )}
    </>
  );
}

function statusError(error: Error): string {
  if (error instanceof HttpError && error.status === 503) return "status unavailable (archive service not reporting)";
  return `status unavailable: ${error.message}`;
}

interface ConfirmRunDialogProps {
  open: boolean;
  onClose: () => void;
}

function ConfirmRunDialog({ open, onClose }: ConfirmRunDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      // showModal gives Esc-to-close, a backdrop and focus trapping natively; jsdom lacks it.
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    } else if (!open && dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
  }, [open]);

  function close(): void {
    setError(null);
    onClose();
  }

  async function confirm(): Promise<void> {
    setError(null);
    setPending(true);
    try {
      await runArchiveNow(null);
      void queryClient.invalidateQueries({ queryKey: ARCHIVE_STATUS_QUERY_KEY });
      close();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to queue the maintenance run.");
    } finally {
      setPending(false);
    }
  }

  return (
    <dialog ref={ref} aria-label="Run maintenance now" onClose={close}>
      <h3>Run nightly maintenance now?</h3>
      <p>
        Queues the full nightly sequence for yesterday (every venue, then consolidate, then the off-site
        backup if it is enabled). It starts after any job already running.
      </p>
      {error && (
        <p role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
      <div>
        <button type="button" disabled={pending} onClick={() => void confirm()}>
          Run
        </button>
        <button type="button" onClick={close}>
          Cancel
        </button>
      </div>
    </dialog>
  );
}

/**
 * Compact nightly-maintenance status (Story 25.1b): last run + outcome, next run, run-now, and
 * "backup off" when the off-site backup is disabled (Story 26.1b).
 */
export default function ArchiveStatus() {
  const { data, error } = useArchiveStatus();
  const [confirmOpen, setConfirmOpen] = useState(false);
  return (
    <span aria-label="Maintenance status" style={{ fontSize: "0.85em" }}>
      maintenance:{" "}
      {data ? (
        <StatusText status={data} />
      ) : error ? (
        <span style={{ color: "var(--color-warn)" }}>{statusError(error)}</span>
      ) : (
        <span>…</span>
      )}{" "}
      <button type="button" onClick={() => setConfirmOpen(true)}>
        Run now
      </button>
      <ConfirmRunDialog open={confirmOpen} onClose={() => setConfirmOpen(false)} />
    </span>
  );
}
