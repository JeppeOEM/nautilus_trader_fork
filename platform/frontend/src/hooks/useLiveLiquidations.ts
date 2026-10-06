import { useEffect, useRef, useState } from "react";

import { openLiveSubscription } from "./liveSubscription";

/**
 * One liquidation as `/ws/live` relays it on `liquidations:{iid}` (Story 33.4): the
 * `kernel/liquidation.py` `Liquidation.to_dict` row verbatim. `side` is the liquidated position
 * (`long` = a forced sell), sizes and prices are integer units at the row's own precisions, and the
 * price is Bybit's bankruptcy price, not the fill -- format through `lib/units.ts`, never by guessing
 * a precision. Nanosecond timestamps are doubles here (see `useLiveDerivs`' Known limit).
 */
export interface LiveLiquidation {
  instrument_id: string;
  side: "long" | "short";
  size_units: number;
  price_units: number;
  price_precision: number;
  size_precision: number;
  venue_event_id: string;
  ts_event: number;
  ts_init: number;
}

const INTEGER_KEYS = ["size_units", "price_units", "price_precision", "size_precision", "ts_event", "ts_init"];

export function isLiveLiquidation(value: unknown): value is LiveLiquidation {
  if (typeof value !== "object" || value === null) return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.instrument_id === "string" &&
    (row.side === "long" || row.side === "short") &&
    typeof row.venue_event_id === "string" &&
    INTEGER_KEYS.every((key) => Number.isInteger(row[key]))
  );
}

export interface LiveLiquidationHandlers {
  /** Every liquidation, in arrival order -- a cascade is many frames, never collapsed into one. */
  onLiquidation?: (row: LiveLiquidation) => void;
  /** The socket re-opened after a drop: liquidations published meanwhile were missed. */
  onReconnect?: () => void;
}

/**
 * Subscribes `liquidations:{instrumentId}` on its own `/ws/live` socket (`openLiveSubscription`) and
 * returns the newest liquidation (null before the first, and again right after `instrumentId`
 * changes). Every row also reaches `onLiquidation`, since React batches state updates and a cascade
 * delivers many rows at once. A malformed or foreign frame is ignored. Only Bybit linear ids have the
 * feed, so any other id simply never receives a frame; an empty `instrumentId` opens no socket.
 */
export function useLiveLiquidations(
  instrumentId: string,
  handlers: LiveLiquidationHandlers = {},
): LiveLiquidation | null {
  const [latest, setLatest] = useState<LiveLiquidation | null>(null);
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    // Synchronous reset before the new socket can deliver (see useLiveCandle's reset).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLatest(null);
    // No instrument yet (a page still resolving its id): no socket, never a `"liquidations:"` subscribe
    // the server can only refuse.
    if (!instrumentId) return;
    return openLiveSubscription(`liquidations:${instrumentId}`, {
      onMessage: (message) => {
        if (!isLiveLiquidation(message.liq)) return;
        const row = message.liq;
        handlersRef.current.onLiquidation?.(row);
        setLatest(row);
      },
      onReconnect: () => handlersRef.current.onReconnect?.(),
    });
  }, [instrumentId]);

  return latest;
}
