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
const { MAX_GAP_ROWS_PER_GAP } = await import("../lib/gaps");
const { HttpError } = await import("../api/client");

function page(items: Array<Partial<CandleItem> & { t: number }>, hasMore: boolean): CandlesResponse {
  return {
    items: items.map((i) => ({ o: null, h: null, l: null, c: null, v: null, ...i })),
    has_more: hasMore,
    venue: "dydx", market: "perp", price_precision: 2, size_precision: 3,
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

  it("fills a page-boundary hole with one whitespace slot per missing bar, candles and volume alike", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 300_000, o: 1, h: 1, l: 1, c: 1, v: 1 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    // Older page's newest candle (0 ms) is 300 s before the existing page's earliest
    // (300_000 ms) -- four 60 s bars missing, sitting exactly at the seam (Story 32.1).
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 0, o: 2, h: 2, l: 2, c: 2, v: 1 }], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.candles).toHaveLength(6));
    expect(result.current.candles.map((c) => c.time)).toEqual([0, 60, 120, 180, 240, 300]);
    expect(result.current.candles.slice(1, 5)).toEqual([{ time: 60 }, { time: 120 }, { time: 180 }, { time: 240 }]);
    expect(result.current.volume.slice(1, 5)).toEqual([{ time: 60 }, { time: 120 }, { time: 180 }, { time: 240 }]);
  });

  it("caps a page-boundary run at MAX_GAP_ROWS_PER_GAP, contiguous from the hole's start", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 86_400_000, o: 1, h: 1, l: 1, c: 1, v: 1 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 0, o: 2, h: 2, l: 2, c: 2, v: 1 }], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.candles).toHaveLength(722));
    expect(result.current.candles[1].time).toBe(60);
    expect(result.current.candles[720].time).toBe(43_200);
    expect(result.current.candles[721].time).toBe(86_400);
  });

  it("still refills when the visible logical `from` lands inside a long gap run at the loaded left edge", async () => {
    // The loaded page starts with one real bar, then a 100-slot gap run: the left edge the
    // operator scrolls into is whitespace, but REFILL_MARGIN_BARS counts logical slots.
    const items = [{ t: 0, o: 1, h: 1, l: 1, c: 1, v: 1 }, ...Array.from({ length: 100 }, (_, i) => ({ t: (i + 1) * 60_000 }))];
    fetchCandlesMock.mockResolvedValueOnce(page([...items, { t: 101 * 60_000, o: 1, h: 1, l: 1, c: 1, v: 1 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.candles).toHaveLength(102));

    fetchCandlesMock.mockResolvedValueOnce(page([], false));
    chart.fire({ from: 10 as LogicalRange["from"], to: 60 as LogicalRange["to"] }); // slot 10: a gap slot

    await waitFor(() => expect(fetchCandlesMock).toHaveBeenCalledTimes(2));
    expect(fetchCandlesMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", 0, 120, 60);
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

  it("mergeByTime fills a hole before the refetched page with one whitespace slot per missing bar", () => {
    const bar = (time: number) => ({ time: time as UTCTimestamp, open: 1, high: 1, low: 1, close: 1 });
    const merged = mergeByTime([bar(60)], [bar(300)], 60);
    expect(merged.map((c) => c.time)).toEqual([60, 120, 180, 240, 300]);
    expect(merged.slice(1, 4)).toEqual([{ time: 120 }, { time: 180 }, { time: 240 }]);
  });

  it("mergeByTime adds no seam for adjacent bars and resumes after a trailing gap slot prev holds", () => {
    const bar = (time: number) => ({ time: time as UTCTimestamp, open: 1, high: 1, low: 1, close: 1 });
    expect(mergeByTime([bar(60)], [bar(120)], 60).map((c) => c.time)).toEqual([60, 120]);
    // prev already ends in a gap slot at 120 (its own gap row): kept as-is, the run continues after it.
    const prev = [bar(60), { time: 120 as UTCTimestamp } as ReturnType<typeof bar>];
    const merged = mergeByTime(prev, [bar(300)], 60);
    expect(merged.map((c) => c.time)).toEqual([60, 120, 180, 240, 300]);
    expect(merged[1]).toBe(prev[1]);
  });

  it("mergeByTime caps a tail run at MAX_GAP_ROWS_PER_GAP", () => {
    const bar = (time: number) => ({ time: time as UTCTimestamp, open: 1, high: 1, low: 1, close: 1 });
    const merged = mergeByTime([bar(0)], [bar(86_400)], 60);
    expect(merged).toHaveLength(722);
    expect(merged[720].time).toBe(43_200);
  });

  it("mergeByTime never lets an incoming gap slot blank out a real bar prev already holds", () => {
    const bar = (time: number) => ({ time: time as UTCTimestamp, open: 1, high: 1, low: 1, close: 1 });
    // A refetched page read before the promoted live bar at 120 was persisted: the server
    // reports 120 as a gap row, the chart already showed the bar.
    const prev = [bar(60), bar(120)];
    const merged = mergeByTime(prev, [{ time: 120 as UTCTimestamp } as ReturnType<typeof bar>, bar(180)], 60);
    expect(merged).toEqual([bar(60), bar(120), bar(180)]);
    // A real incoming bar still wins over a gap slot prev holds.
    const filled = mergeByTime([bar(60), { time: 120 as UTCTimestamp } as ReturnType<typeof bar>], [bar(120)], 60);
    expect(filled).toEqual([bar(60), bar(120)]);
  });

  it("openGapTo opens the run up to a forming bar that starts after a hole, candles and volume alike", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 60_000, o: 1, h: 2, l: 1, c: 2, v: 1 }], false));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    act(() => result.current.openGapTo(300 as UTCTimestamp));
    expect(result.current.candles).toEqual([expect.objectContaining({ time: 60 }), { time: 120 }, { time: 180 }, { time: 240 }]);
    expect(result.current.volume.slice(1)).toEqual([{ time: 120 }, { time: 180 }, { time: 240 }]);

    // The next bar right after the newest point, and a repeat for the same forming bar, add nothing.
    const before = result.current.candles;
    act(() => result.current.openGapTo(300 as UTCTimestamp));
    expect(result.current.candles).toBe(before);

    // The forming bar closes: it lands right after the run, with no second run.
    act(() => result.current.appendBar({ time: 300 as UTCTimestamp, open: 2, high: 3, low: 2, close: 3, volume: 5 }));
    expect(result.current.candles.map((c) => c.time)).toEqual([60, 120, 180, 240, 300]);
  });

  it("a hole over the cap keeps exactly one capped run through openGapTo and the bar's close", async () => {
    fetchCandlesMock.mockResolvedValueOnce(page([{ t: 60_000, o: 1, h: 2, l: 1, c: 2, v: 1 }], false));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    await waitFor(() => expect(result.current.candles).toHaveLength(1));

    const formingAt = (60 + 3 * 86_400) as UTCTimestamp; // three days later: far past the cap
    act(() => result.current.openGapTo(formingAt));
    expect(result.current.candles).toHaveLength(1 + MAX_GAP_ROWS_PER_GAP);
    // A repeat (history re-delivered, effect re-run) adds nothing past the capped run.
    act(() => result.current.openGapTo(formingAt));
    expect(result.current.candles).toHaveLength(1 + MAX_GAP_ROWS_PER_GAP);

    act(() => result.current.appendBar({ time: formingAt, open: 2, high: 3, low: 2, close: 3, volume: 5 }));
    const times = result.current.candles.map((c) => c.time as number);
    expect(times).toHaveLength(1 + MAX_GAP_ROWS_PER_GAP + 1);
    expect(times[MAX_GAP_ROWS_PER_GAP]).toBe(60 + MAX_GAP_ROWS_PER_GAP * 60);
    expect(times.at(-1)).toBe(formingAt);
    expect(result.current.volume).toHaveLength(1 + MAX_GAP_ROWS_PER_GAP + 1);
  });

  it("openGapTo does nothing before history has loaded", () => {
    fetchCandlesMock.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useCandles("BTC-USD-PERP.DYDX", null));
    act(() => result.current.openGapTo(300 as UTCTimestamp));
    expect(result.current.candles).toEqual([]);
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
