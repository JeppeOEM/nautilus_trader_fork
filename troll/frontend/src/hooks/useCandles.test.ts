import type { IChartApi, LogicalRange } from "lightweight-charts";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CandleItem, CandlesResponse } from "../api/schema";

const fetchCandlesMock = vi.fn<(...args: unknown[]) => Promise<CandlesResponse>>();

vi.mock("../api/client", () => ({
  fetchCandles: (...args: unknown[]) => fetchCandlesMock(...args),
}));

const { useCandles } = await import("./useCandles");

function page(items: Array<Partial<CandleItem> & { t: number }>, hasMore: boolean): CandlesResponse {
  return {
    items: items.map((i) => ({ o: null, h: null, l: null, c: null, v: null, ...i })),
    has_more: hasMore,
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
