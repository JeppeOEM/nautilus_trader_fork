import { act, renderHook } from "@testing-library/react";
import type { IChartApi } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useVisibleRange } from "./useVisibleRange";

type Range = { from: number; to: number } | null;

// A manual animation-frame queue: a test decides when "the next frame" happens.
let frames: Map<number, FrameRequestCallback>;
let nextFrameId: number;
const runFrame = (): void => {
  const due = [...frames.values()];
  frames.clear();
  act(() => due.forEach((cb) => cb(0)));
};

beforeEach(() => {
  frames = new Map();
  nextFrameId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frames.set(nextFrameId, cb);
    return nextFrameId++;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
});
afterEach(() => vi.unstubAllGlobals());

/** A chart whose visible time and logical ranges a test sets, then announces with `emit`. */
function fakeChart(initial: Range, initialLogical: Range = { from: 0, to: 10 }) {
  const view = { time: initial, logical: initialLogical };
  const handlers: (() => void)[] = [];
  const timeHandlers: (() => void)[] = [];
  const unsubscribe = vi.fn();
  const unsubscribeTime = vi.fn();
  const getVisibleRange = vi.fn(() => view.time);
  const chart = {
    timeScale: () => ({
      getVisibleRange,
      getVisibleLogicalRange: () => view.logical,
      subscribeVisibleLogicalRangeChange: (h: () => void) => handlers.push(h),
      unsubscribeVisibleLogicalRangeChange: unsubscribe,
      subscribeVisibleTimeRangeChange: (h: () => void) => timeHandlers.push(h),
      unsubscribeVisibleTimeRangeChange: unsubscribeTime,
    }),
  } as unknown as IChartApi;
  const emit = (time: Range, logical: Range = view.logical): void => {
    view.time = time;
    view.logical = logical;
    handlers.forEach((h) => h());
  };
  /** Only the time range changed (new data under unchanged bar indices). */
  const emitTime = (time: Range): void => {
    view.time = time;
    timeHandlers.forEach((h) => h());
  };
  return { chart, emit, emitTime, unsubscribe, unsubscribeTime, getVisibleRange };
}

describe("useVisibleRange (Story 18.7, DW-151)", () => {
  it("is null without a chart, then seeds synchronously from the current visible range", () => {
    const { chart } = fakeChart({ from: 10, to: 20 });
    const { result, rerender } = renderHook(({ c }) => useVisibleRange(c), {
      initialProps: { c: null as IChartApi | null },
    });
    expect(result.current).toBeNull();

    rerender({ c: chart });

    expect(result.current).toEqual({ from: 10, to: 20, pastOldest: false });
  });

  it("coalesces a burst of range changes into one read after the frame", () => {
    const { chart, emit, getVisibleRange } = fakeChart({ from: 10, to: 20 });
    const { result } = renderHook(() => useVisibleRange(chart));
    getVisibleRange.mockClear();

    for (let i = 1; i <= 10; i++) emit({ from: 10 + i, to: 20 + i });
    expect(result.current).toEqual({ from: 10, to: 20, pastOldest: false }); // nothing before the frame
    expect(frames.size).toBe(1);

    runFrame();

    expect(getVisibleRange).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ from: 20, to: 30, pastOldest: false });
  });

  it("re-reads when only the time range changes, coalesced with logical changes in the same frame", () => {
    const { chart, emit, emitTime, getVisibleRange } = fakeChart({ from: 10, to: 20 });
    const { result } = renderHook(() => useVisibleRange(chart));
    getVisibleRange.mockClear();

    emitTime({ from: 10, to: 25 }); // a replay step revealed a bar into right whitespace
    emit({ from: 10, to: 26 });
    expect(frames.size).toBe(1);
    runFrame();

    expect(getVisibleRange).toHaveBeenCalledTimes(1);
    expect(result.current).toEqual({ from: 10, to: 26, pastOldest: false });
  });

  it("keeps identity when the same range is re-reported, and clears on null", () => {
    const { chart, emit } = fakeChart({ from: 10, to: 20 });
    const { result } = renderHook(() => useVisibleRange(chart));
    const same = result.current;

    emit({ from: 10, to: 20 });
    runFrame();
    expect(result.current).toBe(same);

    emit(null);
    runFrame();
    expect(result.current).toBeNull();
  });

  it("flags a view that reaches past the oldest loaded bar, and only then", () => {
    const { chart, emit } = fakeChart({ from: 10, to: 20 }, { from: -0.5, to: 9 });
    const { result } = renderHook(() => useVisibleRange(chart));
    expect(result.current!.pastOldest).toBe(false); // bar 0's own left half

    emit({ from: 10, to: 20 }, { from: -3, to: 9 }); // same clamped times, empty space to the left
    runFrame();
    expect(result.current).toEqual({ from: 10, to: 20, pastOldest: true });

    emit({ from: 10, to: 20 }, { from: 0, to: 9 }); // older bars were prepended under the view
    runFrame();
    expect(result.current!.pastOldest).toBe(false);
  });

  it("unsubscribes and cancels a pending frame on unmount", () => {
    const { chart, emit, unsubscribe, unsubscribeTime } = fakeChart({ from: 10, to: 20 });
    const { unmount } = renderHook(() => useVisibleRange(chart));
    emit({ from: 11, to: 21 });
    expect(frames.size).toBe(1);

    unmount();

    expect(unsubscribe).toHaveBeenCalledTimes(1);
    expect(unsubscribeTime).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);
  });
});
