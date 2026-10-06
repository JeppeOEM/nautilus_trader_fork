import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FakeWebSocket, latestSocket } from "../test/fakeWebSocket";

// Story 33.4: the `liquidations:{iid}` channel of /ws/live: `{"channel", "liq": Liquidation.to_dict}`.
const IID = "BTCUSDT-LINEAR.BYBIT";
const CHANNEL = `liquidations:${IID}`;

function liq(overrides: Record<string, unknown> = {}) {
  return {
    instrument_id: IID,
    side: "long",
    size_units: 41,
    price_units: 8_513_850,
    price_precision: 2,
    size_precision: 3,
    venue_event_id: "1759700000000:Buy:0.041:85138.50",
    ts_event: 1_759_700_000_000,
    ts_init: 1_759_700_000_005,
    ...overrides,
  };
}

const { useLiveLiquidations } = await import("./useLiveLiquidations");

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useLiveLiquidations", () => {
  it("opens no socket and subscribes nothing without an instrument id", () => {
    renderHook(() => useLiveLiquidations(""));
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("subscribes liquidations:{iid} on open", () => {
    renderHook(() => useLiveLiquidations(IID));
    act(() => latestSocket().open());
    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: CHANNEL })]);
  });

  it("returns the newest liquidation, its integer units untouched", () => {
    const { result } = renderHook(() => useLiveLiquidations(IID));
    act(() => latestSocket().open());
    act(() => latestSocket().receive({ channel: CHANNEL, liq: liq() }));
    expect(result.current).toEqual(liq());
  });

  it("hands every row of a cascade to onLiquidation, never collapsed", () => {
    const onLiquidation = vi.fn();
    renderHook(() => useLiveLiquidations(IID, { onLiquidation }));
    act(() => latestSocket().open());
    act(() => {
      latestSocket().receive({ channel: CHANNEL, liq: liq({ venue_event_id: "a" }) });
      latestSocket().receive({ channel: CHANNEL, liq: liq({ venue_event_id: "b", side: "short" }) });
    });
    expect(onLiquidation.mock.calls.map(([row]) => [row.venue_event_id, row.side])).toEqual([
      ["a", "long"],
      ["b", "short"],
    ]);
  });

  it("ignores a foreign channel's frame and malformed rows", () => {
    const { result } = renderHook(() => useLiveLiquidations(IID));
    act(() => latestSocket().open());
    act(() => {
      latestSocket().receive({ channel: "liquidations:ETHUSDT-LINEAR.BYBIT", liq: liq() });
      latestSocket().receive({ channel: CHANNEL, liq: liq({ side: "sideways" }) });
      latestSocket().receive({ channel: CHANNEL, liq: liq({ size_units: 0.041 }) }); // units are integers
      latestSocket().receive({ channel: CHANNEL, liq: null });
      latestSocket().receive({ channel: CHANNEL });
      latestSocket().onmessage?.({ data: "not json" });
    });
    expect(result.current).toBeNull();
  });

  it("resets and unsubscribes the old channel when the instrument changes", () => {
    const { result, rerender } = renderHook(({ iid }) => useLiveLiquidations(iid), {
      initialProps: { iid: IID },
    });
    act(() => latestSocket().open());
    act(() => latestSocket().receive({ channel: CHANNEL, liq: liq() }));
    const oldSocket = latestSocket();

    rerender({ iid: "ETHUSDT-LINEAR.BYBIT" });

    expect(result.current).toBeNull();
    expect(oldSocket.sent).toContain(JSON.stringify({ unsubscribe: CHANNEL }));
    act(() => oldSocket.receive({ channel: CHANNEL, liq: liq() })); // a frame racing in on the closed socket
    expect(result.current).toBeNull();
  });

  it("reconnects with backoff, resubscribes and reports the reconnect", () => {
    vi.useFakeTimers();
    const onReconnect = vi.fn();
    renderHook(() => useLiveLiquidations(IID, { onReconnect }));
    act(() => latestSocket().open());

    act(() => {
      latestSocket().close();
      vi.advanceTimersByTime(1000);
    });
    act(() => latestSocket().open());

    expect(latestSocket().sent).toEqual([JSON.stringify({ subscribe: CHANNEL })]);
    expect(onReconnect).toHaveBeenCalledTimes(1);
  });
});
