import { useCallback, useEffect, useRef, useState } from "react";

import { fetchWatchlist, saveWatchlist } from "../api/client";

// Story 33.12: the chart watchlist -- the operator's pinned chart instruments, a UI-only list (not the
// Collection Plan nor a Coin Ranking, DATA_DICTIONARY §2.20). One server-side list
// (`GET`/`PUT /api/watchlist`, `chart_watchlist.toml`), never localStorage, so every browser shows
// the same pins.

export interface ChartWatchlist {
  instruments: readonly string[];
  /** True once a GET succeeded: before it the list is unknown, and a pin (a whole-list PUT) would
   * replace every stored pin with the new one alone -- so pin and unpin wait for it. */
  loaded: boolean;
  /** True while the last GET failed and a retry is scheduled (the pin toggle says so). */
  loadFailed: boolean;
  error: string | null;
  pin: (instrumentId: string) => void;
  unpin: (instrumentId: string) => void;
}

/** The waits before each retry of a failed GET; the last one repeats until a GET succeeds. */
export const WATCHLIST_RETRY_MS: readonly number[] = [2000, 5000, 10000, 30000];

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function retryDelay(attempt: number): number {
  return WATCHLIST_RETRY_MS[Math.min(attempt, WATCHLIST_RETRY_MS.length - 1)];
}

export function useWatchlist(): ChartWatchlist {
  const [instruments, setInstruments] = useState<readonly string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Every GET and every edit takes the next number; a response applies only while it is the latest,
  // so a slow GET never overwrites a later edit, and only the newest save's answer is shown.
  const latestRequest = useRef(0);
  // The list the next edit starts from: the latest one sent, not the last render's, so two pins in one
  // tick both land.
  const currentRef = useRef<readonly string[]>([]);
  // The list the server last confirmed (a GET's or a PUT's answer): a failed save shows it again.
  const storedRef = useRef<readonly string[]>([]);
  // The PUTs run one after another: each waits for the previous one, so two quick edits reach the
  // server in the order they were made and the stored list is the last edit's.
  const saveChain = useRef<Promise<void>>(Promise.resolve());
  const retryTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mounted = useRef(true);

  const reload = useCallback((): void => {
    // A named inner function, so a failed GET can schedule its own retry with the next wait.
    const load = (attempt: number): void => {
      clearTimeout(retryTimer.current);
      const request = ++latestRequest.current;
      fetchWatchlist()
        .then((response) => {
          if (request !== latestRequest.current) return;
          storedRef.current = response.instruments;
          currentRef.current = response.instruments;
          setInstruments(response.instruments);
          setLoaded(true);
          setLoadError(null);
          // A save failure stays shown over the reloaded list until the next edit clears it.
        })
        .catch((err: unknown) => {
          console.error("ChartPage: failed to load the chart watchlist", err);
          if (!mounted.current || request !== latestRequest.current) return;
          const wait = retryDelay(attempt);
          setLoadError(`Watchlist could not be loaded: ${message(err)} (retrying in ${wait / 1000} s)`);
          retryTimer.current = setTimeout(() => load(attempt + 1), wait);
        });
    };
    load(0);
  }, []);
  useEffect(() => {
    mounted.current = true;
    reload();
    return () => {
      mounted.current = false;
      clearTimeout(retryTimer.current);
    };
  }, [reload]);

  const persist = useCallback(
    (next: readonly string[]): void => {
      const request = ++latestRequest.current;
      // A retry of a failed GET still pending would take a newer request number than this edit and
      // paint the server's list from before this PUT over it (and the next edit would build on that
      // list, dropping this one): the PUT's own answer is the server's list, so it replaces the retry.
      clearTimeout(retryTimer.current);
      currentRef.current = next;
      setInstruments(next); // shown at once; the server's answer replaces it
      setSaveError(null);
      saveChain.current = saveChain.current
        .then(() => saveWatchlist([...next]))
        .then((stored) => {
          // The PUTs run in order, so every answer is the server's newest list, superseded or not.
          storedRef.current = stored.instruments;
          if (request !== latestRequest.current) return;
          currentRef.current = stored.instruments;
          setInstruments(stored.instruments);
          setLoadError(null);
        })
        .catch((err: unknown) => {
          console.error("ChartPage: failed to save the chart watchlist", err);
          // A later edit is queued: it carries this one's list too and its own answer decides what
          // shows, so reloading here would paint the server's older list over it.
          if (!mounted.current || request !== latestRequest.current) return;
          // The pin the screen showed was not stored: say so and show the last stored list at once, so an
          // edit made before the reload lands builds on it and never stores the failed one.
          currentRef.current = storedRef.current;
          setInstruments(storedRef.current);
          setSaveError(`Watchlist could not be saved: ${message(err)}`);
          reload();
        });
    },
    [reload],
  );

  const pin = useCallback(
    (instrumentId: string): void => {
      if (!loaded || currentRef.current.includes(instrumentId)) return;
      persist([...currentRef.current, instrumentId]);
    },
    [loaded, persist],
  );
  const unpin = useCallback(
    (instrumentId: string): void => {
      if (!loaded || !currentRef.current.includes(instrumentId)) return;
      persist(currentRef.current.filter((iid) => iid !== instrumentId));
    },
    [loaded, persist],
  );

  return { instruments, loaded, loadFailed: loadError !== null, error: saveError ?? loadError, pin, unpin };
}
