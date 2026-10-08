import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type Drawing, UnknownDrawingKindError } from "../lib/drawings";

const api = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchCoinDrawings: (...args: unknown[]) => api.fetch(...args),
  saveCoinDrawings: (...args: unknown[]) => api.save(...args),
}));

const { SAVE_DEBOUNCE_MS, SAVE_RETRY_MS, useChartDrawings } = await import("./useChartDrawings");

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

describe("useChartDrawings history (Story 33.10)", () => {
  const moved = (price: number) => (all: Drawing[]) =>
    all.map((d) => (d.kind === "trendline" ? { ...d, anchors: [d.anchors[0], { time: 2000, price }] as typeof d.anchors } : d));

  it("undoes and redoes edits, saving the restored list like any edit", async () => {
    vi.useFakeTimers();
    try {
      const hook = await loaded();
      api.save.mockResolvedValue(undefined);
      act(() => hook.result.current.setDrawings((all) => [...all, LINE]));
      act(() => hook.result.current.setDrawings(moved(300)));
      expect(hook.result.current).toMatchObject({ canUndo: true, canRedo: false });

      act(() => hook.result.current.undo());
      expect(hook.result.current.drawings).toEqual([LINE]);
      expect(hook.result.current.canRedo).toBe(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SAVE_DEBOUNCE_MS + 1);
      });
      expect(api.save.mock.calls.at(-1)?.[1]).toEqual([LINE]);

      act(() => hook.result.current.redo());
      expect((hook.result.current.drawings[0] as Extract<Drawing, { kind: "trendline" }>).anchors[1].price).toBe(300);
      hook.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("makes one drag gesture one undo step", async () => {
    const hook = await loaded();
    act(() => hook.result.current.setDrawings((all) => [...all, LINE]));
    for (const price of [210, 220, 230]) act(() => hook.result.current.setDrawings(moved(price), "drag:1"));
    act(() => hook.result.current.undo());
    expect(hook.result.current.drawings).toEqual([LINE]);
    hook.unmount();
  });

  it("starts with no history: the load is not undoable", async () => {
    api.fetch.mockResolvedValue([LINE]);
    const hook = renderHook(() => useChartDrawings(IID));
    await act(async () => {});
    expect(hook.result.current).toMatchObject({ drawings: [LINE], canUndo: false, canRedo: false });
    hook.unmount();
  });

  it("logs an unknown kind as drawings.unknown_kind, fails, never retries and never saves", async () => {
    vi.useFakeTimers();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      api.fetch.mockRejectedValue(new UnknownDrawingKindError("zigzag"));
      const hook = renderHook(() => useChartDrawings(IID));
      await act(async () => {});
      expect(hook.result.current.status).toBe("failed");
      expect(errors).toHaveBeenCalledTimes(1);
      expect(String(errors.mock.calls[0][0])).toMatch(/^drawings\.unknown_kind: unknown drawing kind "zigzag"/);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(SAVE_RETRY_MS * 3);
      });
      expect(api.fetch).toHaveBeenCalledTimes(1);
      hook.unmount();
      expect(api.save).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
      vi.useRealTimers();
    }
  });
});
