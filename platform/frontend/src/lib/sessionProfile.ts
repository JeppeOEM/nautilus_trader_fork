import type { ChartDatum, VolumeDatum } from "../hooks/useCandles";
import type { LiveBar } from "../hooks/useLiveCandle";
import {
  buildVolumeProfile,
  type ProfileCandle,
  type ProfileWeight,
  type VolumeProfile,
  type VolumeProfileSettings,
} from "./volumeProfile";

/** Story 18.8/18.9: the recurring windows a profile can be grouped by. */
export type SessionPeriod = "4h" | "daily" | "weekly" | "monthly";

const HOUR = 3600;
const DAY = 86_400;

// UTC throughout: candle times are UTC seconds, and UTC is the only calendar-day convention
// this codebase uses where a "day" exists at all (the bots' per-UTC-day PnL) -- the
// rankings are rolling 24h windows, not calendar sessions. Weeks start Monday 00:00 UTC.
/** Start (UTC seconds) of the period containing `timeSec`. */
export function periodStart(timeSec: number, period: SessionPeriod): number {
  switch (period) {
    case "4h":
      return Math.floor(timeSec / (4 * HOUR)) * 4 * HOUR;
    case "daily":
      return Math.floor(timeSec / DAY) * DAY;
    case "weekly": {
      const dayIndex = Math.floor(timeSec / DAY);
      // 1970-01-01 was a Thursday, so (dayIndex + 3) % 7 = days since that week's Monday.
      return (dayIndex - ((dayIndex + 3) % 7)) * DAY;
    }
    case "monthly": {
      const d = new Date(timeSec * 1000);
      return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
    }
  }
}

/** Start of the period `back` periods before the one containing `timeSec` (0 = that one). */
export function periodStartBack(timeSec: number, period: SessionPeriod, back: number): number {
  if (period === "monthly") {
    const d = new Date(timeSec * 1000);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - back, 1) / 1000;
  }
  const step = { "4h": 4 * HOUR, daily: DAY, weekly: 7 * DAY }[period];
  return periodStart(timeSec, period) - back * step;
}

/** The candle bar size each period is profiled from: the finest (1 minute) where the
 * needed history is affordable to page, coarser for the long periods (a month of 1-minute
 * bars is ~43k rows). */
export function sessionBarSeconds(period: SessionPeriod): number {
  return { "4h": 60, daily: 60, weekly: 300, monthly: 900 }[period];
}

export interface SessionProfileEntry {
  /** The period's start (UTC seconds) -- also the stable identity of the session. */
  periodStart: number;
  /** First and last real bar times inside the period. */
  startTime: number;
  endTime: number;
  profile: VolumeProfile;
  /** The period's bars, ascending: what the TPO's per-row letters and initial balance read. */
  bars: readonly TimedBar[];
}

interface CacheEntry {
  /** Everything that can change a session's profile without changing its bar count or
   * first/last time -- a refresh replaces the forming bar in place (same `t`, new
   * high/low/close/volume), so identity by count + times alone would serve it stale. */
  fingerprint: string;
  profile: VolumeProfile;
}

/** Mutable memo shared across `buildSessionProfiles` calls (see there). */
export type SessionProfileCache = Map<string, CacheEntry>;

export interface TimedBar extends ProfileCandle {
  time: number;
}

/**
 * `candles` / `volume` with the live forming bar appended (Story 32.7: an anchored drawing runs to
 * the latest bar, the forming one included, so one placed on the forming bar draws at once). The
 * inputs come back unchanged with no forming bar or one not newer than the newest point.
 */
export function withFormingBar(
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
  bar: LiveBar | null,
): { candles: readonly ChartDatum[]; volume: readonly VolumeDatum[] } {
  if (bar === null) return { candles, volume };
  const newest = candles.at(-1)?.time as number | undefined;
  if (newest !== undefined && (bar.time as number) <= newest) return { candles, volume };
  const { time, open, high, low, close } = bar;
  return { candles: [...candles, { time, open, high, low, close }], volume: [...volume, { time, value: bar.volume }] };
}

/**
 * The real candles paired with their volume by time (gap slots dropped). Under the volume weight a
 * candle with no volume datum is dropped; under the time weight (the TPO) it still counts, with
 * volume 0, because a TPO counts every real candle whatever its volume.
 */
export function timedBars(
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
  weight: ProfileWeight = "volume",
): TimedBar[] {
  const volumeByTime = new Map<number, number>();
  for (const v of volume) if ("value" in v) volumeByTime.set(v.time as number, v.value);
  const bars: TimedBar[] = [];
  for (const c of candles) {
    if (!("open" in c)) continue;
    const v = volumeByTime.get(c.time as number) ?? (weight === "time" ? 0 : undefined);
    if (v !== undefined) bars.push({ time: c.time as number, open: c.open, high: c.high, low: c.low, close: c.close, volume: v });
  }
  return bars;
}

/**
 * One profile per period for the last `sessionCount` periods that hold data (spec §A7.2/
 * §A7.3): candles grouped by UTC period, each group built independently -- its own POC/
 * VAH/VAL, never merged. Only the newest (in-progress) period ever changes as bars arrive,
 * so a session whose bar count and first/last bar are unchanged is served from `cache`
 * instead of being rebuilt (the cache is pruned to the sessions still in view).
 *
 * `weight` "time" builds the TPO's counts instead of volume (Story 32.7).
 *
 * `completeFrom`: periods starting before it are omitted -- they are only partially
 * covered by the fetched history, and a truncated profile would silently misstate the
 * session. Pass null when the history is complete (or exhausted).
 */
export function buildSessionProfiles(
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
  period: SessionPeriod,
  sessionCount: number,
  settings: Pick<VolumeProfileSettings, "rowCount" | "valueAreaPercent">,
  cache: SessionProfileCache,
  completeFrom: number | null = null,
  weight: ProfileWeight = "volume",
): SessionProfileEntry[] {
  const groups = new Map<number, TimedBar[]>();
  for (const bar of timedBars(candles, volume, weight)) {
    const start = periodStart(bar.time, period);
    if (completeFrom !== null && start < completeFrom) continue;
    const group = groups.get(start);
    if (group) group.push(bar);
    else groups.set(start, [bar]);
  }

  // slice(-0) would keep everything, so a zero/negative count is handled explicitly.
  const count = Math.floor(sessionCount);
  const starts = count >= 1 ? [...groups.keys()].sort((a, b) => a - b).slice(-count) : [];
  const used = new Set<string>();
  const entries = starts.map((start): SessionProfileEntry => {
    const bars = groups.get(start)!;
    const first = bars[0].time;
    const newest = bars[bars.length - 1];
    const last = newest.time;
    const key = `${period}|${start}|${settings.rowCount}|${settings.valueAreaPercent}|${weight}`;
    used.add(key);
    const totalVolume = bars.reduce((sum, b) => sum + b.volume, 0);
    const fingerprint = [bars.length, first, last, totalVolume, newest.high, newest.low, newest.close].join("|");
    const hit = cache.get(key);
    if (hit && hit.fingerprint === fingerprint) {
      return { periodStart: start, startTime: first, endTime: last, profile: hit.profile, bars };
    }
    const profile = buildVolumeProfile(bars, settings.rowCount, settings.valueAreaPercent / 100, weight);
    cache.set(key, { fingerprint, profile });
    return { periodStart: start, startTime: first, endTime: last, profile, bars };
  });
  for (const key of [...cache.keys()]) if (!used.has(key)) cache.delete(key);
  return entries;
}

/** Story 18.8 AC #3: SVP HD is a preset of the same component, not a fork -- only the
 * default row count and the redraw-on-zoom flag differ. */
export const SESSION_PRESETS = {
  svp: { label: "Session Volume Profile", period: "daily" as SessionPeriod, rowCount: 24, respondsToZoom: false },
  "svp-hd": { label: "Session Volume Profile HD", period: "daily" as SessionPeriod, rowCount: 120, respondsToZoom: true },
  // Story 18.9: the same component with a user-chosen period (`period` here is only the
  // default the dropdown starts on).
  pvp: { label: "Periodic Volume Profile", period: "weekly" as SessionPeriod, rowCount: 24, respondsToZoom: false },
  // Story 32.7: the same slot (still ONE session-type profile per chart) with two more presets. The
  // TPO counts 30-minute candle touches per session/day (`lib/tpo.ts`); the Auto Anchored profile
  // spans an anchor to the latest bar (`lib/autoAnchor.ts`), so its `period` is only the unused default.
  tpo: { label: "Time Price Opportunity (TPO)", period: "daily" as SessionPeriod, rowCount: 48, respondsToZoom: false },
  auto: { label: "Auto Anchored Volume Profile", period: "daily" as SessionPeriod, rowCount: 24, respondsToZoom: false },
} as const;

/** The period dropdown's fixed set (Story 18.9 AC #1) -- nothing user-defined. */
export const SESSION_PERIODS: readonly SessionPeriod[] = ["4h", "daily", "weekly", "monthly"];

export type SessionPreset = keyof typeof SESSION_PRESETS;

/** The shared settings plus how many past sessions to render (AC #4). */
export type SessionProfileSettings = VolumeProfileSettings & { sessionCount: number };

export const MAX_SESSIONS = 10;
export const DEFAULT_SESSION_COUNT = 5;

/**
 * A session's drawable time span: the first and last bar the CHART holds inside
 * [startTime, endTime]. The session's own candles come from a separate fetch that reaches
 * further back than the chart's loaded window, but a time anchor only resolves to a pixel
 * where the chart has a bar -- so a session partly outside the loaded window is drawn over
 * the part that is inside it (its profile still covers the whole session). Null when the
 * chart holds none of it.
 */
export function drawableSpan(
  chartCandles: readonly ChartDatum[],
  startTime: number,
  endTime: number,
): { startTime: number; endTime: number } | null {
  let first: number | null = null;
  let last: number | null = null;
  for (const c of chartCandles) {
    if (!("open" in c)) continue;
    const t = c.time as number;
    if (t < startTime || t > endTime) continue;
    if (first === null) first = t;
    last = t;
  }
  return first === null || last === null ? null : { startTime: first, endTime: last };
}
