import { useEffect, useRef, useState } from "react";

import { openLiveSubscription } from "./liveSubscription";

export type DerivsKind = "mark" | "index" | "funding" | "oi";

const KINDS: ReadonlySet<string> = new Set<DerivsKind>(["mark", "index", "funding", "oi"]);

/**
 * One `derivs:{iid}` frame of `/ws/live` (Story 33.4, `views/live_derivs.py`): a
 * `kernel/derivs_wire.py` row without its `instrument_id` (the channel names it). `value` stays the
 * exact decimal text the archive holds -- never parsed to a float here; a display formats it through
 * `lib/units.ts` at the edge. `interval` (seconds) and `next_funding_ns` are on funding rows only,
 * either possibly null. A WS-only convention, hand-written rather than codegen'd (like
 * `useLiveCandle`'s message).
 *
 * Known limit: `t`/`ts_init`/`next_funding_ns` are nanoseconds parsed by `JSON.parse` into a double,
 * exact to ~256 ns at today's epoch -- far finer than any display. Upgrade path: send them as text.
 */
export interface LiveDerivsTick {
  kind: DerivsKind;
  t: number;
  ts_init: number;
  value: string;
  interval?: number | null;
  next_funding_ns?: number | null;
  /** Story 33.5: a funding frame's annualised rate, computed by the server
   * (`kernel.indicators.funding_annualised`); null without an interval. */
  annualised?: number | null;
  /** Story 33.5: a mark or index frame's mark-index basis in bps, computed by the server from its
   * latest mark and index (`kernel.indicators.basis_bps`); null while either is unknown. */
  basis_mi_bps?: number | null;
}

function isNullableNumber(value: unknown): boolean {
  return value === undefined || value === null || Number.isFinite(value);
}

export function isLiveDerivsTick(message: Record<string, unknown>): boolean {
  return (
    typeof message.kind === "string" &&
    KINDS.has(message.kind) &&
    Number.isFinite(message.t) &&
    Number.isFinite(message.ts_init) &&
    typeof message.value === "string" &&
    isNullableNumber(message.interval) &&
    isNullableNumber(message.next_funding_ns) &&
    isNullableNumber(message.annualised) &&
    isNullableNumber(message.basis_mi_bps)
  );
}

function toTick(message: Record<string, unknown>): LiveDerivsTick {
  const tick: LiveDerivsTick = {
    kind: message.kind as DerivsKind,
    t: message.t as number,
    ts_init: message.ts_init as number,
    value: message.value as string,
  };
  if (tick.kind === "funding") {
    tick.interval = (message.interval as number | null | undefined) ?? null;
    tick.next_funding_ns = (message.next_funding_ns as number | null | undefined) ?? null;
    tick.annualised = (message.annualised as number | null | undefined) ?? null;
  }
  if (tick.kind === "mark" || tick.kind === "index") {
    tick.basis_mi_bps = (message.basis_mi_bps as number | null | undefined) ?? null;
  }
  return tick;
}

/** The newest tick of each kind; a kind not seen yet is absent, never a fabricated 0. */
export type LiveDerivs = Partial<Record<DerivsKind, LiveDerivsTick>>;

export interface LiveDerivsHandlers {
  /** Every tick, in arrival order -- a pane appending a series uses this, not the batched state. */
  onTick?: (tick: LiveDerivsTick) => void;
  /** The socket re-opened after a drop: ticks published meanwhile were missed and must be refetched. */
  onReconnect?: () => void;
}

/**
 * Subscribes `derivs:{instrumentId}` on its own `/ws/live` socket (`openLiveSubscription`) and
 * returns the newest mark, index, funding and open-interest tick, reset synchronously when
 * `instrumentId` changes. A malformed frame or a foreign channel's frame is ignored. A spot id
 * simply never receives a frame (capture publishes no derivatives for spot); an empty
 * `instrumentId` opens no socket at all.
 */
export function useLiveDerivs(instrumentId: string, handlers: LiveDerivsHandlers = {}): LiveDerivs {
  const [latest, setLatest] = useState<LiveDerivs>({});
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    // Synchronous reset before the new socket can deliver (see useLiveCandle's reset).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLatest({});
    // No instrument yet (a page still resolving its id): no socket, never a `"derivs:"` subscribe
    // the server can only refuse.
    if (!instrumentId) return;
    return openLiveSubscription(`derivs:${instrumentId}`, {
      onMessage: (message) => {
        if (!isLiveDerivsTick(message)) return;
        const tick = toTick(message);
        handlersRef.current.onTick?.(tick);
        setLatest((previous) => ({ ...previous, [tick.kind]: tick }));
      },
      onReconnect: () => handlersRef.current.onReconnect?.(),
    });
  }, [instrumentId]);

  return latest;
}
