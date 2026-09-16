import type { IChartApi, LogicalRange } from "lightweight-charts";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { IndicatorConfigEntry, IndicatorValuesItem, IndicatorValuesResponse } from "../api/schema";

const fetchIndicatorValuesMock = vi.fn<(...args: unknown[]) => Promise<IndicatorValuesResponse>>();

vi.mock("../api/client", () => ({
  fetchIndicatorValues: (...args: unknown[]) => fetchIndicatorValuesMock(...args),
}));

const { usePickerIndicatorValues } = await import("./usePickerIndicatorValues");

function page(items: Array<Partial<IndicatorValuesItem> & { t: number }>, hasMore: boolean): IndicatorValuesResponse {
  return { items: items.map((i) => ({ values: {}, ...i })), has_more: hasMore };
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
      getVisibleRange: () => null,
    }),
  } as unknown as IChartApi;
  return { api, fire: (range: LogicalRange | null) => handler?.(range) };
}

const entries: IndicatorConfigEntry[] = [{ name: "RelativeStrengthIndex", params: { period: 14 }, category: "native" }];

beforeEach(() => {
  fetchIndicatorValuesMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("usePickerIndicatorValues", () => {
  it("does nothing when there are no configured entries", async () => {
    const { result } = renderHook(() => usePickerIndicatorValues("BTC-USD-PERP.DYDX", null, []));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchIndicatorValuesMock).not.toHaveBeenCalled();
    expect(result.current).toEqual({});
  });

  it("fetches the initial page and exposes one series per value key", async () => {
    fetchIndicatorValuesMock.mockResolvedValue(
      page([{ t: 60_000, values: { "RelativeStrengthIndex_period=14.value": 55 } }], true),
    );

    const { result } = renderHook(() => usePickerIndicatorValues("BTC-USD-PERP.DYDX", null, entries));

    await waitFor(() => expect(Object.keys(result.current)).toHaveLength(1));
    expect(fetchIndicatorValuesMock).toHaveBeenCalledWith(
      "BTC-USD-PERP.DYDX",
      expect.any(Number),
      120,
      60,
      [{ name: "RelativeStrengthIndex", params: { period: 14 } }],
    );
    expect(result.current["RelativeStrengthIndex_period=14.value"]).toEqual([{ time: 60, value: 55 }]);
  });

  it("refills using the earliest loaded point as before_ns when the visible range nears the start", async () => {
    fetchIndicatorValuesMock.mockResolvedValueOnce(
      page([{ t: 120_000, values: { "RelativeStrengthIndex_period=14.value": 1 } }], true),
    );
    const chart = fakeChart();
    const { result } = renderHook(() => usePickerIndicatorValues("BTC-USD-PERP.DYDX", chart.api, entries));
    await waitFor(() => expect(result.current["RelativeStrengthIndex_period=14.value"]).toHaveLength(1));

    fetchIndicatorValuesMock.mockResolvedValueOnce(
      page([{ t: 60_000, values: { "RelativeStrengthIndex_period=14.value": 2 } }], false),
    );
    chart.fire({ from: 5 as LogicalRange["from"], to: 50 as LogicalRange["to"] });

    await waitFor(() => expect(result.current["RelativeStrengthIndex_period=14.value"]).toHaveLength(2));
    expect(fetchIndicatorValuesMock).toHaveBeenLastCalledWith(
      "BTC-USD-PERP.DYDX",
      120_000 * 1_000_000,
      120,
      60,
      [{ name: "RelativeStrengthIndex", params: { period: 14 } }],
    );
  });

  it("re-fetches from scratch when the entries list changes", async () => {
    fetchIndicatorValuesMock.mockResolvedValue(page([{ t: 60_000, values: { "SimpleMovingAverage.value": 1 } }], false));

    const { rerender } = renderHook(
      ({ e }: { e: IndicatorConfigEntry[] }) => usePickerIndicatorValues("BTC-USD-PERP.DYDX", null, e),
      { initialProps: { e: entries } },
    );
    await waitFor(() => expect(fetchIndicatorValuesMock).toHaveBeenCalledTimes(1));

    const nextEntries: IndicatorConfigEntry[] = [{ name: "SimpleMovingAverage", params: {}, category: "native" }];
    rerender({ e: nextEntries });

    await waitFor(() => expect(fetchIndicatorValuesMock).toHaveBeenCalledTimes(2));
    expect(fetchIndicatorValuesMock).toHaveBeenLastCalledWith(
      "BTC-USD-PERP.DYDX",
      expect.any(Number),
      120,
      60,
      [{ name: "SimpleMovingAverage", params: {} }],
    );
  });
});
