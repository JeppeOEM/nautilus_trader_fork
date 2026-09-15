import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Story 15.5: this hook's socket lifecycle (subscribe-on-open, resubscribe-on-reconnect,
// unsubscribe-on-teardown) is the thing under test, so -- unlike RankingsPage.test.tsx,
// which mocks useLiveChannel at the module level -- a minimal hand-rolled fake
// WebSocket global is needed here instead (no existing pattern for this in the repo;
// this file establishes the first one, per the spec's own guidance). Kept deliberately
// small: just enough surface (onopen/onmessage/onclose/onerror/send/close/readyState)
// for the hook's own code, driven manually from each test via open()/receive()/close().
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readyState: number = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sent: string[] = [];
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }

  // --- test-only driver methods, not part of the real WebSocket API ---
  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

function latestSocket(): FakeWebSocket {
  const socket = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!socket) throw new Error("no FakeWebSocket instance was created");
  return socket;
}

function candleMessage(overrides: { channel?: string; t?: number } = {}) {
  return {
    channel: overrides.channel ?? "candles:BTC-USD-PERP.DYDX:60",
    bar: { t: overrides.t ?? 60_000, o: 1, h: 2, l: 0.5, c: 1.5, v: 10 },
  };
}

const { useLiveCandle } = await import("./useLiveCandle");

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useLiveCandle", () => {
  it("sends a subscribe control message for candles:{iid}:{barSeconds} on open", () => {
    renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));

    act(() => latestSocket().open());

    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: "candles:BTC-USD-PERP.DYDX:60" })]);
  });

  it("updates the returned bar when a matching channel message arrives", () => {
    const { result } = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());

    act(() => latestSocket().receive(candleMessage()));

    expect(result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 });
  });

  it("ignores a message for a channel it did not subscribe to", () => {
    const { result } = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());

    act(() => latestSocket().receive(candleMessage({ channel: "candles:ETH-USD-PERP.DYDX:60" })));

    expect(result.current).toBeNull();
  });

  it("does not crash and keeps previous state on a malformed frame", () => {
    const { result } = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(candleMessage()));

    act(() => latestSocket().onmessage?.({ data: "not json" }));

    expect(result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5 });
  });

  it(
    "clears the live bar and unsubscribes the old channel synchronously when barSeconds changes " +
      "(AC #4/#5)",
    () => {
      const { result, rerender } = renderHook(({ bar }) => useLiveCandle("BTC-USD-PERP.DYDX", bar), {
        initialProps: { bar: 60 },
      });
      act(() => latestSocket().open());
      act(() => latestSocket().receive(candleMessage()));
      expect(result.current).not.toBeNull();
      const oldSocket = latestSocket();

      rerender({ bar: 300 });

      // Cleared before the new subscription's socket is even created, let alone before
      // any message on it could arrive -- proven here by asserting immediately after
      // rerender(), with no intervening open()/receive().
      expect(result.current).toBeNull();
      expect(oldSocket.sent).toContain(JSON.stringify({ unsubscribe: "candles:BTC-USD-PERP.DYDX:60" }));
    },
  );

  it("subscribes to the new channel (not the old one) after barSeconds changes", () => {
    const { rerender } = renderHook(({ bar }) => useLiveCandle("BTC-USD-PERP.DYDX", bar), {
      initialProps: { bar: 60 },
    });
    act(() => latestSocket().open());

    rerender({ bar: 300 });
    act(() => latestSocket().open());

    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: "candles:BTC-USD-PERP.DYDX:300" })]);
  });

  it("a stray message on the old channel arriving after resubscribe is ignored", () => {
    const { result, rerender } = renderHook(({ bar }) => useLiveCandle("BTC-USD-PERP.DYDX", bar), {
      initialProps: { bar: 60 },
    });
    act(() => latestSocket().open());
    const oldSocket = latestSocket();

    rerender({ bar: 300 });
    act(() => latestSocket().open());

    // A message for the now-stale 60s channel racing in on the (now-closed) old socket
    // reference must never render as the current bar.
    act(() => oldSocket.receive(candleMessage({ channel: "candles:BTC-USD-PERP.DYDX:60" })));

    expect(result.current).toBeNull();
  });

  it("reconnects with backoff and resubscribes after the socket closes unexpectedly", () => {
    vi.useFakeTimers();
    renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      latestSocket().close();
      vi.advanceTimersByTime(1000); // RECONNECT_BASE_MS * attempt(1)
    });
    expect(FakeWebSocket.instances).toHaveLength(2);

    act(() => latestSocket().open());
    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: "candles:BTC-USD-PERP.DYDX:60" })]);
  });

  it("does not reconnect after unmount", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());

    unmount();
    vi.advanceTimersByTime(10_000);

    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
