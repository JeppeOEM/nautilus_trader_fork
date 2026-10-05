import type { ChartDatum, VolumeDatum } from "../hooks/useCandles";

export interface ProfileCandle {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ProfileRow {
  priceLow: number;
  priceHigh: number;
  upVolume: number;
  downVolume: number;
}

/**
 * What a candle contributes to a row. `"volume"`: its volume, spread evenly over the rows it
 * touches (every profile since Story 18.5). `"time"` (Story 32.7, the TPO): one count in EVERY
 * row it touches, undivided, whatever its volume -- a zero-volume candle still counts.
 */
export type ProfileWeight = "volume" | "time";

export interface VolumeProfile {
  /** One per price bucket, low -> high. Empty when there was nothing to profile. */
  rows: ProfileRow[];
  /** Centre price of the fullest row (the lowest such row on a tie). */
  poc: number;
  /** Top of the value area. */
  vah: number;
  /** Bottom of the value area. */
  val: number;
  /** The sum of every row's weight: volume, or touches for `weight: "time"`. */
  totalVolume: number;
}

/** Upper bound on rows the engine will allocate, whatever the caller asks for. */
export const MAX_PROFILE_ROWS = 500;

const EMPTY: VolumeProfile = { rows: [], poc: 0, vah: 0, val: 0, totalVolume: 0 };

/**
 * Pairs each real candle with its volume by time. Whitespace/gap entries (no OHLC, or no
 * volume `value`) are dropped -- a gap must never contribute a bar or a volume.
 */
export function joinCandlesWithVolume(
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
): ProfileCandle[] {
  const volumeByTime = new Map<unknown, number>();
  for (const v of volume) if ("value" in v) volumeByTime.set(v.time, v.value);
  const joined: ProfileCandle[] = [];
  for (const c of candles) {
    if (!("open" in c)) continue;
    const v = volumeByTime.get(c.time);
    if (v !== undefined) joined.push({ open: c.open, high: c.high, low: c.low, close: c.close, volume: v });
  }
  return joined;
}

/**
 * The profile of the candles whose time lies in [startTime, endTime] (either order) -- the
 * one call every range-based variant makes: slice, pair with volume, build. Settings carry
 * the value area as a percent, the engine takes a fraction.
 */
export function buildRangeProfile(
  candles: readonly ChartDatum[],
  volume: readonly VolumeDatum[],
  startTime: number,
  endTime: number,
  settings: Pick<VolumeProfileSettings, "rowCount" | "valueAreaPercent">,
  weight: ProfileWeight = "volume",
): VolumeProfile {
  const from = Math.min(startTime, endTime);
  const to = Math.max(startTime, endTime);
  const inRange = <T extends { time: unknown }>(d: T): boolean => (d.time as number) >= from && (d.time as number) <= to;
  return buildVolumeProfile(
    joinCandlesWithVolume(candles.filter(inRange), volume.filter(inRange)),
    settings.rowCount,
    settings.valueAreaPercent / 100,
    weight,
  );
}

function extendValueArea(totals: readonly number[], poc: number, target: number): [number, number] {
  let lo = poc;
  let hi = poc;
  let accumulated = totals[poc];
  while (accumulated < target && (lo > 0 || hi < totals.length - 1)) {
    const above = hi + 1 < totals.length ? totals[hi + 1] : -1;
    const below = lo - 1 >= 0 ? totals[lo - 1] : -1;
    if (above >= below) {
      hi++;
      accumulated += above;
    } else {
      lo--;
      accumulated += below;
    }
  }
  return [lo, hi];
}

/**
 * The candles a profile is built from, each with its low <= high: finite OHLC (and, for the volume
 * weight, a finite volume above zero -- a zero-volume candle carries nothing to profile, so it does
 * not widen the range either; the time weight counts it). A low > high candle is bad data, not a
 * reason to drop or corrupt it: ordered. Shared by the engine and the TPO's per-row detail
 * (`lib/tpo.ts`), so the two can never disagree on which candles count.
 */
export function usableCandles(candles: readonly ProfileCandle[], weight: ProfileWeight): ProfileCandle[] {
  return candles
    .filter((c) => {
      const finite = [c.open, c.high, c.low, c.close].every(Number.isFinite);
      return weight === "time" ? finite : finite && Number.isFinite(c.volume) && c.volume > 0;
    })
    .map((c) => (c.low <= c.high ? c : { ...c, low: c.high, high: c.low }));
}

/**
 * The first and last row (inclusive) a candle's low..high touches, in a profile of `count` rows of
 * `size` starting at `min`. A high exactly on a row boundary belongs to the row below it (floor
 * would credit the next row too and dilute the candle's weight); a flat profile has one row.
 */
export function touchedRows(low: number, high: number, min: number, size: number, count: number): [number, number] {
  // Position in row units, snapped when float division lands a hair off a whole number.
  const position = (price: number): number => {
    const x = (price - min) / size;
    return Math.abs(x - Math.round(x)) < 1e-9 ? Math.round(x) : x;
  };
  const clampRow = (i: number): number => Math.min(count - 1, Math.max(0, i));
  const first = size === 0 ? 0 : clampRow(Math.floor(position(low)));
  const last = size === 0 ? 0 : Math.max(first, clampRow(Math.ceil(position(high)) - 1));
  return [first, last];
}

/**
 * The one Volume Profile calculation behind every variant (Story 18.5, spec §A7.0; the nine
 * TradingView profile tools of Story 32.7 all call it): bucket the slice's price range into
 * `rowCount` equal rows, give each candle's weight to the rows its low..high touches, split up
 * (close >= open) / down weight, POC = fullest row, Value Area grown outward from the POC (fuller
 * neighbour first, the upper one on a tie) until it holds `valueAreaPct` (a fraction) of the total.
 *
 * `weight` "volume" (the default) spreads the candle's volume evenly over the rows it touches;
 * "time" (the TPO) counts the candle once in every row it touches.
 *
 * Known limit: the candle-level spread, not the trades inside the candle, so a candle's volume is
 * an even smear over its range. Upgrade path: Story 32.8's per-trade footprint from the raw trade
 * archive.
 */
export function buildVolumeProfile(
  candles: readonly ProfileCandle[],
  rowCount: number,
  valueAreaPct = 0.7,
  weight: ProfileWeight = "volume",
): VolumeProfile {
  // Floor first: 0.5 and NaN must not slip past a `< 1` check into a zero-row profile.
  const requestedRows = Math.floor(rowCount);
  const usable = usableCandles(candles, weight);
  if (usable.length === 0 || !(requestedRows >= 1)) return EMPTY;

  // Loops, not Math.min(...array): a long history of base-timeframe bars can exceed the
  // engine's argument-count limit.
  let min = Infinity;
  let max = -Infinity;
  for (const c of usable) {
    min = Math.min(min, c.low);
    max = Math.max(max, c.high);
  }
  // A flat slice has no range to divide: one row at that price.
  const count = max === min ? 1 : Math.min(MAX_PROFILE_ROWS, requestedRows);
  const size = (max - min) / count;
  const rows: ProfileRow[] = Array.from({ length: count }, (_, i) => ({
    priceLow: min + i * size,
    priceHigh: i === count - 1 ? max : min + (i + 1) * size,
    upVolume: 0,
    downVolume: 0,
  }));

  for (const c of usable) {
    const [first, last] = touchedRows(c.low, c.high, min, size, count);
    const share = weight === "time" ? 1 : c.volume / (last - first + 1);
    const side = c.close >= c.open ? "upVolume" : "downVolume";
    for (let i = first; i <= last; i++) rows[i][side] += share;
  }

  const totals = rows.map((r) => r.upVolume + r.downVolume);
  const totalVolume = totals.reduce((a, b) => a + b, 0);
  const poc = totals.indexOf(totals.reduce((a, b) => Math.max(a, b), 0));
  // A NaN percentage falls back to the default rather than collapsing the area to the POC.
  const pct = Number.isFinite(valueAreaPct) ? Math.min(1, Math.max(0, valueAreaPct)) : 0.7;
  const [lo, hi] = extendValueArea(totals, poc, pct * totalVolume);
  return {
    rows,
    poc: (rows[poc].priceLow + rows[poc].priceHigh) / 2,
    vah: rows[hi].priceHigh,
    val: rows[lo].priceLow,
    totalVolume,
  };
}

// Story 18.5 (AC #4): the settings every variant shares (the panel is
// components/chart/VolumeProfileSettings.tsx); kept here so the panel file only exports a component.
export interface VolumeProfileSettings {
  rowCount: number;
  /** A percentage (70), not a fraction -- divide by 100 for `buildVolumeProfile`. */
  valueAreaPercent: number;
  upColor: string;
  downColor: string;
  showPoc: boolean;
  showValueArea: boolean;
}

export const DEFAULT_VOLUME_PROFILE_SETTINGS: VolumeProfileSettings = {
  rowCount: 24,
  valueAreaPercent: 70,
  upColor: "#55ff55",
  downColor: "#ff5555",
  showPoc: true,
  showValueArea: true,
};
