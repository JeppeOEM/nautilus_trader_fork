import type { IChartApi, LogicalRange } from "lightweight-charts";
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FootprintItem, FootprintResponse } from "../api/schema";

const fetchFootprintMock = vi.fn<(...args: unknown[]) => Promise<FootprintResponse>>();

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  fetchFootprint: (...args: unknown[]) => fetchFootprintMock(...args),
}));

const { useFootprint, FOOTPRINT_REFRESH_MS, refreshDue } = await import("./useFootprint");
const { HttpError } = await import("../api/client");

const IID = "BTCUSDT-LINEAR.BYBIT";

const bar = (t: number): FootprintItem => ({
  t,
  row_ticks: 5,
  rows: [{ p: 1000, b: 3, s: 1 }],
  delta: 2,
  total: 4,
  poc_row: 1000,
  no_trades: false,
});

function page(times: number[], hasMore: boolean): FootprintResponse {
  return {
    items: times.map(bar),
    has_more: hasMore,
    price_precision: 2,
    size_precision: 6,
  };
}

/** A chart whose slot i holds bar time `i * 60` s (slots 0..199), with a settable visible range. */
function fakeChart() {
  let handler: (() => void) | null = null;
  let range: LogicalRange | null = null;
  const api = {
    timeScale: () => ({
      subscribeVisibleLogicalRangeChange: (h: () => void) => {
        handler = h;
      },
      unsubscribeVisibleLogicalRangeChange: () => {
        handler = null;
      },
      getVisibleLogicalRange: () => range,
      timeToIndex: (time: number) => (time % 60 === 0 && time / 60 < 200 ? time / 60 : null),
    }),
  } as unknown as IChartApi;
  return {
    api,
    scrollTo: (from: number) => {
      range = { from, to: from + 50 } as LogicalRange;
      handler?.();
    },
  };
}

const beforeNsOf = (call: number): number => fetchFootprintMock.mock.calls[call][1] as number;

beforeEach(() => {
  fetchFootprintMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useFootprint", () => {
  it("issues no request while off, then loads the newest page with the row size", async () => {
    fetchFootprintMock.mockResolvedValue(page([6_000_000, 6_060_000], true));
    const { result, rerender } = renderHook(({ on }) => useFootprint(IID, null, 60, on, 0), {
      initialProps: { on: false },
    });
    await act(async () => {});
    expect(fetchFootprintMock).not.toHaveBeenCalled();

    rerender({ on: true });

    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(fetchFootprintMock).toHaveBeenCalledTimes(1);
    expect(fetchFootprintMock.mock.calls[0].slice(2)).toEqual([120, 60, 0]);
    expect(result.current.precision).toEqual({ price: 2, size: 6 });
  });

  it("refills from its own earliest bar on scroll-back, and stops once has_more is false", async () => {
    const chart = fakeChart();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true)) // slots 100, 101
      .mockResolvedValueOnce(page([5_940_000], false)); // slot 99
    const { result } = renderHook(() => useFootprint(IID, chart.api, 60, true, 0));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    act(() => chart.scrollTo(150)); // far from slot 100: nothing
    expect(fetchFootprintMock).toHaveBeenCalledTimes(1);

    act(() => chart.scrollTo(90));
    await waitFor(() => expect(result.current.items.map((b) => b.t)).toEqual([5_940_000, 6_000_000, 6_060_000]));
    expect(beforeNsOf(1)).toBe(6_000_000 * 1_000_000);

    act(() => chart.scrollTo(80));
    await act(async () => {});
    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
  });

  it("does not page past the chart's own loaded bars", async () => {
    const chart = fakeChart();
    fetchFootprintMock.mockResolvedValueOnce(page([60_000_000], true)); // not a slot of the chart
    const { result } = renderHook(() => useFootprint(IID, chart.api, 60, true, 0));
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    act(() => chart.scrollTo(0));
    await act(async () => {});

    expect(fetchFootprintMock).toHaveBeenCalledTimes(1);
  });

  it("refetches the newest page on its interval while on, merging the newly settled bar", async () => {
    vi.useFakeTimers();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000], true))
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true));
    const { result, rerender } = renderHook(({ on }) => useFootprint(IID, null, 60, on, 0), {
      initialProps: { on: true },
    });
    await act(async () => {});
    expect(result.current.items).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });
    expect(result.current.items.map((b) => b.t)).toEqual([6_000_000, 6_060_000]);

    rerender({ on: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS * 3);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
  });

  it("fills the hole a newest page leaves after a long absence, back to the bar held before it", async () => {
    vi.useFakeTimers();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000], true))
      .mockResolvedValueOnce(page([6_240_000, 6_300_000], true)) // starts after 6_000_000: a hole
      .mockResolvedValueOnce(page([6_120_000, 6_180_000], true)) // fill, not there yet
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true)); // fill reaches the held bar
    const { result } = renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });

    expect(result.current.items.map((b) => b.t)).toEqual([
      6_000_000, 6_060_000, 6_120_000, 6_180_000, 6_240_000, 6_300_000,
    ]);
    expect(beforeNsOf(2)).toBe(6_240_000 * 1_000_000);
    expect(beforeNsOf(3)).toBe(6_120_000 * 1_000_000);
    // The refresh asks for a few newest bars only; the fill pages are full pages.
    expect(fetchFootprintMock.mock.calls.map((call) => call[2])).toEqual([120, 10, 120, 120]);
    expect(fetchFootprintMock).toHaveBeenCalledTimes(4);
  });

  it("does not fill when the newest page overlaps the bars already held", async () => {
    vi.useFakeTimers();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000], true))
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true));
    renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });

    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
  });

  it("does not fill when the newest page starts at the bar right after the newest held one", async () => {
    vi.useFakeTimers();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000], true))
      .mockResolvedValueOnce(page([6_060_000, 6_120_000], true));
    const { result } = renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });

    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
    expect(result.current.items.map((b) => b.t)).toEqual([6_000_000, 6_060_000, 6_120_000]);
  });

  it("fills a second hole that opens while the first one's fill waits on a retry", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    let newest = 0;
    fetchFootprintMock.mockImplementation((_iid, beforeNs, limit) => {
      if (limit === 10) {
        newest += 1;
        return Promise.resolve(newest === 1 ? page([6_240_000, 6_300_000], true) : page([6_480_000, 6_540_000], true));
      }
      if (beforeNs === 6_240_000 * 1_000_000) return Promise.reject(new TypeError("network")); // hole 1
      if (beforeNs === 6_480_000 * 1_000_000) {
        return Promise.resolve(page([6_120_000, 6_180_000, 6_240_000, 6_300_000, 6_360_000, 6_420_000], true));
      }
      if (beforeNs === 6_120_000 * 1_000_000) return Promise.resolve(page([6_000_000, 6_060_000], true));
      return Promise.resolve(page([6_000_000], true)); // the initial page
    });
    const { result } = renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2 * FOOTPRINT_REFRESH_MS + 10_000);
    });

    expect(result.current.items.map((b) => b.t)).toEqual(
      Array.from({ length: 10 }, (_, k) => 6_000_000 + k * 60_000),
    );
    // The first hole's pending retry was overtaken by the restarted fill: never asked again.
    const calls = fetchFootprintMock.mock.calls;
    const secondNewest = calls.findIndex((call, k) => call[2] === 10 && calls.slice(0, k).some((c) => c[2] === 10));
    expect(calls.slice(secondNewest).some((call) => call[1] === 6_240_000 * 1_000_000)).toBe(false);
  });

  it("drops a pending older retry once a pan loaded that page meanwhile", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    const chart = fakeChart();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true)) // slots 100, 101
      .mockRejectedValueOnce(new TypeError("network")) // the older page, retried in 1 s
      .mockResolvedValueOnce(page([5_940_000], true)) // the pan's own older page
      .mockResolvedValueOnce(page([5_880_000], false));
    const { result } = renderHook(() => useFootprint(IID, chart.api, 60, true, 0));
    await act(async () => {});

    act(() => chart.scrollTo(90));
    await act(async () => {});
    act(() => chart.scrollTo(89));
    await act(async () => {});
    expect(result.current.items.map((b) => b.t)).toEqual([5_880_000, 5_940_000, 6_000_000, 6_060_000]);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(4);
  });

  it("drops the pages and refetches on a row-size change, and discards a late answer for the old size", async () => {
    let resolveOld: (r: FootprintResponse) => void = () => {};
    fetchFootprintMock
      .mockReturnValueOnce(new Promise((resolve) => (resolveOld = resolve)))
      .mockResolvedValueOnce(page([6_060_000], true));
    const { result, rerender } = renderHook(({ ticks }) => useFootprint(IID, null, 60, true, ticks), {
      initialProps: { ticks: 0 },
    });

    rerender({ ticks: 5 });
    await waitFor(() => expect(result.current.items.map((b) => b.t)).toEqual([6_060_000]));
    await act(async () => resolveOld(page([6_000_000], true)));

    expect(result.current.items.map((b) => b.t)).toEqual([6_060_000]);
    expect(fetchFootprintMock.mock.calls.map((c) => c[4])).toEqual([0, 5]);
  });

  it("retries a transient failure with backoff, and stops on a deterministic one", async () => {
    vi.useFakeTimers();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchFootprintMock
      .mockRejectedValueOnce(new HttpError(502, "bad gateway"))
      .mockRejectedValueOnce(new HttpError(500, "TradeDecodeError"));
    const { result } = renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});
    expect(result.current.error).toMatch(/retrying/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.error).toMatch(/500/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
    expect(errors).toHaveBeenCalledTimes(2);
  });

  it("drops a pending retry when switched off, and asks again on the next enable", async () => {
    vi.useFakeTimers();
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchFootprintMock.mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce(page([6_000_000], false));
    const { result, rerender } = renderHook(({ on }) => useFootprint(IID, null, 60, on, 0), {
      initialProps: { on: true },
    });
    await act(async () => {});

    rerender({ on: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(1);

    rerender({ on: true });
    await act(async () => {});
    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
    expect(result.current.items).toHaveLength(1);
  });

  it("asks no older page again after a deterministic failure of one, however the chart pans", async () => {
    const chart = fakeChart();
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true)) // slots 100, 101
      .mockRejectedValueOnce(new HttpError(500, "TradeDecodeError"));
    const { result } = renderHook(() => useFootprint(IID, chart.api, 60, true, 0));
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    act(() => chart.scrollTo(90));
    await waitFor(() => expect(result.current.error).toMatch(/500/));
    for (const from of [89, 85, 80]) act(() => chart.scrollTo(from));
    await act(async () => {});

    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);
  });

  it("asks no fill page while off, and resumes the fill on the next enable", async () => {
    vi.useFakeTimers();
    let resolveHole: (r: FootprintResponse) => void = () => {};
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000], true))
      .mockReturnValueOnce(new Promise((resolve) => (resolveHole = resolve)))
      .mockResolvedValueOnce(page([6_000_000, 6_060_000, 6_120_000, 6_180_000], true));
    const { result, rerender } = renderHook(({ on }) => useFootprint(IID, null, 60, on, 0), {
      initialProps: { on: true },
    });
    await act(async () => {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });

    rerender({ on: false });
    await act(async () => resolveHole(page([6_240_000, 6_300_000], true))); // leaves a hole
    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS * 2);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(2);

    rerender({ on: true });
    await act(async () => {});
    expect(fetchFootprintMock).toHaveBeenCalledTimes(3);
    expect(beforeNsOf(2)).toBe(6_240_000 * 1_000_000);
    expect(result.current.items.map((b) => b.t)).toEqual([
      6_000_000, 6_060_000, 6_120_000, 6_180_000, 6_240_000, 6_300_000,
    ]);
  });

  it("drops the held bars when a response comes in other precisions", async () => {
    vi.useFakeTimers();
    fetchFootprintMock
      .mockResolvedValueOnce(page([6_000_000, 6_060_000], true))
      .mockResolvedValueOnce({ ...page([6_120_000], true), price_precision: 1 });
    const { result } = renderHook(() => useFootprint(IID, null, 60, true, 0));
    await act(async () => {});
    expect(result.current.items).toHaveLength(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS);
    });

    expect(result.current.items.map((b) => b.t)).toEqual([6_120_000]);
    expect(result.current.precision).toEqual({ price: 1, size: 6 });
  });

  it("refreshes only once the bar after the newest held one can have closed", async () => {
    vi.useFakeTimers();
    const day = 86_400;
    const newest = Date.now() - 3_600_000; // a 1D bar that started an hour ago
    fetchFootprintMock.mockResolvedValue(page([newest], true));
    renderHook(() => useFootprint(IID, null, day, true, 0));
    await act(async () => {});

    await act(async () => {
      await vi.advanceTimersByTimeAsync(FOOTPRINT_REFRESH_MS * 5);
    });
    expect(fetchFootprintMock).toHaveBeenCalledTimes(1);

    expect(refreshDue({ latestMs: null }, day, 0)).toBe(true);
    expect(refreshDue({ latestMs: newest }, day, newest + 2 * day * 1000 - 1)).toBe(false);
    expect(refreshDue({ latestMs: newest }, day, newest + 2 * day * 1000)).toBe(true);
  });
});
