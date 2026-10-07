import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Drawing } from "../lib/drawings";

const api = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchCoinDrawings: (...args: unknown[]) => api.fetch(...args),
  saveCoinDrawings: (...args: unknown[]) => api.save(...args),
}));

const { useChartDrawings } = await import("./useChartDrawings");

const IID = "BTCUSDT-LINEAR.BYBIT";
const LINE: Drawing = {
  id: "trendline-1",
  kind: "trendline",
  anchors: [
    { time: 1000, price: 100 },
    { time: 2000, price: 200 },
  ],
} as Drawing;

async function loaded() {
  api.fetch.mockResolvedValue([]);
  const hook = renderHook(() => useChartDrawings(IID));
  await act(async () => {});
  expect(hook.result.current.status).toBe("ready");
  return hook;
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useChartDrawings saveNow (Story 33.8)", () => {
  it("saves a just-drawn line at once, without waiting for the debounce", async () => {
    const hook = await loaded();
    api.save.mockResolvedValue(undefined);
    act(() => hook.result.current.setDrawings((all) => [...all, LINE]));
    await act(async () => {
      await hook.result.current.saveNow();
    });
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][1]).toEqual([LINE]);
    await act(async () => {
      await hook.result.current.saveNow(); // already saved: nothing is sent again
    });
    expect(api.save).toHaveBeenCalledTimes(1);
    hook.unmount();
  });

  it("waits for a save in flight and rejects when the list cannot be saved", async () => {
    const hook = await loaded();
    api.save.mockRejectedValue(Object.assign(new Error("HTTP 503"), { status: 503 }));
    act(() => hook.result.current.setDrawings((all) => [...all, LINE]));
    let failure: unknown = null;
    await act(async () => {
      await hook.result.current.saveNow().catch((err: unknown) => {
        failure = err;
      });
    });
    expect(failure).toBeInstanceOf(Error);
    expect((failure as Error).message).toBe("HTTP 503");
    expect(hook.result.current.saveError).not.toBeNull();
    hook.unmount();
  });
});
