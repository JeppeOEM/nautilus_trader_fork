import { useCallback, useEffect, useRef, useState } from "react";

import { fetchCoinLayout, resetLayoutToDefault, saveCoinLayout, saveLayoutAsDefault } from "../api/client";
import { type ChartLayout, layoutKey, normalizeLayout, sameLayout } from "../lib/chartLayout";
import { TIMEFRAMES } from "../timeframes";
import { SAVE_DEBOUNCE_MS, SAVE_RETRY_MS, httpStatus } from "./useChartDrawings";

/** The pre-32.6 browser-local timeframe and volume choice of a coin (imported once, then removed). */
export function legacyTimeframeKey(instrumentId: string): string {
  return `chart-timeframe:${instrumentId}`;
}
export function legacyVolumeKey(instrumentId: string): string {
  return `chart-volume:${instrumentId}`;
}

interface LegacyChoice {
  bar_seconds?: number;
  volume?: boolean;
  /** Whether either key exists at all (a key with an unusable value is still removed). */
  present: boolean;
}

function readLegacy(instrumentId: string): LegacyChoice {
  try {
    const timeframe = localStorage.getItem(legacyTimeframeKey(instrumentId));
    const volume = localStorage.getItem(legacyVolumeKey(instrumentId));
    const seconds = Number(timeframe);
    return {
      present: timeframe !== null || volume !== null,
      bar_seconds: TIMEFRAMES.some((t) => t.seconds === seconds) ? seconds : undefined,
      volume: volume === "off" ? false : volume === "on" ? true : undefined,
    };
  } catch {
    return { present: false }; // blocked: nothing to import
  }
}

function removeLegacy(instrumentId: string): void {
  try {
    localStorage.removeItem(legacyTimeframeKey(instrumentId));
    localStorage.removeItem(legacyVolumeKey(instrumentId));
  } catch {
    // storage blocked: the keys stay and the import is simply tried again on the next open
  }
}

export type LayoutStatus = "loading" | "ready" | "failed";

export interface ChartLayoutStore {
  /** `null` until the server's layout has loaded: nothing renders (or fetches candles) before it. */
  layout: ChartLayout | null;
  status: LayoutStatus;
  /** Applies a change; one that leaves every field equal is dropped (no state change, no save). */
  update: (change: (prev: ChartLayout) => ChartLayout) => void;
  /** Why the latest save failed (shown to the operator), or null: the layout on screen is not saved. */
  saveError: string | null;
  /** Saves any pending edit, then makes this coin's saved layout and indicators the default. Rejects on failure. */
  saveAsDefault: () => Promise<void>;
  /** Replaces this coin's layout and indicators with the default and adopts the result. Rejects on failure. */
  resetToDefault: () => Promise<void>;
  /** Bumped with the layout a reset adopted (the same render), so the chart remounts once on it. */
  revision: number;
}

/**
 * A coin's chart layout (Story 32.6), restored from the server on mount and persisted on every
 * change, the same discipline as `useChartDrawings`: debounced to one PUT per burst, a failed save
 * retried (5xx / network) or held until the next edit (4xx), a save still pending flushed on
 * unmount and on `pagehide`, and nothing ever PUT before the first GET succeeded. `layout` is
 * `null` until then, so the page renders no chart (and issues no candle request) before it knows the
 * saved timeframe.
 *
 * The first load imports the browser's `chart-timeframe:{iid}` and `chart-volume:{iid}` once: on the
 * coin's first open (the server just seeded it) they override the seeded `bar_seconds` and `volume`
 * and the keys are removed after the first save that includes them landed (with nothing to import,
 * at once). A coin the server already holds a layout for (set up in another browser since) keeps
 * it: the stale browser keys are only removed. Known limit: an import whose save never landed (the
 * page closed while it was being retried) is not re-applied on the next open, the coin is no longer
 * seeded then; the keys are removed and the server's layout stands. Upgrade path: none needed, the
 * keys predate Story 32.6 and every browser drops them on its first open. Stale server values fall back
 * to the built-in default (`normalizeLayout`, one `console.error`) and the corrected layout is saved.
 *
 * Known limit: the PUT replaces the whole layout with no version, so two browsers editing one coin
 * overwrite each other (last write wins). Upgrade path: the version field named in
 * `useChartDrawings` and `data_api/routes/layout.py`.
 */
export function useChartLayout(instrumentId: string): ChartLayoutStore {
  const [layout, setLayout] = useState<ChartLayout | null>(null);
  const [status, setStatus] = useState<LayoutStatus>("loading");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  // The newest layout, written synchronously with every state change (never by an effect, which
  // would lag a commit behind the unmount and `pagehide` flushes that read it).
  const latestRef = useRef<ChartLayout | null>(null);
  // `layoutKey` of the layout last known to equal the server's; null = the server's differs.
  const savedRef = useRef<string | null>(null);
  const legacyPendingRef = useRef(false);
  const statusRef = useRef<LayoutStatus>("loading");
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  const queuedRef = useRef(0); // saves in flight or queued
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);
  const resettingRef = useRef(false);
  const unloadingRef = useRef(false); // `pagehide` fired and no `pageshow` since

  useEffect(() => {
    let cancelled = false;
    let reloadTimer: ReturnType<typeof setTimeout> | null = null;
    const load = (): void => {
      fetchCoinLayout(instrumentId)
        .then(({ layout: raw, seeded }) => {
          if (cancelled) return;
          const { layout: server, fallbacks } = normalizeLayout(raw);
          // Only a first open takes the browser's old choice; a layout already on the server wins.
          const found = readLegacy(instrumentId);
          const legacy: LegacyChoice = seeded ? found : { present: found.present };
          const loaded: ChartLayout = {
            ...server,
            bar_seconds: legacy.bar_seconds ?? server.bar_seconds,
            volume: legacy.volume ?? server.volume,
          };
          savedRef.current = fallbacks.length === 0 ? layoutKey(server) : null;
          // Removed with the first save that carries the import; with nothing to import, now.
          legacyPendingRef.current = legacy.present && !sameLayout(loaded, server);
          if (legacy.present && !legacyPendingRef.current) removeLegacy(instrumentId);
          latestRef.current = loaded;
          statusRef.current = "ready";
          setLayout(loaded);
          setStatus("ready");
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          console.error(`useChartLayout: failed to load the layout of ${instrumentId}`, err);
          statusRef.current = "failed";
          setStatus("failed");
          // A transient failure is retried while this coin's page lives; a 4xx would fail identically.
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

  const saveNow = useCallback(
    function saveNow(unloading = false): Promise<void> {
      if (retryTimer.current !== null) {
        clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
      if (statusRef.current !== "ready" || resettingRef.current) return Promise.resolve();
      const run = async (): Promise<void> => {
        const snapshot = latestRef.current;
        if (snapshot === null) return;
        const json = layoutKey(snapshot);
        if (json === savedRef.current) return;
        try {
          await saveCoinLayout(instrumentId, snapshot, unloading);
        } catch (err) {
          console.error(`useChartLayout: failed to save the layout of ${instrumentId}`, err);
          const code = httpStatus(err);
          const refused = code !== null && code >= 400 && code < 500;
          if (aliveRef.current) {
            setSaveError(
              refused
                ? `The layout was refused by the server (HTTP ${code}) and is not saved. Change the chart to try again.`
                : `The layout could not be saved; retrying every ${SAVE_RETRY_MS / 1000} s.`,
            );
            // A 4xx would fail identically: no retry until the next edit. No retry outliving the page.
            if (!refused) retryTimer.current = setTimeout(() => void saveNow().catch(() => {}), SAVE_RETRY_MS);
          }
          throw err;
        }
        savedRef.current = json;
        if (aliveRef.current) setSaveError(null);
        if (legacyPendingRef.current) {
          legacyPendingRef.current = false;
          removeLegacy(instrumentId);
        }
      };
      // A save in flight finishes first, so the newer layout is sent after it, in order. Only on
      // `pagehide` (the in-flight one may never settle) is the latest sent at once, with keepalive.
      //
      // Known limit: that `pagehide` PUT and the one in flight are two independent requests, so the
      // older can reach the server last and win. Upgrade path: the versioned PUT named above.
      if (unloading) return run();
      // Idle: the PUT starts now (an unmount's flush must not wait a microtask). Otherwise it queues.
      const idle = queuedRef.current === 0;
      queuedRef.current += 1;
      const next = (idle ? run() : chainRef.current.catch(() => undefined).then(run)).finally(() => {
        queuedRef.current -= 1;
      });
      chainRef.current = next;
      return next;
    },
    [instrumentId],
  );

  useEffect(() => {
    if (status !== "ready" || layout === null || layoutKey(layout) === savedRef.current) return;
    const timer = setTimeout(() => void saveNow().catch(() => {}), SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [layout, status, saveNow]);

  useEffect(() => {
    aliveRef.current = true;
    const flushOnUnload = (): void => {
      unloadingRef.current = true;
      void saveNow(true).catch(() => {});
    };
    const onShow = (): void => {
      unloadingRef.current = false; // restored from the back/forward cache: debounce again
    };
    window.addEventListener("pagehide", flushOnUnload);
    window.addEventListener("pageshow", onShow);
    return () => {
      window.removeEventListener("pagehide", flushOnUnload);
      window.removeEventListener("pageshow", onShow);
      void saveNow().catch(() => {}); // a save still pending when this coin's chart goes away
      aliveRef.current = false;
    };
  }, [saveNow]);

  // A change reported after this hook's own flush ran (a child chart's unmount cleanup runs after
  // its parent's, and its `pagehide` listener after this one's) is saved at once, not debounced:
  // no debounce outlives the page or this coin's chart.
  const update = useCallback(
    (change: (prev: ChartLayout) => ChartLayout): void => {
      const prev = latestRef.current;
      if (prev === null) return;
      const next = change(prev);
      if (sameLayout(prev, next)) return;
      latestRef.current = next;
      if (!aliveRef.current || unloadingRef.current) {
        void saveNow(unloadingRef.current).catch(() => {});
        return;
      }
      setLayout(next);
    },
    [saveNow],
  );

  const saveAsDefault = useCallback(async (): Promise<void> => {
    if (resettingRef.current) throw new Error("a reset to default is still running");
    await saveNow(); // the server copies what it holds, so a pending edit goes first
    await saveLayoutAsDefault(instrumentId);
  }, [instrumentId, saveNow]);

  const resetToDefault = useCallback(async (): Promise<void> => {
    resettingRef.current = true;
    try {
      await chainRef.current.catch(() => undefined);
      const { layout: fresh, fallbacks } = normalizeLayout(await resetLayoutToDefault(instrumentId));
      savedRef.current = fallbacks.length === 0 ? layoutKey(fresh) : null;
      latestRef.current = fresh;
      setLayout(fresh);
      setRevision((n) => n + 1);
      setSaveError(null);
      resettingRef.current = false;
    } catch (err) {
      resettingRef.current = false;
      void saveNow().catch(() => {}); // the debounce timers that fired meanwhile were held back
      throw err;
    }
  }, [instrumentId, saveNow]);

  return { layout, status, update, saveError, saveAsDefault, resetToDefault, revision };
}
