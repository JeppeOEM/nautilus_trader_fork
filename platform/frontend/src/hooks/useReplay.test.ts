import { act, renderHook } from "@testing-library/react";
import type { Time } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { gapRun } from "../lib/gaps";
import type { ChartDatum } from "./useCandles";
import { REPLAY_BASE_MS, REPLAY_SPEEDS, stepBarTime, useReplay } from "./useReplay";

const bar = (n: number): ChartDatum => ({ time: n as Time, open: 1, high: 2, low: 1, close: 1.5 });
// 60, 120, [gap 180], 240, 300
const CANDLES: ChartDatum[] = [bar(60), bar(120), { time: 180 as Time }, bar(240), bar(300)];

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("stepBarTime (Story 18.4)", () => {
  it("skips gap entries and returns null at both ends", () => {
    expect(stepBarTime(CANDLES, 120, 1)).toBe(240);
    expect(stepBarTime(CANDLES, 240, -1)).toBe(120);
    expect(stepBarTime(CANDLES, 300, 1)).toBeNull();
    expect(stepBarTime(CANDLES, 60, -1)).toBeNull();
    // a time absent from the data still steps to the nearest bar, not "end"
    expect(stepBarTime(CANDLES, 130, 1)).toBe(240);
  });
});

describe("replay across a long gap run (Story 32.1)", () => {
  // 60, [720 whitespace slots 120 .. 43_260], 86_400: the run is stepped over, never onto.
  const slots = gapRun(60, 86_400, 60).map((time): ChartDatum => ({ time: time as Time }));
  const HOLED: ChartDatum[] = [bar(60), ...slots, bar(86_400)];

  it("steps from the last bar before the run straight to the first bar after it, both ways", () => {
    expect(slots).toHaveLength(720);
    expect(stepBarTime(HOLED, 60, 1)).toBe(86_400);
    expect(stepBarTime(HOLED, 86_400, -1)).toBe(60);
    expect(stepBarTime(HOLED, 600, 1)).toBe(86_400); // from inside the run
  });

  it("step and playback never land on a gap slot, and a gap slot cannot be picked", () => {
    const { result } = renderHook(() => useReplay(HOLED));
    act(() => result.current.startPicking());
    let ok = true;
    act(() => {
      ok = result.current.pick(120);
    });
    expect(ok).toBe(false);
    act(() => {
      result.current.pick(60);
    });

    act(() => result.current.step(1));
    expect(result.current.displayed.at(-1)?.time).toBe(86_400);
    act(() => result.current.step(-1));
    expect(result.current.displayed.at(-1)?.time).toBe(60);

    act(() => result.current.togglePlay());
    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS));
    expect(result.current.displayed.at(-1)?.time).toBe(86_400);
    expect(result.current.isPlaying).toBe(false); // newest bar reached in one tick
  });
});

describe("useReplay (Story 18.4)", () => {
  const picked = (time = 120) => {
    const hook = renderHook(({ candles }) => useReplay(candles), { initialProps: { candles: CANDLES } });
    act(() => hook.result.current.startPicking());
    act(() => {
      hook.result.current.pick(time);
    });
    return hook;
  };

  it("shows every bar until a start is picked, then hides all bars after it (AC #2)", () => {
    const { result } = picked(120);

    expect(result.current.mode).toBe("active");
    expect(result.current.displayed.map((c) => c.time)).toEqual([60, 120]);
    expect(result.current.markerTime).toBe(120);
  });

  it("rejects a pick that is not a real loaded bar", () => {
    const { result } = renderHook(() => useReplay(CANDLES));
    act(() => result.current.startPicking());

    let ok = true;
    act(() => {
      ok = result.current.pick(180); // gap entry
    });

    expect(ok).toBe(false);
    expect(result.current.mode).toBe("picking");
  });

  it("steps one real bar at a time, clamped, and pauses autoplay (AC #4)", () => {
    const { result } = picked(120);

    act(() => result.current.step(1));
    expect(result.current.displayed.at(-1)?.time).toBe(240);
    act(() => result.current.step(1));
    act(() => result.current.step(1));
    expect(result.current.displayed.at(-1)?.time).toBe(300);
    act(() => result.current.step(-1));
    expect(result.current.displayed.at(-1)?.time).toBe(240);
  });

  it("plays one bar per base interval / speed and stops at the newest bar, never wrapping", () => {
    const { result } = picked(120);
    act(() => result.current.togglePlay());
    expect(result.current.isPlaying).toBe(true);

    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS));
    expect(result.current.displayed.at(-1)?.time).toBe(240);
    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS * 5));
    expect(result.current.displayed.at(-1)?.time).toBe(300);
    expect(result.current.isPlaying).toBe(false);
  });

  it("scales the tick interval by speed and cycles a fixed set", () => {
    const { result } = picked(60);
    act(() => result.current.cycleSpeed()); // 1 -> 2
    expect(result.current.speed).toBe(2);
    act(() => result.current.togglePlay());

    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS / 2));
    expect(result.current.displayed.at(-1)?.time).toBe(120);

    for (let i = 0; i < REPLAY_SPEEDS.length; i++) act(() => result.current.cycleSpeed());
    expect(result.current.speed).toBe(2);
  });

  it("keeps the replay position by time when older bars are prepended (AC #7)", () => {
    const { result, rerender } = picked(120);
    const older = [bar(0), bar(30)];

    rerender({ candles: [...older, ...CANDLES] });

    expect(result.current.displayed.map((c) => c.time)).toEqual([0, 30, 60, 120]);
  });

  it("Go to... keeps the replay in progress; cancelling returns to it, exit restores all", () => {
    const { result } = picked(120);

    act(() => result.current.startPicking());
    expect(result.current.mode).toBe("picking");
    expect(result.current.displayed).toBe(CANDLES);
    act(() => result.current.cancelPick());
    expect(result.current.mode).toBe("active");
    expect(result.current.displayed).toHaveLength(2);

    act(() => result.current.exit());
    expect(result.current.mode).toBe("off");
    expect(result.current.displayed).toBe(CANDLES);
    expect(result.current.markerTime).toBeNull();
    expect(result.current.cutoffTime).toBeNull();
  });

  it("exposes the newest revealed time as the cutoff for volume/indicator trimming", () => {
    const { result } = picked(120);
    expect(result.current.cutoffTime).toBe(120);

    act(() => result.current.step(1));
    expect(result.current.cutoffTime).toBe(240);
  });

  it("never falls back to showing everything when the replay time is missing from the data", () => {
    const { result, rerender } = picked(120);

    rerender({ candles: [bar(60), bar(300)] });

    expect(result.current.displayed.map((c) => c.time)).toEqual([60]);
  });

  it("never steps back past the start marker, and reports atStart (DW-146)", () => {
    const { result } = picked(120);
    expect(result.current.atStart).toBe(true);

    act(() => result.current.step(-1));
    expect(result.current.displayed.at(-1)?.time).toBe(120);

    act(() => result.current.step(1));
    expect(result.current.atStart).toBe(false);
    act(() => result.current.step(-1));
    act(() => result.current.step(-1));
    expect(result.current.displayed.at(-1)?.time).toBe(120);
    expect(result.current.atStart).toBe(true);
  });

  it("is atStart when the previous bar is older than a start the data no longer holds", () => {
    const { result, rerender } = picked(240);
    act(() => result.current.step(1)); // 300
    // the start bar 240 vanished (reload/trim); the previous real bar 120 is older than it
    rerender({ candles: [bar(60), bar(120), bar(300)] });

    expect(result.current.atStart).toBe(true);
    act(() => result.current.step(-1));
    expect(result.current.displayed.at(-1)?.time).toBe(300);
  });

  it("flags a missed pick until a successful pick, startPicking, cancelPick or exit (DW-146)", () => {
    const { result } = renderHook(() => useReplay(CANDLES));
    const miss = () =>
      act(() => {
        result.current.pick(180);
      });
    expect(result.current.pickMissed).toBe(false);
    act(() => result.current.startPicking());

    miss();
    expect(result.current.pickMissed).toBe(true);
    expect(result.current.mode).toBe("picking");
    act(() => {
      result.current.pick(120);
    });
    expect(result.current.pickMissed).toBe(false);

    act(() => result.current.startPicking());
    miss();
    act(() => result.current.startPicking());
    expect(result.current.pickMissed).toBe(false);

    miss();
    act(() => result.current.cancelPick());
    expect(result.current.pickMissed).toBe(false);
    expect(result.current.mode).toBe("active");

    act(() => result.current.startPicking());
    miss();
    act(() => result.current.exit());
    expect(result.current.pickMissed).toBe(false);
  });

  it("Play at the newest bar is a no-op that a later bar does not turn into playback (DW-146)", () => {
    const { result, rerender } = picked(300);
    expect(result.current.atEnd).toBe(true);

    act(() => result.current.togglePlay());
    expect(result.current.isPlaying).toBe(false);

    rerender({ candles: [...CANDLES, bar(360)] });
    expect(result.current.atEnd).toBe(false);
    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS * 3));
    expect(result.current.isPlaying).toBe(false);
    expect(result.current.displayed.at(-1)?.time).toBe(300);

    act(() => result.current.togglePlay());
    expect(result.current.isPlaying).toBe(true);
  });

  it("a fresh-but-equal candles array while playing never delays the next tick (DW-146)", () => {
    const { result, rerender } = picked(60);
    act(() => result.current.togglePlay());

    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS / 2));
    rerender({ candles: [...CANDLES] });
    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS / 2));

    expect(result.current.displayed.at(-1)?.time).toBe(120);
  });

  it("the play tick reads the latest candles, not those of when it was armed", () => {
    const { result, rerender } = picked(300);
    rerender({ candles: [...CANDLES, bar(360)] });
    act(() => result.current.togglePlay());

    rerender({ candles: [...CANDLES, bar(330), bar(360)] });
    act(() => vi.advanceTimersByTime(REPLAY_BASE_MS));

    expect(result.current.displayed.at(-1)?.time).toBe(330);
  });

  it("cancelling a first pick returns to off", () => {
    const { result } = renderHook(() => useReplay(CANDLES));
    act(() => result.current.startPicking());

    act(() => result.current.cancelPick());

    expect(result.current.mode).toBe("off");
  });
});
