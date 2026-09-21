import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleItem } from "../api/schema";

const fetchCandlesMock = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({ fetchCandles: fetchCandlesMock }));

const { useSessionCandles } = await import("./useSessionCandles");

const item = (tSec: number, v = 1): CandleItem => ({ t: tSec * 1000, o: 1, h: 2, l: 1, c: 2, v });
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2024-01-03T12:00:00Z"));
  fetchCandlesMock.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("useSessionCandles (Story 18.8)", () => {
  it("is idle while disabled", async () => {
    renderHook(() => useSessionCandles("BTC", false, 0, 60));
    await flush();

    expect(fetchCandlesMock).not.toHaveBeenCalled();
  });

  it("pages back until the wanted start is covered, then stops (fully covered)", async () => {
    const since = 1_000_000;
    fetchCandlesMock
      .mockResolvedValueOnce({ items: [item(1_000_600), item(1_000_660)], has_more: true })
      .mockResolvedValueOnce({ items: [item(999_900), item(1_000_000)], has_more: true });
    const { result } = renderHook(() => useSessionCandles("BTC", true, since, 60));
    await flush();
    await flush();

    expect(fetchCandlesMock).toHaveBeenCalledTimes(2);
    expect(fetchCandlesMock.mock.calls[1][1]).toBe(1_000_600 * 1000 * 1_000_000); // cursor = earliest bar so far
    expect(result.current.candles.map((c) => c.time)).toEqual([999_900, 1_000_000, 1_000_600, 1_000_660]);
    expect(result.current.completeFrom).toBeNull();
  });

  it("flags the history as only partially covered when it stops short of the wanted start", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(5_000)], has_more: true });
    const { result } = renderHook(() => useSessionCandles("BTC", true, 10, 60));
    await flush();
    for (let i = 0; i < 45; i++) await flush();

    expect(fetchCandlesMock.mock.calls.length).toBeLessThanOrEqual(80);
    expect(result.current.completeFrom).toBe(5_000);
  });

  it("sizes the page budget to the wanted span, so 10 weekly periods at 5m bars are reachable", async () => {
    const now = Date.parse("2024-01-03T12:00:00Z") / 1000;
    fetchCandlesMock.mockResolvedValue({ items: [item(now)], has_more: true }); // never reaches `since`
    renderHook(() => useSessionCandles("BTC", true, now - 10 * 7 * 86_400, 300));
    for (let i = 0; i < 90; i++) await flush();

    // ~20160 bars / 500 per page = 41 pages, +2 slack -- more than the old fixed 40.
    expect(fetchCandlesMock.mock.calls.length).toBeGreaterThan(40);
    expect(fetchCandlesMock.mock.calls.length).toBeLessThanOrEqual(80);
  });

  it("refreshes at most once a minute even for coarse bars", async () => {
    const now = Date.parse("2024-01-03T12:00:00Z") / 1000;
    fetchCandlesMock.mockResolvedValue({ items: [item(now)], has_more: false });
    renderHook(() => useSessionCandles("BTC", true, now, 900));
    await flush();
    fetchCandlesMock.mockClear();

    await act(async () => { vi.advanceTimersByTime(60_000); });

    expect(fetchCandlesMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes only the newest bars each interval, replacing the forming bar in place", async () => {
    const now = Date.parse("2024-01-03T12:00:00Z") / 1000;
    fetchCandlesMock.mockResolvedValueOnce({ items: [item(now - 60, 1)], has_more: false });
    const { result } = renderHook(() => useSessionCandles("BTC", true, now - 60, 60));
    await flush();

    fetchCandlesMock.mockResolvedValueOnce({ items: [item(now - 60, 9), item(now, 2)], has_more: false });
    await act(async () => { vi.advanceTimersByTime(60_000); });
    await flush();

    expect(fetchCandlesMock.mock.calls[1][2]).toBe(5);
    expect(result.current.volume.map((v) => "value" in v && v.value)).toEqual([9, 2]);
  });

  it("widens the refresh to bridge a long starved interval", async () => {
    const now = Date.parse("2024-01-03T12:00:00Z") / 1000;
    fetchCandlesMock.mockResolvedValueOnce({ items: [item(now - 3600)], has_more: false }); // newest bar is an hour old
    renderHook(() => useSessionCandles("BTC", true, now - 3600, 60));
    await flush();

    fetchCandlesMock.mockResolvedValueOnce({ items: [], has_more: false });
    await act(async () => { vi.advanceTimersByTime(60_000); });
    await flush();

    expect(fetchCandlesMock.mock.calls[1][2]).toBeGreaterThanOrEqual(60);
  });

  it("derives coverage from the current wanted start, so asking for more sessions is not covered until fetched", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000)], has_more: true });
    const { result, rerender } = renderHook(({ since }) => useSessionCandles("BTC", true, since, 60), {
      initialProps: { since: 1_000_000 },
    });
    await flush();
    expect(result.current.completeFrom).toBeNull();

    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000)], has_more: true }); // can't go further back
    rerender({ since: 900_000 });

    expect(result.current.completeFrom).toBe(1_000_000);
  });

  it("keeps refreshing after a failed initial load, and discards items of another bar size", async () => {
    fetchCandlesMock.mockRejectedValueOnce(new Error("boom"));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { result, rerender } = renderHook(({ bar }) => useSessionCandles("BTC", true, 1_000_000, bar), {
      initialProps: { bar: 60 },
    });
    await flush();
    await flush();

    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000, 3)], has_more: false });
    await act(async () => { vi.advanceTimersByTime(60_000); });
    await flush();
    expect(result.current.candles).toHaveLength(1);

    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_300, 4)], has_more: false });
    rerender({ bar: 300 });
    await flush();
    await flush();

    expect(result.current.candles.map((c) => c.time)).toEqual([1_000_300]);
  });

  it("stops fetching after unmount", async () => {
    fetchCandlesMock.mockResolvedValue({ items: [item(1_000_000)], has_more: false });
    const { unmount } = renderHook(() => useSessionCandles("BTC", true, 1_000_000, 60));
    await flush();
    unmount();
    fetchCandlesMock.mockClear();

    await act(async () => { vi.advanceTimersByTime(300_000); });

    expect(fetchCandlesMock).not.toHaveBeenCalled();
  });
});
