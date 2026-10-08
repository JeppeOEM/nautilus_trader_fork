import type { CandlestickData, Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { openLiveSubscription } from "./liveSubscription";

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
  bar: {
    t: number;
    o: number;
    h: number;
    l: number;
    c: number;
    v: number;
    // Story 33.3's per-bar order flow and liquidation aggregates, appended to the frozen six keys
    // (`candles.application.forming.forming_bar`): integer units at the row's precisions, null
    // where unknown (no flow, no liquidation feed). Optional: a server before 33.3 sends none.
    buy_v?: number | null;
    sell_v?: number | null;
    buy_n?: number | null;
    sell_n?: number | null;
    pv?: number | null;
    liq_long_v?: number | null;
    liq_short_v?: number | null;
    liq_n?: number | null;
    price_precision?: number | null;
    size_precision?: number | null;
  };
}

function isLiveCandleMessage(message: Record<string, unknown>): message is Record<string, unknown> & LiveCandleMessage {
  if (typeof message.channel !== "string" || typeof message.bar !== "object" || message.bar === null) {
    return false;
  }
  const bar = message.bar as Record<string, unknown>;
  return ["t", "o", "h", "l", "c"].every((k) => Number.isFinite(bar[k]));
}

/** The forming bar plus its bucket volume (`bar.v`), so the volume pane can follow it too, and its
 * buy/sell volume in integer units (Story 33.6: the Volume pane's `delta` colour; null = unknown). */
export type LiveBar = CandlestickData<Time> & { volume: number; buy_v?: number | null; sell_v?: number | null };

const unitsOrNull = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);

function toChartDatum(bar: LiveCandleMessage["bar"]): LiveBar {
  const time = (bar.t / 1000) as UTCTimestamp; // wire is ms, lightweight-charts wants seconds
  return {
    time,
    open: bar.o,
    high: bar.h,
    low: bar.l,
    close: bar.c,
    volume: Number.isFinite(bar.v) ? bar.v : 0,
    buy_v: unitsOrNull(bar.buy_v),
    sell_v: unitsOrNull(bar.sell_v),
  };
}

export interface LiveCandleHandlers {
  /** The socket re-opened after a drop: bars published meanwhile were missed and must be refetched. */
  onReconnect?: () => void;
  /** A newer bucket's bar arrived, so the previous forming bar is closed and final. */
  onBarClosed?: (bar: LiveBar) => void;
}

/**
 * Opens its own dedicated `/ws/live` WebSocket (`openLiveSubscription`, sibling to
 * `useLiveChannel`, never shared -- see spec-15-5's Design Notes) and tracks the currently-forming
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
export function useLiveCandle(
  instrumentId: string,
  barSeconds: number,
  handlers: LiveCandleHandlers = {},
): LiveBar | null {
  const [liveBar, setLiveBar] = useState<LiveBar | null>(null);
  // Latest-handlers ref: callers pass fresh closures every render, and re-running the socket
  // effect for that would drop and reopen the connection.
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    let currentBar: LiveBar | null = null;
    // Synchronous reset is the point (AC #4/#5): this effect is synchronizing with an
    // external system (the WS socket for instrumentId/barSeconds), and the reset must
    // land before that system's own connect() below can possibly deliver a message --
    // deriving this during render isn't an option, there is no render-time input to
    // derive "no live bar yet" from other than the change this effect is reacting to.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLiveBar(null);

    // The subscription drops every frame of another channel (rankings:live, another
    // instrument/bar_seconds) and every frame racing in after teardown, so a stale bar from the
    // old channel can never render as the current one.
    return openLiveSubscription(`candles:${instrumentId}:${barSeconds}`, {
      onMessage: (message) => {
        if (!isLiveCandleMessage(message)) return;
        const bar = toChartDatum(message.bar);
        if (currentBar && bar.time > currentBar.time) handlersRef.current.onBarClosed?.(currentBar);
        currentBar = bar;
        setLiveBar(bar);
      },
      onReconnect: () => handlersRef.current.onReconnect?.(),
    });
  }, [instrumentId, barSeconds]);

  return liveBar;
}
