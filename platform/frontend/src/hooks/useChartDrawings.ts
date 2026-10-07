import { useCallback, useEffect, useRef, useState } from "react";

import { fetchCoinDrawings, saveCoinDrawings } from "../api/client";
import { type Drawing, importLegacyHlines } from "../lib/drawings";

/** One PUT per burst of edits: a drag changes the drawing on every mouse move. */
export const SAVE_DEBOUNCE_MS = 600;
/** A failed save (or load) is retried at this interval while the page lives. */
export const SAVE_RETRY_MS = 5000;

/** The pre-32.5 browser-local horizontal lines of a coin (imported once, then removed). */
export function legacyHlineKey(instrumentId: string): string {
  return `chart-hlines:${instrumentId}`;
}

function readLegacyHlines(instrumentId: string): unknown {
  try {
    const text = localStorage.getItem(legacyHlineKey(instrumentId));
    return text === null ? undefined : JSON.parse(text);
  } catch {
    return undefined; // blocked or not JSON: nothing to import
  }
}

function removeLegacyHlines(instrumentId: string): void {
  try {
    localStorage.removeItem(legacyHlineKey(instrumentId));
  } catch {
    // storage blocked: the key stays and the (price-deduplicated) import is simply tried again
  }
}

/** An HTTP status carried by a failed request (`HttpError`), read without importing the class. */
export function httpStatus(err: unknown): number | null {
  const status = (err as { status?: unknown } | null)?.status;
  return typeof status === "number" ? status : null;
}

export type DrawingsStatus = "loading" | "ready" | "failed";

export interface ChartDrawings {
  drawings: Drawing[];
  setDrawings: (update: (all: Drawing[]) => Drawing[]) => void;
  /** Edits are only allowed once the server's list has loaded, or a save would overwrite it. */
  status: DrawingsStatus;
  /** Why the latest save failed (shown to the operator), or null: the drawings on screen are not saved. */
  saveError: string | null;
  /**
   * Story 33.8: save the drawings on screen now, skipping the debounce, and resolve once the server
   * holds them (rejects when they cannot be saved). A trendline alert is checked against the saved
   * file, so its dialog awaits this before creating it.
   */
  saveNow: () => Promise<void>;
}

/**
 * A coin's drawings, restored from the server on mount and persisted on every change (Story 32.5):
 * debounced to one PUT per burst, a failure retried, and a save still pending when the chart goes
 * away flushed on unmount and on `pagehide`. Nothing is ever PUT before the first GET succeeded
 * (an empty client list must never overwrite the server's).
 *
 * The first load after Story 32.5 imports the browser's `chart-hlines:{iid}` once: its lines
 * join the loaded list (a line at a price the server already has is skipped, so a repeated
 * import cannot duplicate) and the key is removed after the first save that includes them landed.
 *
 * A failed save is never silent: `saveError` is set until a save lands. A network error or 5xx is
 * retried every `SAVE_RETRY_MS` while the page lives; a 4xx (the server refused the list: retrying
 * the same body can only fail again) stops until the next edit. After unmount or `pagehide`
 * nothing is retried.
 *
 * Known limit: the PUT replaces the whole list with no version, so two browsers editing one coin
 * overwrite each other (last write wins). Upgrade path: a version field on the GET, echoed by the
 * PUT, a 409 on a mismatch and a client-side merge (see `data_api/routes/drawings.py`).
 */
export function useChartDrawings(instrumentId: string): ChartDrawings {
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const [status, setStatus] = useState<DrawingsStatus>("loading");
  const [saveError, setSaveError] = useState<string | null>(null);
  const latestRef = useRef<Drawing[]>(drawings);
  // Declared before the effects below, so it is current when they (and the unmount flush) read it.
  useEffect(() => {
    latestRef.current = drawings;
  }, [drawings]);
  // The array last known to equal the server's list: reference-compared with the current one.
  const savedRef = useRef<Drawing[] | null>(null);
  const legacyPendingRef = useRef(false);
  const savingRef = useRef(false);
  // The save in flight, settling (after its bookkeeping) to null when it landed or to its error.
  const inflightRef = useRef<Promise<unknown> | null>(null);
  const statusRef = useRef<DrawingsStatus>("loading");
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    let cancelled = false;
    let reloadTimer: ReturnType<typeof setTimeout> | null = null;
    const load = (): void => {
      fetchCoinDrawings(instrumentId)
        .then((server) => {
          if (cancelled) return;
          let loaded = server;
          const legacy = readLegacyHlines(instrumentId);
          if (legacy !== undefined) {
            const known = new Set(server.flatMap((d) => (d.kind === "hline" ? [d.price] : [])));
            // Deduplicated against the server's lines and within the imported set itself.
            const imported = importLegacyHlines(legacy, server).filter((line) => {
              if (known.has(line.price)) return false;
              known.add(line.price);
              return true;
            });
            if (imported.length > 0) loaded = [...server, ...imported];
            // Removed with the first save that carries them; with nothing to import, now.
            legacyPendingRef.current = imported.length > 0;
            if (imported.length === 0) removeLegacyHlines(instrumentId);
          }
          savedRef.current = server;
          statusRef.current = "ready";
          setDrawings(loaded);
          setStatus("ready");
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          console.error(`useChartDrawings: failed to load the drawings of ${instrumentId}`, err);
          statusRef.current = "failed";
          setStatus("failed");
          // A transient failure (data_api restarting) must not leave the chart without its drawings
          // until a reload: the load is retried while this coin's chart lives. A 4xx (a malformed
          // id) would fail identically, so it is not.
          const code = httpStatus(err);
          if (code === null || code >= 500) reloadTimer = setTimeout(load, SAVE_RETRY_MS);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (reloadTimer !== null) clearTimeout(reloadTimer);
    };
  }, [instrumentId]);

  const flush = useCallback(function flush(unloading = false): void {
    if (retryTimer.current !== null) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    // A save in flight defers this one to its `finally`, which sends the newer list after it, in
    // order (an unmount too: the JS context lives on). Only on `pagehide` may that `finally` never
    // run, so the latest list is sent now (keepalive).
    //
    // Known limit: that `pagehide` PUT and the one in flight are two independent requests, so the
    // older can reach the server last and win. Upgrade path: the versioned PUT named above (a 409
    // refuses the stale one).
    if (statusRef.current !== "ready" || (savingRef.current && !unloading)) return;
    const snapshot = latestRef.current;
    if (snapshot === savedRef.current) return;
    savingRef.current = true;
    inflightRef.current = saveCoinDrawings(instrumentId, snapshot, unloading)
      .then(() => {
        savedRef.current = snapshot;
        if (aliveRef.current) setSaveError(null);
        if (legacyPendingRef.current) {
          legacyPendingRef.current = false;
          removeLegacyHlines(instrumentId);
        }
        return null;
      })
      .catch((err: unknown) => {
        console.error(`useChartDrawings: failed to save the drawings of ${instrumentId}`, err);
        const code = httpStatus(err);
        const refused = code !== null && code >= 400 && code < 500;
        if (aliveRef.current) {
          setSaveError(
            refused
              ? `Drawings were refused by the server (HTTP ${code}) and are not saved. Edit a drawing to try again.`
              : `Drawings could not be saved; retrying every ${SAVE_RETRY_MS / 1000} s.`,
          );
          // A 4xx would fail identically: no retry until the next edit. No retry outliving the page.
          if (!refused) retryTimer.current = setTimeout(() => flush(), SAVE_RETRY_MS);
        }
        return err;
      })
      .finally(() => {
        savingRef.current = false;
        // Edited while this PUT was in flight: the newer list is saved right after, in order. Only
        // an edit triggers this, never the failure itself, so a failing save cannot loop.
        if (latestRef.current !== snapshot && latestRef.current !== savedRef.current) flush();
      });
  }, [instrumentId]);

  useEffect(() => {
    if (status !== "ready" || drawings === savedRef.current) return;
    if (retryTimer.current !== null) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    const timer = setTimeout(() => flush(), SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [drawings, status, flush]);

  useEffect(() => {
    aliveRef.current = true;
    const flushOnUnload = (): void => flush(true);
    window.addEventListener("pagehide", flushOnUnload);
    return () => {
      window.removeEventListener("pagehide", flushOnUnload);
      flush(); // a save still pending when this coin's chart goes away (after any in flight)
      aliveRef.current = false;
    };
  }, [flush]);

  const saveNow = useCallback(
    async function saveNow(): Promise<void> {
      // Each pass either waits out the save in flight (its `finally` may chain a newer one) or
      // starts one for the unsaved list; it returns once the list on screen is the saved one.
      for (;;) {
        if (savingRef.current && inflightRef.current) {
          await inflightRef.current;
          continue;
        }
        if (statusRef.current !== "ready") throw new Error("This coin's drawings have not loaded yet.");
        if (latestRef.current === savedRef.current) return;
        flush();
        const failure = await inflightRef.current;
        if (failure) throw failure instanceof Error ? failure : new Error(String(failure));
      }
    },
    [flush],
  );

  const update = useCallback((fn: (all: Drawing[]) => Drawing[]): void => setDrawings(fn), []);
  return { drawings, setDrawings: update, status, saveError, saveNow };
}
