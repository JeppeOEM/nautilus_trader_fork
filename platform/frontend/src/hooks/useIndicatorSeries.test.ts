import type { IChartApi, LogicalRange } from "lightweight-charts";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IndicatorSeriesPoint, IndicatorSeriesResponse } from "../api/schema";

const fetchIndicatorSeriesMock = vi.fn<(...args: unknown[]) => Promise<IndicatorSeriesResponse>>();

vi.mock("../api/client", () => ({
  fetchIndicatorSeries: (...args: unknown[]) => fetchIndicatorSeriesMock(...args),
}));

const { useIndicatorSeries } = await import("./useIndicatorSeries");

function page(items: Array<Partial<IndicatorSeriesPoint> & { t: number }>, hasMore: boolean): IndicatorSeriesResponse {
  return { items, has_more: hasMore, venue: "dydx", market: "perp" };
}

const full = (t: number, v: number): IndicatorSeriesPoint => ({ t, ofi: v, obi: v, microprice: v, spread: v });

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
  return { api, fire: (range: LogicalRange | null) => handler?.(range) };
}

beforeEach(() => {
  fetchIndicatorSeriesMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useIndicatorSeries", () => {
  it("refills using the earliest loaded point as before_ns and prepends without a seam for adjacent pages", async () => {
    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(120_000, 1)], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useIndicatorSeries("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.ofi).toHaveLength(1));

    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(60_000, 2)], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.ofi).toHaveLength(2));
    expect(fetchIndicatorSeriesMock).toHaveBeenLastCalledWith("BTC-USD-PERP.DYDX", 120_000 * 1_000_000, 120, 60);
    expect(result.current.ofi).toEqual([
      { time: 60, value: 2 },
      { time: 120, value: 1 },
    ]);
  });

  it("fills a page-boundary hole with one whitespace slot per missing bar on every series (Story 32.1)", async () => {
    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(300_000, 1)], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useIndicatorSeries("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.ofi).toHaveLength(1));

    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(0, 2)], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current.ofi).toHaveLength(6));
    for (const series of [result.current.ofi, result.current.obi, result.current.microprice, result.current.spread]) {
      expect(series).toEqual([
        { time: 0, value: 2 },
        { time: 60 },
        { time: 120 },
        { time: 180 },
        { time: 240 },
        { time: 300, value: 1 },
      ]);
    }
  });

  it("caps a page-boundary run at 720 slots and stops refilling once has_more is false", async () => {
    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(86_400_000, 1)], true));
    const chart = fakeChart();
    const { result } = renderHook(() => useIndicatorSeries("BTC-USD-PERP.DYDX", chart.api));
    await waitFor(() => expect(result.current.ofi).toHaveLength(1));

    fetchIndicatorSeriesMock.mockResolvedValueOnce(page([full(0, 2)], false));
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });
    await waitFor(() => expect(result.current.ofi).toHaveLength(722));
    expect(result.current.ofi[720]).toEqual({ time: 43_200 });

    fetchIndicatorSeriesMock.mockClear();
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchIndicatorSeriesMock).not.toHaveBeenCalled();
  });
});
