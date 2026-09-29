import type { IChartApi, LogicalRange } from "lightweight-charts";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SnapshotSeriesPoint, SnapshotSeriesResponse } from "../api/schema";

const fetchSnapshotSeriesMock = vi.fn<(...args: unknown[]) => Promise<SnapshotSeriesResponse>>();

vi.mock("../api/client", () => ({
  fetchSnapshotSeries: (...args: unknown[]) => fetchSnapshotSeriesMock(...args),
}));

const { useSnapshotSeries } = await import("./useSnapshotSeries");

function page(items: Array<Partial<SnapshotSeriesPoint> & { t: number }>, hasMore: boolean): SnapshotSeriesResponse {
  return {
    items: items.map((i) => ({
      bid_units: null,
      ask_units: null,
      price_precision: null,
      mid: null,
      micro: null,
      price: null,
      ...i,
    })),
    has_more: hasMore,
    venue: "dydx", market: "perp",
  };
}

type RangeHandler = (range: LogicalRange | null) => void;

function fakeChart(visibleRange: { from: number; to: number } | null = null) {
  let handler: RangeHandler | null = null;
  const api = {
    timeScale: () => ({
      getVisibleRange: () => visibleRange,
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
  fetchSnapshotSeriesMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useSnapshotSeries", () => {
  it("fetches nothing while disabled", async () => {
    renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", null, false));

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchSnapshotSeriesMock).not.toHaveBeenCalled();
  });

  it("fetches the initial page anchored on `now` once enabled, when the chart has no visible range yet", async () => {
    fetchSnapshotSeriesMock.mockResolvedValue(page([{ t: 60_000, bid_units: 10, ask_units: 20, price_precision: 1, mid: 1.5, micro: 1.4, price: 1.5 }], true));

    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", null, true));

    await waitFor(() => expect(result.current.bid).toHaveLength(1));
    expect(fetchSnapshotSeriesMock).toHaveBeenCalledWith("BTC-USD-PERP.DYDX", expect.any(Number), 900);
  });

  it("plots the integer bid/ask through the exact units helper and keeps a gap row as whitespace", async () => {
    fetchSnapshotSeriesMock.mockResolvedValue(
      page(
        [
          { t: 60_000, bid_units: 858919, ask_units: 858920, price_precision: 1, mid: 85891.95 },
          { t: 62_999 },
        ],
        false,
      ),
    );

    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", null, true));

    await waitFor(() => expect(result.current.bid).toHaveLength(2));
    expect(result.current.bid[0]).toEqual({ time: 60, value: 85891.9 });
    expect(result.current.ask[0]).toEqual({ time: 60, value: 85892 });
    expect("value" in result.current.bid[1]).toBe(false);
  });

  it("anchors the initial fetch off the chart's current visible range when it isn't at the live edge", async () => {
    fetchSnapshotSeriesMock.mockResolvedValue(page([{ t: 60_000, bid_units: 10, ask_units: 20, price_precision: 1, mid: 1.5, micro: 1.4, price: 1.5 }], false));
    // Visible range far in the past (not near "now") -- from=0s, to=1000s (not live edge).
    const chart = fakeChart({ from: 0, to: 1000 });

    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", chart.api, true));

    await waitFor(() => expect(result.current.bid).toHaveLength(1));
    expect(fetchSnapshotSeriesMock).toHaveBeenCalledWith("BTC-USD-PERP.DYDX", 1000 * 1_000_000_000, 1000);
  });

  it("does not re-fetch the initial page a second time when re-enabled after being disabled", async () => {
    fetchSnapshotSeriesMock.mockResolvedValue(page([{ t: 60_000 }], false));
    const { rerender } = renderHook(({ enabled }) => useSnapshotSeries("BTC-USD-PERP.DYDX", null, enabled), {
      initialProps: { enabled: true },
    });
    await waitFor(() => expect(fetchSnapshotSeriesMock).toHaveBeenCalledTimes(1));

    rerender({ enabled: false });
    rerender({ enabled: true });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchSnapshotSeriesMock).toHaveBeenCalledTimes(1);
  });

  it("refills using the earliest loaded row as before_ns when the visible range nears the start", async () => {
    // Adjacent, 1-second-apart rows (61s then 60s) -- no seam gap (rows are ~1/second, so
    // only a >2.5s seam triggers a marker, unlike useCandles' 60s bar spacing).
    fetchSnapshotSeriesMock.mockResolvedValueOnce(page([{ t: 61_000, bid_units: 10, ask_units: 20, price_precision: 1, mid: 1.5, micro: 1.5, price: 1.5 }], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", chart.api, true));
    await waitFor(() => expect(result.current.bid).toHaveLength(1));

    fetchSnapshotSeriesMock.mockResolvedValueOnce(page([{ t: 60_000, bid_units: 20, ask_units: 30, price_precision: 1, mid: 2.5, micro: 2.5, price: 2.5 }], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.bid).toHaveLength(2));
    expect(fetchSnapshotSeriesMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", 61_000 * 1_000_000, 900);
    expect(result.current.bid[0].time).toBe(60); // prepended: 60_000ms -> 60s
  });

  it("does not insert a seam-gap marker for a 2s page-boundary gap (below the 2.5s backend threshold)", async () => {
    // Regression: the seam check must use the same 2.5s threshold the backend's own
    // gap-marker insertion uses, not a hardcoded 1s -- a 2s gap here is normal, not a
    // DATA-01 gap, and must render identically whether or not it happens to straddle a
    // scroll-back page boundary.
    fetchSnapshotSeriesMock.mockResolvedValueOnce(
      page([{ t: 63_000, bid_units: 10, ask_units: 20, price_precision: 1, mid: 1.5, micro: 1.5, price: 1.5 }], true),
    );
    const chart = fakeChart();
    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", chart.api, true));
    await waitFor(() => expect(result.current.bid).toHaveLength(1));

    fetchSnapshotSeriesMock.mockResolvedValueOnce(
      page([{ t: 61_000, bid_units: 20, ask_units: 30, price_precision: 1, mid: 2.5, micro: 2.5, price: 2.5 }], false),
    );
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.bid).toHaveLength(2));
    expect("value" in result.current.bid[0]).toBe(true); // no injected null gap-marker row
  });

  it("stops issuing further requests once has_more is false", async () => {
    fetchSnapshotSeriesMock.mockResolvedValueOnce(page([{ t: 120_000 }], false));
    const chart = fakeChart();
    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", chart.api, true));
    await waitFor(() => expect(result.current.bid).toHaveLength(1));

    fetchSnapshotSeriesMock.mockClear();
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchSnapshotSeriesMock).not.toHaveBeenCalled();
  });

  it("passes a gap-marker item (all fields null) through as native whitespace data", async () => {
    fetchSnapshotSeriesMock.mockResolvedValue(page([{ t: 60_000 }], false));

    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", null, true));

    await waitFor(() => expect(result.current.bid).toHaveLength(1));
    expect("value" in result.current.bid[0]).toBe(false);
    expect("value" in result.current.mid[0]).toBe(false);
  });

  it("does not crash and keeps previous state when a fetch rejects", async () => {
    fetchSnapshotSeriesMock.mockRejectedValueOnce(new Error("network error"));

    const { result } = renderHook(() => useSnapshotSeries("BTC-USD-PERP.DYDX", null, true));

    await waitFor(() => expect(fetchSnapshotSeriesMock).toHaveBeenCalledTimes(1));
    expect(result.current.bid).toEqual([]);
  });
});
