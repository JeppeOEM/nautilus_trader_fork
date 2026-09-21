import { useEffect, useRef, useState } from "react";

// Reconnect-with-backoff, client-side analog of the backend's own reconnect-forever
// discipline (redis_bus.py's RankingsBus.run(), itself mirroring dashboard.py's
// _redis_listener). Backoff grows linearly per attempt, capped -- no reason to hammer
// data_api every second if it's genuinely down for a while.
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10_000;

export interface LiveChannelState<T> {
  /** Most recently received, successfully-parsed message. `null` before the first one. */
  latest: T | null;
  /** Whether the WebSocket is currently open. */
  connected: boolean;
}

/**
 * Opens one WebSocket to `/ws/live` and exposes the latest parsed message plus
 * connection state. Reconnects on close with backoff, forever -- never gives up and
 * never falls back to REST polling (epics AC2/AC3; platform/CLAUDE.md "no polling fallback
 * once /ws/live is connected").
 *
 * Generic over the message type `T` -- Story 15.2's only consumer passes the
 * `rankings:live` shape verbatim (see RankingsPage.tsx), but the hook itself has no
 * rankings-specific logic, so a later story reusing `/ws/live` for another relayed
 * channel doesn't need a second hook.
 */
export function useLiveChannel<T>(): LiveChannelState<T> {
  const [latest, setLatest] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const attemptRef = useRef(0);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    // Defer the first connect by one tick so React StrictMode's dev-only
    // double-invoke (mount -> cleanup -> mount) never opens a real socket for the
    // throwaway first mount -- `cancelled` is already true by the time this runs, so
    // it's skipped. Without this, the fake mount's socket gets proxied mid-handshake
    // through Vite's /ws proxy and torn down before the write finishes, logging an
    // EPIPE stack trace vite has no public API to suppress. No effect on the real
    // (second) mount, which schedules and fires normally.
    const startTimer = setTimeout(() => {
      if (!cancelled) connect();
    }, 0);

    function connect(): void {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/live`);

      socket.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          setLatest(JSON.parse(event.data) as T);
        } catch {
          // Malformed frame -- ignore, keep the previous state. Mirrors the backend's
          // own "malformed payload is logged and skipped, previous cache kept" rule
          // (redis_bus.py's RankingsBus.handle_message) -- the frontend should never
          // crash or blank the table over one bad frame either.
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        const attempt = attemptRef.current + 1;
        attemptRef.current = attempt;
        const delay = Math.min(RECONNECT_BASE_MS * attempt, RECONNECT_MAX_MS);
        reconnectTimer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        // Let onclose (which always fires after onerror for a WebSocket) own the
        // reconnect scheduling -- just make sure the socket actually closes.
        socket?.close();
      };
    }

    return () => {
      cancelled = true;
      clearTimeout(startTimer);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  return { latest, connected };
}
