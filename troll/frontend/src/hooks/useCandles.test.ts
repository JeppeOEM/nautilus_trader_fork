import type { IChartApi, LogicalRange, UTCTimestamp } from "lightweight-charts";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleItem, CandlesResponse } from "../api/schema";

const fetchCandlesMock = vi.fn<(...args: unknown[]) => Promise<CandlesResponse>>();

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchCandles: (...args: unknown[]) => fetchCandlesMock(...args),
}));

const { useCandles, mergeByTime } = await import("./useCandles");
const { HttpError } = await import("../api/client");

function page(items: Array<Partial<CandleItem> & { t: number }>, hasMore: boolean): CandlesResponse {
  return {
    items: items.map((i) => ({ o: null, h: null, l: null, c: null, v: null, ...i })),
    has_more: hasMore,
    venue: "dydx",
  };
}

type RangeHandler = (range: LogicalRange | null) => void;

function fakeChart() {
  let handler: RangeHandler | null = null;
  const api = {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: (h: RangeHandler) => {
        handler = h;
      },
      unsubscribeVisibleLogicalRangeChange: () => {
        handler = null;
      },
    }),
  } as unknown as IChartApi;
  return {
    api,
    fire: (range: LogicalRange | null) => handler?.(range),
  };
}

beforeEach(() => {
  fetchCandlesMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useCandles", () => {
  it("retries a failed initial fetch instead of staying blank forever", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchCandlesMock
      .mockRejectedValueOnce(new Error("GET /api/candles failed: 502"))
      .mockResolvedValueOnce(page([{ t: 60_000, o: 1, h: 2, l: 1, c: 2, v: 1 }], false));

    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));

    await waitFor(() => expect(result.current.loadFailed).toBe(true));
    await waitFor(() => expect(result.current.candles).toHaveLength(1), { timeout: 3000 });
    expect(result.current.loadFailed).toBe(false);
  });

  it("renders a malformed candle as a gap, never as a shape", async () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchCandlesMock.mockResolvedValue(
      page(
        [
          { t: 60_000, o: 1, h: 2, l: 0.5, c: 1.5, v: 3 },
          { t: 120_000, o: 1, h: 0.5, l: 2, c: 1.5, v: 3 }, // inverted: high below low
          { t: 180_000, o: 1, h: 2, l: 0.5, c: 1.5, v: -1 }, // negative volume
        ],
        false,
      ),
    );
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.candles).toHaveLength(3));
    expect(result.current.candles[0]).toHaveProperty("open", 1);
    expect(result.current.candles[1]).toEqual({ time: 120 });
    expect(result.current.candles[2]).toEqual({ time: 180 });
    expect(result.current.volume[1]).toEqual({ time: 120 });
    expect(result.current.volume[2]).toEqual({ time: 180 });
    errSpy.mockRestore();
  });

  it("fetches the initial 120-bar/60s page on mount", async () => {
    fetchCandlesMock.mockResolvedValue(page([{ t: 60_000, o: 1, h: 2, l: 0.5, c: 1.5 }], true));

    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));

    await waitFor(() => expect(result.current.candles).toHaveLength(1));
    expect(fetchCandlesMock).toHaveBeenCalledWith("BTC-USD-PERP.DYDX", expect.any(Number), 120, 60);
  });

  it("refills using the earliest loaded candle as before_ns when the visible range nears the start", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 120_000, o: 1, h: 1, l: 1, c: 1 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 60_000, o: 2, h: 2, l: 2, c: 2 }], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.candles).toHaveLength(2));
    expect(fetchCandlesMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", 120_000 * 1_000_000, 120, 60);
    expect(result.current.candles[0].time).toBe(60); // prepended: 60_000ms -> 60s
  });

  it("stops issuing further requests once has_more is false", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 120_000 }], false));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    fetchCandlesMock.mockClear();
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchCandlesMock).not.toHaveBeenCalled();
  });

  it("inserts a gap marker at a page boundary when the seam spans more than one bar", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 300_000, o: 1, h: 1, l: 1, c: 1 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    // Older page's newest candle (100_000ms) is 200s before the existing page's
    // earliest (300_000ms) -- a genuine >60s gap sitting exactly at the seam.
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 100_000, o: 2, h: 2, l: 2, c: 2 }], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.candles).toHaveLength(3));
    const [real1, gap, real2] = result.current.candles;
    expect(real1.time).toBe(100);
    expect("open" in gap).toBe(false);
    expect(gap.time).toBe(160); // 100s + one 60s bar
    expect(real2.time).toBe(300);
  });

  it("does not crash and keeps previous state when a fetch rejects", async () => {
    fetchCandlesMock.mockRejectedValueOnce(new Error("network error"));

    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));

    await waitFor(() => expect(fetchCandlesMock).toHaveBeenCalledTimes(1));
    expect(result.current.candles).toEqual([]);
  });
});

describe("useCandles live merge and retry rules", () => {
  it("appendBar upserts a closed live bar and refreshNewest merges a page without duplicates", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 60_000, o: 1, h: 2, l: 1, c: 2, v: 1 }], false));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    act(() => result.current.appendBar({ time: 120 as UTCTimestamp, open: 2, high: 3, low: 2, close: 3, volume: 5 }));
    expect(result.current.candles.map((c) => c.time)).toEqual([60, 120]);
    expect(result.current.volume[1]).toEqual({ time: 120, value: 5 });

    // Socket was down for two bars: the newest page overlaps t=120 and adds 180/240.
    fetchCandlesMock.mockResolvedValueOnce(
      page(
        [
          { t: 120_000, o: 2, h: 3, l: 2, c: 3, v: 5 },
          { t: 180_000, o: 3, h: 4, l: 3, c: 4, v: 1 },
          { t: 240_000, o: 4, h: 5, l: 4, c: 5, v: 1 },
        ],
        true,
      ),
    );
    await act(() => result.current.refreshNewest());
    expect(result.current.candles.map((c) => c.time)).toEqual([60, 120, 180, 240]);
  });

  it("mergeByTime marks a hole before the refetched page with one whitespace seam", () => {
    const bar = (time: number) => ({ time: time as UTCTimestamp, open: 1, high: 1, low: 1, close: 1 });
    const merged = mergeByTime([bar(60)], [bar(300)], 60);
    expect(merged.map((c) => c.time)).toEqual([60, 120, 300]);
    expect(merged[1]).toEqual({ time: 120 });
  });

  it("does not retry a deterministic HTTP error, but reports it", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchCandlesMock.mockRejectedValueOnce(new HttpError(500, "GET /api/candles failed: 500"));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.loadFailed).toBe(true));
    expect(result.current.loadError).toMatch(/500/);
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(fetchCandlesMock).toHaveBeenCalledTimes(1);
  });

  it("keeps asking, from a fresh cursor, when the initial page is empty", async () => {
    fetchCandlesMock
      .mockResolvedValueOnce(page([], false))
      .mockResolvedValueOnce(page([{ t: 60_000, o: 1, h: 2, l: 1, c: 2, v: 1 }], false));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.loadFailed).toBe(true));
    await waitFor(() => expect(result.current.candles).toHaveLength(1), { timeout: 3000 });
    expect(result.current.loadFailed).toBe(false);
    const [first, second] = fetchCandlesMock.mock.calls;
    expect(second[1] as number).toBeGreaterThanOrEqual(first[1] as number);
  });
});
