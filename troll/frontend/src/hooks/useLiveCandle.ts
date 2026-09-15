import type { CandlestickData, Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useState } from "react";

import type { ChartDatum } from "./useCandles";

// Reconnect-with-backoff constants -- deliberately duplicated from useLiveChannel.ts
// rather than imported: this hook is a sibling implementation with its own dedicated
// socket, not a shared/rewritten version of useLiveChannel (see spec-15-5's Design
// Notes for why one hook per connection type, not one hook multiplexing both).
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10_000;

/**
 * `{"channel": "candles:{iid}:{bar_seconds}", "bar": {...}}` -- the live-candle WS
 * message shape. This is this story's own invention (`data_api/ws/live.py`'s
 * `_parse_candle_channel`/`LiveCandleBus`), not a Redis wire format and not generated
 * from the OpenAPI schema -- REST responses go through the codegen'd `api/schema.ts`
 * types (AD-F5), but this is a WS-only control-plane convention the frontend and
 * backend agree on directly, so it is hand-written here instead.
 */
interface LiveCandleMessage {
  channel: string;
  bar: { t: number; o: number; h: number; l: number; c: number; v: number };
}

function isLiveCandleMessage(value: unknown): value is LiveCandleMessage {
  if (typeof value !== "object" || value === null) return false;
  const message = value as Record<string, unknown>;
  return typeof message.channel === "string" && typeof message.bar === "object" && message.bar !== null;
}

function toChartDatum(bar: LiveCandleMessage["bar"]): CandlestickData<Time> {
  const time = (bar.t / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  return { time, open: bar.o, high: bar.h, low: bar.l, close: bar.c };
}

/**
 * Opens its own dedicated `/ws/live` WebSocket (sibling to `useLiveChannel`, never
 * shared -- see spec-15-5's Design Notes) and tracks the currently-forming
 * `(instrumentId, barSeconds)` bar. Sends `{"subscribe": "candles:{iid}:{bar_seconds}"}`
 * on every open (initial connect and every reconnect) and `{"unsubscribe": ...}` for the
 * previous channel whenever `instrumentId`/`barSeconds` changes or the hook unmounts.
 *
 * The returned bar is reset to `null` synchronously at the top of the effect -- before
 * the new subscription's socket is even opened -- whenever `instrumentId`/`barSeconds`
 * changes (AC #4/#5). `ChartPage.tsx`'s `key={iid}` remount already covers the
 * `instrumentId` case for free by unmounting this hook entirely; the explicit reset here
 * is what covers a `barSeconds` change on an otherwise-stable mount.
 */
export function useLiveCandle(instrumentId: string, barSeconds: number): ChartDatum | null {
  const [liveBar, setLiveBar] = useState<ChartDatum | null>(null);

  useEffect(() => {
    // Synchronous reset is the point (AC #4/#5): this effect is synchronizing with an
    // external system (the WS socket for instrumentId/barSeconds), and the reset must
    // land before that system's own connect() below can possibly deliver a message --
    // deriving this during render isn't an option, there is no render-time input to
    // derive "no live bar yet" from other than the change this effect is reacting to.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLiveBar(null);

    const channel = `candles:${instrumentId}:${barSeconds}`;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    let attempt = 0;

    function connect(): void {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/live`);

      socket.onopen = () => {
        attempt = 0;
        socket?.send(JSON.stringify({ subscribe: channel }));
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        // A message arriving on a socket this effect has already torn down (e.g. a
        // stray frame racing in on the old socket right after instrumentId/barSeconds
        // changes) must never update state -- `channel` alone isn't enough to guard
        // this, since a message for that same old channel can legitimately still be in
        // flight on the wire at teardown time.
        if (cancelled) return;
        try {
          const parsed: unknown = JSON.parse(event.data);
          // Filtered to this hook's current channel -- ignores every other relayed
          // message (rankings:live, another instrument/bar_seconds' candle channel, or
          // a stray old-channel message racing in during a resubscribe).
          if (isLiveCandleMessage(parsed) && parsed.channel === channel) {
            setLiveBar(toChartDatum(parsed.bar));
          }
        } catch {
          // Malformed frame -- ignore, keep previous state (mirrors useLiveChannel.ts).
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        attempt += 1;
        const delay = Math.min(RECONNECT_BASE_MS * attempt, RECONNECT_MAX_MS);
        reconnectTimer = setTimeout(connect, delay);
      };

      socket.onerror = () => {
        // Let onclose (which always fires after onerror for a WebSocket) own the
        // reconnect scheduling -- just make sure the socket actually closes.
        socket?.close();
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ unsubscribe: channel }));
      }
      socket?.close();
    };
  }, [instrumentId, barSeconds]);

  return liveBar;
}
