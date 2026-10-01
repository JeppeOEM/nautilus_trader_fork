import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BUILT_IN_LAYOUT, type ChartLayout } from "../lib/chartLayout";

const api = vi.hoisted(() => ({
  fetch: vi.fn(),
  save: vi.fn(),
  saveDefault: vi.fn(),
  reset: vi.fn(),
}));
vi.mock("../api/client", () => ({
  fetchCoinLayout: (...args: unknown[]) => api.fetch(...args),
  saveCoinLayout: (...args: unknown[]) => api.save(...args),
  saveLayoutAsDefault: (...args: unknown[]) => api.saveDefault(...args),
  resetLayoutToDefault: (...args: unknown[]) => api.reset(...args),
}));

const { useChartLayout, legacyTimeframeKey, legacyVolumeKey } = await import("./useChartLayout");
const { SAVE_DEBOUNCE_MS, SAVE_RETRY_MS } = await import("./useChartDrawings");

const IID = "BTC-USD-PERP.DYDX";
const layout = (patch: Partial<ChartLayout> = {}): ChartLayout => ({ ...BUILT_IN_LAYOUT, ...patch });
const settle = () => act(async () => {});
const advance = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status });

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  api.fetch.mockReset().mockResolvedValue({ layout: layout(), seeded: false });
  api.save.mockReset().mockResolvedValue(undefined);
  api.saveDefault.mockReset().mockResolvedValue(undefined);
  api.reset.mockReset().mockResolvedValue(layout({ bar_seconds: 900 }));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useChartLayout", () => {
  it("has no layout and sends nothing before the first GET answered", async () => {
    let answer: (v: unknown) => void = () => {};
    api.fetch.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const { result } = renderHook(() => useChartLayout(IID));
    await advance(SAVE_DEBOUNCE_MS * 3);

    expect(result.current.layout).toBeNull();
    expect(result.current.status).toBe("loading");
    result.current.update((l) => ({ ...l, volume: false })); // dropped: there is nothing to change yet
    await advance(SAVE_DEBOUNCE_MS * 3);
    expect(api.save).not.toHaveBeenCalled();

    answer({ layout: layout({ bar_seconds: 3600 }), seeded: false });
    await settle();
    expect(result.current.layout?.bar_seconds).toBe(3600);
    expect(api.save).not.toHaveBeenCalled(); // what the server holds is not saved back
  });

  it("sends one PUT for a burst of five changes, with the last one", async () => {
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();

    for (const bars of [100, 101, 102, 103, 104]) {
      act(() => result.current.update((l) => ({ ...l, visible_bars: bars })));
      await advance(50);
    }
    await advance(SAVE_DEBOUNCE_MS);

    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][0]).toBe(IID);
    expect(api.save.mock.calls[0][1].visible_bars).toBe(104);
  });

  it("drops a change that leaves every field equal", async () => {
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();

    act(() => result.current.update((l) => ({ ...l, volume: l.volume })));
    await advance(SAVE_DEBOUNCE_MS * 2);

    expect(api.save).not.toHaveBeenCalled();
  });

  it("flushes a pending save on unmount and sends a pagehide save with keepalive", async () => {
    const { result, unmount } = renderHook(() => useChartLayout(IID));
    await settle();
    act(() => result.current.update((l) => ({ ...l, crosshair: false })));
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(api.save.mock.calls[0][2]).toBe(true);

    api.save.mockClear();
    act(() => {
      window.dispatchEvent(new Event("pageshow")); // the page stayed (back/forward cache)
    });
    act(() => result.current.update((l) => ({ ...l, volume: false })));
    unmount();
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][2]).toBe(false);
  });

  it("saves at once a change reported after its own unmount flush (a child chart's cleanup)", async () => {
    const { result, unmount } = renderHook(() => useChartLayout(IID));
    await settle();
    const { update } = result.current;
    unmount(); // nothing pending: no PUT
    expect(api.save).not.toHaveBeenCalled();

    update((l) => ({ ...l, visible_bars: 80 })); // the chart's pending zoom, reported in its cleanup
    await vi.advanceTimersByTimeAsync(0); // queued behind the unmount flush, no debounce
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][1].visible_bars).toBe(80);
    expect(api.save.mock.calls[0][2]).toBe(false);
  });

  it("sends a change reported after pagehide at once with keepalive, and debounces again after pageshow", async () => {
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(api.save).not.toHaveBeenCalled(); // nothing pending yet

    act(() => result.current.update((l) => ({ ...l, visible_bars: 80 }))); // the chart's own pagehide flush
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][1].visible_bars).toBe(80);
    expect(api.save.mock.calls[0][2]).toBe(true);
    await settle();

    api.save.mockClear();
    act(() => {
      window.dispatchEvent(new Event("pageshow")); // restored from the back/forward cache
    });
    act(() => result.current.update((l) => ({ ...l, visible_bars: 90 })));
    expect(api.save).not.toHaveBeenCalled();
    await advance(SAVE_DEBOUNCE_MS);
    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][2]).toBe(false);
  });

  it("treats the same pane heights in another key order as no change", async () => {
    api.fetch.mockResolvedValue({ layout: layout({ pane_heights: { price: 400, volume: 90 } }), seeded: false });
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();

    act(() => result.current.update((l) => ({ ...l, pane_heights: { volume: 90, price: 400 } })));
    await advance(SAVE_DEBOUNCE_MS * 2);

    expect(api.save).not.toHaveBeenCalled();
  });

  it("imports the old timeframe and volume keys once: they win, are saved, then removed", async () => {
    api.fetch.mockResolvedValue({ layout: layout(), seeded: true }); // the coin's first open
    localStorage.setItem(legacyTimeframeKey(IID), "300");
    localStorage.setItem(legacyVolumeKey(IID), "off");
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();

    expect(result.current.layout).toMatchObject({ bar_seconds: 300, volume: false });
    expect(localStorage.getItem(legacyTimeframeKey(IID))).not.toBeNull(); // not before it is saved
    await advance(SAVE_DEBOUNCE_MS + 1);

    expect(api.save).toHaveBeenCalledTimes(1);
    expect(api.save.mock.calls[0][1]).toMatchObject({ bar_seconds: 300, volume: false });
    expect(localStorage.getItem(legacyTimeframeKey(IID))).toBeNull();
    expect(localStorage.getItem(legacyVolumeKey(IID))).toBeNull();
  });

  it("removes keys that change nothing, or hold an unusable value, without a save", async () => {
    api.fetch.mockResolvedValue({ layout: layout(), seeded: true });
    localStorage.setItem(legacyTimeframeKey(IID), "60"); // the layout is already 1m
    localStorage.setItem(legacyVolumeKey(IID), "maybe");
    renderHook(() => useChartLayout(IID));
    await settle();
    await advance(SAVE_DEBOUNCE_MS * 2);

    expect(api.save).not.toHaveBeenCalled();
    expect(localStorage.getItem(legacyTimeframeKey(IID))).toBeNull();
    expect(localStorage.getItem(legacyVolumeKey(IID))).toBeNull();
  });

  it("drops the old keys unapplied when the server already holds the coin's layout", async () => {
    localStorage.setItem(legacyTimeframeKey(IID), "300"); // older than the layout another browser saved
    localStorage.setItem(legacyVolumeKey(IID), "off");
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    await advance(SAVE_DEBOUNCE_MS * 2);

    expect(result.current.layout).toMatchObject({ bar_seconds: 60, volume: true });
    expect(api.save).not.toHaveBeenCalled();
    expect(localStorage.getItem(legacyTimeframeKey(IID))).toBeNull();
    expect(localStorage.getItem(legacyVolumeKey(IID))).toBeNull();
  });

  it("keeps the old keys while the save that carries them fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    api.fetch.mockResolvedValue({ layout: layout(), seeded: true });
    localStorage.setItem(legacyTimeframeKey(IID), "300");
    api.save.mockRejectedValueOnce(httpError(503));
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    await advance(SAVE_DEBOUNCE_MS + 1);

    expect(result.current.saveError).toMatch(/retrying/);
    expect(localStorage.getItem(legacyTimeframeKey(IID))).toBe("300");
    await advance(SAVE_RETRY_MS + 1);
    expect(api.save).toHaveBeenCalledTimes(2);
    expect(result.current.saveError).toBeNull();
    expect(localStorage.getItem(legacyTimeframeKey(IID))).toBeNull();
  });

  it("holds a refused (4xx) save until the next change instead of retrying it", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    api.save.mockRejectedValueOnce(httpError(422));
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    act(() => result.current.update((l) => ({ ...l, volume: false })));
    await advance(SAVE_DEBOUNCE_MS + 1);

    expect(result.current.saveError).toMatch(/refused/);
    await advance(SAVE_RETRY_MS * 3);
    expect(api.save).toHaveBeenCalledTimes(1);
    act(() => result.current.update((l) => ({ ...l, crosshair: false })));
    await advance(SAVE_DEBOUNCE_MS + 1);
    expect(api.save).toHaveBeenCalledTimes(2);
    expect(result.current.saveError).toBeNull();
  });

  it("falls back for a stale server value with one console.error and saves the corrected layout", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    api.fetch.mockResolvedValue({ layout: { ...layout(), bar_seconds: 30, mode: "bars" }, seeded: false });
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();

    expect(result.current.layout).toMatchObject({ bar_seconds: 60, mode: "candles" });
    expect(errors.mock.calls.filter((c) => String(c[0]).startsWith("chart layout"))).toHaveLength(1);
    await advance(SAVE_DEBOUNCE_MS + 1);
    expect(api.save.mock.calls[0][1]).toMatchObject({ bar_seconds: 60, mode: "candles" });
  });

  it("retries a failed load and reports failed until it succeeds", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    api.fetch.mockRejectedValueOnce(httpError(500));
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    expect(result.current.status).toBe("failed");
    expect(result.current.layout).toBeNull();

    await advance(SAVE_RETRY_MS + 1);
    expect(result.current.status).toBe("ready");
  });

  it("saves a pending edit before Save as default, and Reset adopts the server's layout without a PUT", async () => {
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    act(() => result.current.update((l) => ({ ...l, volume: false })));

    await act(async () => {
      await result.current.saveAsDefault();
    });
    expect(api.save.mock.invocationCallOrder[0]).toBeLessThan(api.saveDefault.mock.invocationCallOrder[0]);
    expect(api.saveDefault).toHaveBeenCalledWith(IID);

    api.save.mockClear();
    await act(async () => {
      await result.current.resetToDefault();
    });
    expect(result.current.layout?.bar_seconds).toBe(900);
    await advance(SAVE_DEBOUNCE_MS * 2);
    expect(api.save).not.toHaveBeenCalled();
  });

  it("a failed reset rejects and leaves the layout, and a pending edit is still saved", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    api.reset.mockRejectedValue(httpError(500));
    const { result } = renderHook(() => useChartLayout(IID));
    await settle();
    act(() => result.current.update((l) => ({ ...l, volume: false })));

    await act(async () => {
      await expect(result.current.resetToDefault()).rejects.toThrow();
    });
    await advance(SAVE_DEBOUNCE_MS + 1);

    expect(result.current.layout?.volume).toBe(false);
    expect(api.save).toHaveBeenCalledTimes(1);
  });
});
