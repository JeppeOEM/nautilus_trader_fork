// Reconnect-with-backoff constants -- deliberately not imported from useLiveChannel.ts: a
// per-channel subscription is a sibling implementation with its own dedicated socket, not a
// shared/rewritten version of useLiveChannel (see spec-15-5's Design Notes for why one socket per
// connection type, not one multiplexing both).
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10_000;

export interface LiveSubscriptionHandlers {
  /** One parsed frame whose `channel` is exactly the subscribed one; validating the rest is the caller's. */
  onMessage: (message: Record<string, unknown>) => void;
  /** The socket re-opened after a drop: frames published meanwhile were missed. */
  onReconnect?: () => void;
}

/**
 * Opens one dedicated `/ws/live` WebSocket subscribed to `channel` -- the socket lifecycle shared by
 * `useLiveCandle`, `useLiveDerivs` and `useLiveLiquidations` (Story 33.4 extracted it from
 * `useLiveCandle`). Sends `{"subscribe": channel}` on every open (initial connect and every
 * reconnect), reconnects with a linear capped backoff after an unexpected close, hands
 * `onMessage` only well-formed object frames of this channel, and ignores every other frame
 * (rankings, another channel, non-JSON). Returns the teardown: it sends `{"unsubscribe": channel}`
 * if the socket is open, closes it, and guarantees no handler runs afterwards -- a frame racing
 * in on the closed socket is dropped, since `channel` alone cannot tell it from a live one.
 */
export function openLiveSubscription(channel: string, handlers: LiveSubscriptionHandlers): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let cancelled = false;
  let attempt = 0;

  function onFrame(data: string): void {
    if (cancelled) return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return; // malformed frame: ignore, keep the caller's previous state (mirrors useLiveChannel.ts)
    }
    if (typeof parsed !== "object" || parsed === null) return;
    const message = parsed as Record<string, unknown>;
    if (message.channel === channel) handlers.onMessage(message);
  }

  function connect(): void {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws/live`);

    socket.onopen = () => {
      const reconnected = attempt > 0; // onclose counted at least one drop before this open
      attempt = 0;
      socket?.send(JSON.stringify({ subscribe: channel }));
      if (reconnected) handlers.onReconnect?.();
    };

    socket.onmessage = (event: MessageEvent<string>) => onFrame(event.data);

    socket.onclose = () => {
      if (cancelled) return;
      attempt += 1;
      const delay = Math.min(RECONNECT_BASE_MS * attempt, RECONNECT_MAX_MS);
      reconnectTimer = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      // Let onclose (which always fires after onerror for a WebSocket) own the reconnect
      // scheduling -- just make sure the socket actually closes.
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
}
