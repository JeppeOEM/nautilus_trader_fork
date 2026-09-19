import { act, renderHook } from "@testing-library/react";
import type { IChartApi, Time } from "lightweight-charts";
import { describe, expect, it, vi } from "vitest";

import { useVisibleRange } from "./useVisibleRange";

type Handler = (range: { from: Time; to: Time } | null) => void;

function fakeChart(initial: { from: Time; to: Time } | null) {
  const handlers: Handler[] = [];
  const unsubscribe = vi.fn();
  const chart = {
    timeScale: () => ({
      getVisibleRange: () => initial,
      subscribeVisibleTimeRangeChange: (h: Handler) => handlers.push(h),
      unsubscribeVisibleTimeRangeChange: unsubscribe,
    }),
  } as unknown as IChartApi;
  return { chart, emit: (r: { from: Time; to: Time } | null) => handlers.forEach((h) => h(r)), unsubscribe };
}

describe("useVisibleRange (Story 18.7)", () => {
  it("is null without a chart, then seeds from the current visible range", () => {
    const { chart } = fakeChart({ from: 10 as Time, to: 20 as Time });
    const { result, rerender } = renderHook(({ c }) => useVisibleRange(c), {
      initialProps: { c: null as IChartApi | null },
    });
    expect(result.current).toBeNull();

    rerender({ c: chart });

    expect(result.current).toEqual({ from: 10, to: 20 });
  });

  it("follows every change, keeps identity when the same range is re-reported, and clears on null", () => {
    const { chart, emit } = fakeChart({ from: 10 as Time, to: 20 as Time });
    const { result } = renderHook(() => useVisibleRange(chart));

    act(() => emit({ from: 30 as Time, to: 40 as Time }));
    expect(result.current).toEqual({ from: 30, to: 40 });
    const same = result.current;
    act(() => emit({ from: 30 as Time, to: 40 as Time }));
    expect(result.current).toBe(same);

    act(() => emit(null));
    expect(result.current).toBeNull();
  });

  it("unsubscribes on unmount", () => {
    const { chart, unsubscribe } = fakeChart(null);
    const { unmount } = renderHook(() => useVisibleRange(chart));

    unmount();

    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });
});
