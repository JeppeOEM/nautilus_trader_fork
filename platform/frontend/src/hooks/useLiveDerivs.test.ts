import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FakeWebSocket, latestSocket } from "../test/fakeWebSocket";

// Story 33.4: the `derivs:{iid}` channel of /ws/live (views/live_derivs.py's relay of
// kernel/derivs_wire.py rows, the instrument id dropped: the channel names it).
const IID = "BTCUSDT-LINEAR.BYBIT";
const CHANNEL = `derivs:${IID}`;

function funding(overrides: Record<string, unknown> = {}) {
  return {
    channel: CHANNEL,
    kind: "funding",
    t: 1_759_700_000_000,
    ts_init: 1_759_700_000_005,
    value: "0.0001",
    interval: 28_800,
    next_funding_ns: 1_759_708_800_000,
    ...overrides,
  };
}

function mark(value = "100.50") {
  return { channel: CHANNEL, kind: "mark", t: 1, ts_init: 2, value };
}

const { useLiveDerivs } = await import("./useLiveDerivs");

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useLiveDerivs", () => {
  it("opens no socket and subscribes nothing without an instrument id", () => {
    renderHook(() => useLiveDerivs(""));
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("subscribes derivs:{iid} on open", () => {
    renderHook(() => useLiveDerivs(IID));
    act(() => latestSocket().open());
    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: CHANNEL })]);
  });

  it("keeps the newest tick of each kind, the value as exact text", () => {
    const { result } = renderHook(() => useLiveDerivs(IID));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(funding()));
    act(() => latestSocket().receive(mark("100.40")));
    act(() => latestSocket().receive(mark("100.50")));

    expect(result.current).toEqual({
      funding: {
        kind: "funding",
        t: 1_759_700_000_000,
        ts_init: 1_759_700_000_005,
        value: "0.0001",
        interval: 28_800,
        next_funding_ns: 1_759_708_800_000,
      },
      mark: { kind: "mark", t: 1, ts_init: 2, value: "100.50" },
    });
  });

  it("hands every tick to onTick in arrival order", () => {
    const onTick = vi.fn();
    renderHook(() => useLiveDerivs(IID, { onTick }));
    act(() => latestSocket().open());
    act(() => {
      latestSocket().receive(mark("1"));
      latestSocket().receive(mark("2"));
    });
    expect(onTick.mock.calls.map(([tick]) => tick.value)).toEqual(["1", "2"]);
  });

  it("reads a funding row without an interval as null, never a default", () => {
    const { result } = renderHook(() => useLiveDerivs(IID));
    act(() => latestSocket().open());
    act(() => latestSocket().receive(funding({ interval: null, next_funding_ns: null })));
    expect([result.current.funding?.interval, result.current.funding?.next_funding_ns]).toEqual([null, null]);
  });

  it("ignores a foreign channel's frame and malformed rows", () => {
    const { result } = renderHook(() => useLiveDerivs(IID));
    act(() => latestSocket().open());
    act(() => {
      latestSocket().receive({ ...mark(), channel: "derivs:ETHUSDT-LINEAR.BYBIT" });
      latestSocket().receive({ ...mark(), kind: "basis" });
      latestSocket().receive({ ...mark(), value: 100.5 }); // a float is never the exact value
      latestSocket().receive({ ...mark(), t: "1" });
      latestSocket().receive(funding({ interval: "8h" }));
      latestSocket().onmessage?.({ data: "not json" });
    });
    expect(result.current).toEqual({});
  });

  it("resets and unsubscribes the old channel when the instrument changes", () => {
    const { result, rerender } = renderHook(({ iid }) => useLiveDerivs(iid), { initialProps: { iid: IID } });
    act(() => latestSocket().open());
    act(() => latestSocket().receive(mark()));
    const oldSocket = latestSocket();

    rerender({ iid: "ETHUSDT-LINEAR.BYBIT" });

    expect(result.current).toEqual({});
    expect(oldSocket.sent).toContain(JSON.stringify({ unsubscribe: CHANNEL }));
    act(() => oldSocket.receive(mark())); // a frame racing in on the closed socket
    expect(result.current).toEqual({});
  });

  it("reconnects with backoff, resubscribes and reports the reconnect", () => {
    vi.useFakeTimers();
    const onReconnect = vi.fn();
    renderHook(() => useLiveDerivs(IID, { onReconnect }));
    act(() => latestSocket().open());

    act(() => {
      latestSocket().close();
      vi.advanceTimersByTime(1000);
    });
    act(() => latestSocket().open());

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: CHANNEL })]);
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });

  it("does not reconnect after unmount", () => {
    vi.useFakeTimers();
    const { unmount } = renderHook(() => useLiveDerivs(IID));
    act(() => latestSocket().open());
    unmount();
    vi.advanceTimersByTime(10_000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
