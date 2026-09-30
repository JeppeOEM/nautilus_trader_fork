import { describe, expect, it } from "vitest";

import { MAX_GAP_ROWS_PER_GAP, findGapRuns, formatGapDuration, gapLabel, gapRun, gapRunsBySlot } from "./gaps";

type Point = { time: number; value?: number };
const real = (time: number): Point => ({ time, value: 1 });
const hole = (time: number): Point => ({ time });
const isReal = (p: Point): boolean => "value" in p;

describe("MAX_GAP_ROWS_PER_GAP", () => {
  it("mirrors views/chart_series.py's cap of 720 rows per hole", () => {
    expect(MAX_GAP_ROWS_PER_GAP).toBe(720);
  });
});

describe("gapRun", () => {
  it("fills every missing interval of a two-bar hole", () => {
    expect(gapRun(60, 240, 60)).toEqual([120, 180]);
  });

  it("is empty for adjacent points", () => {
    expect(gapRun(60, 120, 60)).toEqual([]);
    expect(gapRun(120, 60, 60)).toEqual([]);
  });

  it("builds the page seam run", () => {
    expect(gapRun(0, 300, 60)).toEqual([60, 120, 180, 240]);
  });

  it("emits one slot per missing second in Lines mode", () => {
    expect(gapRun(0, 5, 1)).toEqual([1, 2, 3, 4]);
  });

  it("stops at the cap, contiguous from the hole's start", () => {
    const run = gapRun(0, 86_400, 60);
    expect(run).toHaveLength(720);
    expect(run[0]).toBe(60);
    expect(run[719]).toBe(43_200);
  });

  it("honours an explicit cap", () => {
    expect(gapRun(0, 600, 60, 3)).toEqual([60, 120, 180]);
  });
});

describe("findGapRuns", () => {
  it("finds a run between two real points with its real duration", () => {
    const runs = findGapRuns([real(0), hole(60), hole(120), hole(180), hole(240), hole(300), real(360)], isReal);
    expect(runs).toEqual([{ times: [60, 120, 180, 240, 300], durationSeconds: 300, compressed: false }]);
  });

  it("ignores leading whitespace (warm-up / nothing before it) and reads a trailing run by its slots", () => {
    const runs = findGapRuns([hole(0), real(60), real(120), hole(180), hole(240)], isReal);
    expect(runs).toEqual([{ times: [180, 240], durationSeconds: 120, compressed: false }]);
  });

  it("finds every separate run", () => {
    const runs = findGapRuns([real(0), hole(1), real(2), hole(3), hole(4), real(5)], isReal);
    expect(runs.map((r) => r.times)).toEqual([[1], [3, 4]]);
  });

  it("is empty for gap-free data", () => {
    expect(findGapRuns([real(0), real(60)], isReal)).toEqual([]);
    expect(findGapRuns([], isReal)).toEqual([]);
  });

  it("marks a capped run compressed and keeps the hole's real length", () => {
    const step = 60;
    const slots = gapRun(0, 3 * 86_400 + 4 * 3_600 + step, step);
    const data = [real(0), ...slots.map(hole), real(3 * 86_400 + 4 * 3_600 + step)];
    const [run] = findGapRuns(data, isReal);
    expect(run.times).toHaveLength(720);
    expect(run.compressed).toBe(true);
    expect(run.durationSeconds).toBe(3 * 86_400 + 4 * 3_600);
  });

  it("does not mark a run of exactly the cap compressed when the next point follows it", () => {
    const slots = gapRun(0, 721 * 60, 60);
    const [run] = findGapRuns([real(0), ...slots.map(hole), real(721 * 60)], isReal);
    expect(run.times).toHaveLength(720);
    expect(run.compressed).toBe(false);
  });

  it("ends a trailing run at a real point past the data's end (the forming live bar)", () => {
    const step = 60;
    const liveAt = 3 * 86_400 + 4 * 3_600 + step;
    const slots = gapRun(0, liveAt, step);
    const [run] = findGapRuns([real(0), ...slots.map(hole)], isReal, MAX_GAP_ROWS_PER_GAP, liveAt);
    expect(run.compressed).toBe(true);
    expect(run.durationSeconds).toBe(3 * 86_400 + 4 * 3_600);
    expect(gapLabel(run)).toBe("no data · 3d 4h (compressed)");
    // A short run up to the live bar reads the same as without it; one at or before the run's
    // last slot is ignored.
    const [short] = findGapRuns([real(0), hole(60), hole(120)], isReal, MAX_GAP_ROWS_PER_GAP, 180);
    expect(short).toEqual({ times: [60, 120], durationSeconds: 120, compressed: false });
    const [stale] = findGapRuns([real(0), hole(60), hole(120)], isReal, MAX_GAP_ROWS_PER_GAP, 120);
    expect(stale).toEqual({ times: [60, 120], durationSeconds: 120, compressed: false });
  });

  it("indexes every slot of every run by time", () => {
    const runs = findGapRuns([real(0), hole(1), hole(2), real(3)], isReal);
    const bySlot = gapRunsBySlot(runs);
    expect(bySlot.get(1)).toBe(runs[0]);
    expect(bySlot.get(2)).toBe(runs[0]);
    expect(bySlot.has(0)).toBe(false);
  });
});

describe("formatGapDuration", () => {
  it("shows the largest unit and the next one down", () => {
    expect(formatGapDuration(45)).toBe("45s");
    expect(formatGapDuration(300)).toBe("5m");
    expect(formatGapDuration(90)).toBe("1m 30s");
    expect(formatGapDuration(7_200)).toBe("2h");
    expect(formatGapDuration(3 * 86_400 + 4 * 3_600 + 59)).toBe("3d 4h");
  });

  it("never skips a unit: the second part is the unit right below the first, or nothing", () => {
    expect(formatGapDuration(3 * 86_400 + 5 * 60)).toBe("3d");
    expect(formatGapDuration(86_400 + 30)).toBe("1d");
    expect(formatGapDuration(3_600 + 5)).toBe("1h");
  });

  it("rounds to whole seconds, never below 1s", () => {
    expect(formatGapDuration(0)).toBe("1s");
    expect(formatGapDuration(1.4)).toBe("1s");
    expect(formatGapDuration(59.6)).toBe("1m");
  });
});

describe("gapLabel", () => {
  it("labels an uncompressed run with its duration", () => {
    expect(gapLabel({ times: [60, 120, 180, 240, 300], durationSeconds: 300, compressed: false })).toBe("no data · 5m");
  });

  it("says when a run is compressed", () => {
    const times = gapRun(0, 1e9, 60);
    expect(gapLabel({ times, durationSeconds: 3 * 86_400 + 4 * 3_600, compressed: true })).toBe(
      "no data · 3d 4h (compressed)",
    );
  });
});
