import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FakeWebSocket, latestSocket } from "../test/fakeWebSocket";

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

    expect(result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10, buy_v: null, sell_v: null });
  });

  it("parses a bar carrying Story 33.3's order-flow keys to the same candle, plus its buy/sell volume", () => {
    // AD-D12: the /ws/live bar only gains keys (appended after t,o,h,l,c,v); the chart reads only
    // buy_v/sell_v of them (Story 33.6's Volume colour), the candle itself is unchanged.
    const withFlow = {
      ...candleMessage(),
      bar: {
        ...candleMessage().bar,
        buy_v: 6000,
        sell_v: 4000,
        buy_n: 3,
        sell_n: 2,
        pv: 15_000_000,
        liq_long_v: null,
        liq_short_v: null,
        liq_n: null,
        price_precision: 1,
        size_precision: 3,
      },
    };
    const plain = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(candleMessage()));
    const flow = renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(withFlow));

    expect(plain.result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10, buy_v: null, sell_v: null });
    expect(flow.result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10, buy_v: 6000, sell_v: 4000 });
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

    expect(result.current).toEqual({ time: 60, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10, buy_v: null, sell_v: null });
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

describe("useLiveCandle handlers", () => {
  it("reports the closed bar when a newer bucket's bar arrives, not on same-bucket updates", () => {
    const onBarClosed = vi.fn();
    renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60, { onBarClosed }));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(candleMessage({ t: 60_000 })));
    act(() => latestSocket().receive(candleMessage({ t: 60_000 })));
    expect(onBarClosed).not.toHaveBeenCalled();

    act(() => latestSocket().receive(candleMessage({ t: 120_000 })));

    expect(onBarClosed).toHaveBeenCalledTimes(1);
    expect(onBarClosed).toHaveBeenCalledWith(expect.objectContaining({ time: 60, close: 1.5, volume: 10 }));
  });

  it("calls onReconnect only on a re-open after a drop", () => {
    vi.useFakeTimers();
    const onReconnect = vi.fn();
    renderHook(() => useLiveCandle("BTC-USD-PERP.DYDX", 60, { onReconnect }));
    act(() => latestSocket().open());
    expect(onReconnect).not.toHaveBeenCalled();

    act(() => latestSocket().close());
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    act(() => latestSocket().open());

    expect(onReconnect).toHaveBeenCalledTimes(1);
  });
});
