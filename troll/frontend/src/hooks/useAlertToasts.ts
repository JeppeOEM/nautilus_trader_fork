import { useEffect, useState } from "react";

// Same reconnect constants as useLiveCandle.ts/useLiveChannel.ts (sibling hook, own socket).
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 10_000;
const TOAST_MS = 10_000;

export interface AlertToast {
  id: string;
  message: string;
}

/** `{"channel": "alerts", "alert": {"id", "message"}}` -- pushed by `data_api/alerts.py` on fire. */
function parseToast(data: string): AlertToast | null {
  try {
    const parsed: unknown = JSON.parse(data);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { channel, alert } = parsed as { channel?: unknown; alert?: { id?: unknown; message?: unknown } };
    if (channel !== "alerts" || !alert) return null;
    if (typeof alert.id !== "string" || typeof alert.message !== "string") return null;
    return { id: alert.id, message: alert.message };
  } catch {
    return null; // malformed frame -- ignore, same as the sibling live hooks
  }
}

/** Alerts fire server-side; this only surfaces them. Each toast auto-dismisses after 10s. */
export function useAlertToasts(): AlertToast[] {
  const [toasts, setToasts] = useState<AlertToast[]>([]);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const dismissTimers: ReturnType<typeof setTimeout>[] = [];
    let cancelled = false;
    let attempt = 0;

    function connect(): void {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/live`);
      socket.onopen = () => {
        attempt = 0;
      };
      socket.onmessage = (event: MessageEvent<string>) => {
        const toast = parseToast(event.data);
        if (cancelled || !toast) return;
        // Same alert can fire again (once-per-bar): key by arrival, not alert id.
        const key = `${toast.id}:${Date.now()}`;
        setToasts((current) => [...current, { ...toast, id: key }]);
        dismissTimers.push(
          setTimeout(() => setToasts((current) => current.filter((t) => t.id !== key)), TOAST_MS),
        );
      };
      socket.onclose = () => {
        if (cancelled) return;
        attempt += 1;
        reconnectTimer = setTimeout(connect, Math.min(RECONNECT_BASE_MS * attempt, RECONNECT_MAX_MS));
      };
      socket.onerror = () => socket?.close();
    }

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      dismissTimers.forEach(clearTimeout);
      socket?.close();
    };
  }, []);

  return toasts;
}
