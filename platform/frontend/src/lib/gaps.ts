// Story 32.1: the one gap vocabulary of the frontend. A hole in the data takes one chart
// slot per missing interval -- native lightweight-charts whitespace (`{ time }`), never a
// fabricated OHLC/value (AD-F6) -- so a six-hour outage reads as six hours, not one bar.
// Every page/refetch seam builds its run through `gapRun`; the chart finds runs in the price
// series with `findGapRuns` and labels them with `gapLabel`.

/**
 * Most whitespace slots one hole may take. Mirrors `views/chart_series.py`'s
 * `MAX_GAP_ROWS_PER_GAP` (both sides assert 720 in a test) so a seam run and a backend run
 * compress identically: 12 h of 1m bars, 12 min of Lines-mode seconds.
 *
 * Known limit: a hole longer than the cap shows only its first 720 slots, contiguous from its
 * start, then the next real point -- the chart says "(compressed)" (detected from the spacing
 * in `findGapRuns`), it never hides the hole, but the axis is no longer time-linear across it.
 * Upgrade path: a gap row carrying `span_ms`, drawn as one wide band of the hole's real width.
 */
export const MAX_GAP_ROWS_PER_GAP = 720;

/** One maximal run of whitespace slots in the price series that follows a real point. */
export interface GapRun {
  /** Slot times, ascending, in chart seconds. */
  times: number[];
  /** The hole's real length: from one step after the last real point to the next real one. */
  durationSeconds: number;
  /** The cap was hit and the next real point lies further than one step past the last slot. */
  compressed: boolean;
}

/**
 * The whitespace slot times between two points: `after + k*step` for k = 1, 2, ... while the
 * time is `< before`, at most `cap` of them. Empty when the two points are one step apart.
 */
export function gapRun(
  afterExclusive: number,
  beforeExclusive: number,
  stepSeconds: number,
  cap = MAX_GAP_ROWS_PER_GAP,
): number[] {
  const times: number[] = [];
  if (!(stepSeconds > 0)) return times;
  for (let k = 1; times.length < cap; k++) {
    const time = afterExclusive + k * stepSeconds;
    if (time >= beforeExclusive) break;
    times.push(time);
  }
  return times;
}

function closeRun(times: number[], prevReal: number, nextReal: number | undefined, cap: number): GapRun {
  const step = times[0] - prevReal;
  const last = times[times.length - 1];
  return {
    times,
    durationSeconds: (nextReal ?? last + step) - prevReal - step,
    compressed: times.length >= cap && nextReal !== undefined && nextReal - last > step,
  };
}

/**
 * Every maximal run of non-real points that has a real point before it. Leading whitespace is
 * not a gap (nothing was lost before the first point). A trailing run ends at `realAfterEnd`
 * when given -- a real point past the data's end, i.e. the forming live bar the chart draws
 * with `update()` -- so a capped run up to it reads its real length and "(compressed)";
 * without one, the run's duration is its slots.
 */
export function findGapRuns<T extends { time: unknown }>(
  data: readonly T[],
  isReal: (point: T) => boolean,
  cap = MAX_GAP_ROWS_PER_GAP,
  realAfterEnd?: number,
): GapRun[] {
  const runs: GapRun[] = [];
  let prevReal: number | undefined;
  let pending: number[] = [];
  for (const point of data) {
    const time = point.time as number;
    if (isReal(point)) {
      if (pending.length > 0 && prevReal !== undefined) runs.push(closeRun(pending, prevReal, time, cap));
      pending = [];
      prevReal = time;
    } else if (prevReal !== undefined) {
      pending.push(time);
    }
  }
  if (pending.length > 0 && prevReal !== undefined) {
    const next = realAfterEnd !== undefined && realAfterEnd > pending[pending.length - 1] ? realAfterEnd : undefined;
    runs.push(closeRun(pending, prevReal, next, cap));
  }
  return runs;
}

/** Slot time -> its run, for the crosshair readout. */
export function gapRunsBySlot(runs: readonly GapRun[]): Map<number, GapRun> {
  const bySlot = new Map<number, GapRun>();
  for (const run of runs) for (const time of run.times) bySlot.set(time, run);
  return bySlot;
}

const UNITS: readonly (readonly [string, number])[] = [
  ["d", 86_400],
  ["h", 3_600],
  ["m", 60],
  ["s", 1],
];

/**
 * The largest non-zero unit of d/h/m/s plus the unit right below it when that is non-zero,
 * e.g. "45s", "5m", "1m 30s", "3d 4h", "3d" for 3d 0h 5m (never "3d 5m", which would hide the
 * skipped unit). Rounded to whole seconds, at least 1s.
 */
export function formatGapDuration(seconds: number): string {
  const total = Math.max(1, Math.round(seconds));
  const major = UNITS.findIndex(([, size]) => total >= size);
  const [unit, size] = UNITS[major];
  const parts = [`${Math.floor(total / size)}${unit}`];
  const minor = UNITS[major + 1];
  if (minor !== undefined) {
    const count = Math.floor((total % size) / minor[1]);
    if (count > 0) parts.push(`${count}${minor[0]}`);
  }
  return parts.join(" ");
}

export function gapLabel(run: GapRun): string {
  return `no data · ${formatGapDuration(run.durationSeconds)}${run.compressed ? " (compressed)" : ""}`;
}
