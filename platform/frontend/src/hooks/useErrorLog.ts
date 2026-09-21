import { useEffect, useSyncExternalStore } from "react";

// DATA-07: no error may go unseen. Every `console.error` in the app (all the `.catch(err =>
// console.error(...))` sites), every uncaught error / unhandled rejection, and every failure the
// backend recorded in its error ledger (`GET /api/errors`) lands here and is shown by <ErrorBar>.
// There is deliberately no dismiss button: the bar clears only when the page is reloaded.

export interface ErrorLogState {
  frontend: number;
  lastFrontend: string;
  backend: Record<string, number>;
  lastBackend: Record<string, string>;
}

const EMPTY: ErrorLogState = { frontend: 0, lastFrontend: "", backend: {}, lastBackend: {} };
let state: ErrorLogState = EMPTY;
const listeners = new Set<() => void>();
let installed = false;

function set(next: Partial<ErrorLogState>): void {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

function describe(args: unknown[]): string {
  return args
    .map((a) => (a instanceof Error ? `${a.name}: ${a.message}` : typeof a === "string" ? a : JSON.stringify(a)))
    .join(" ")
    .slice(0, 300);
}

export function recordFrontendError(message: string): void {
  set({ frontend: state.frontend + 1, lastFrontend: message });
}

/** Idempotent: wraps console.error (still calling the original) and hooks global error events. */
export function installErrorCapture(): void {
  if (installed) return;
  installed = true;
  const original = console.error.bind(console);
  console.error = (...args: unknown[]) => {
    original(...args);
    recordFrontendError(describe(args));
  };
  window.addEventListener("error", (e) => recordFrontendError(e.message));
  window.addEventListener("unhandledrejection", (e) => recordFrontendError(describe([e.reason])));
}

const BACKEND_POLL_MS = 30_000;

async function pollBackend(): Promise<void> {
  try {
    const res = await fetch("/api/errors");
    if (!res.ok) throw new Error(`GET /api/errors failed: ${res.status}`);
    const body = (await res.json()) as { counts: Record<string, number>; last: Record<string, string> };
    set({ backend: body.counts, lastBackend: body.last });
  } catch (err) {
    // An unreachable backend is itself an error to show, not something to swallow.
    console.error("error bar: cannot read backend error ledger", err);
  }
}

export function useErrorLog(): ErrorLogState {
  useEffect(() => {
    installErrorCapture();
    void pollBackend();
    const id = setInterval(() => void pollBackend(), BACKEND_POLL_MS);
    return () => clearInterval(id);
  }, []);
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

/** Tests only. */
export function resetErrorLog(): void {
  state = EMPTY;
  listeners.forEach((l) => l());
}
