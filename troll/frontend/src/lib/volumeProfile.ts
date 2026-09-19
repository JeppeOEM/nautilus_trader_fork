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

export interface VolumeProfile {
  /** One per price bucket, low -> high. Empty when there was nothing to profile. */
  rows: ProfileRow[];
  /** Centre price of the highest-volume row (the lowest such row on a tie). */
  poc: number;
  /** Top of the value area. */
  vah: number;
  /** Bottom of the value area. */
  val: number;
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
 * The one Volume Profile calculation behind every variant (Story 18.5, spec §A7.0):
 * bucket the slice's price range into `rowCount` equal rows, spread each candle's volume
 * evenly over every row its low..high touches, split up (close >= open) / down volume,
 * POC = fullest row, Value Area grown outward from the POC (fuller neighbour first, the
 * upper one on a tie) until it holds `valueAreaPct` (a fraction) of the total.
 */
export function buildVolumeProfile(
  candles: readonly ProfileCandle[],
  rowCount: number,
  valueAreaPct = 0.7,
): VolumeProfile {
  // Floor first: 0.5 and NaN must not slip past a `< 1` check into a zero-row profile.
  const requestedRows = Math.floor(rowCount);
  // Zero-volume candles carry nothing to profile, so they don't widen the range either.
  // A low > high candle is bad data, not a reason to drop or corrupt its volume: order it.
  const usable = candles
    .filter((c) => [c.open, c.high, c.low, c.close, c.volume].every(Number.isFinite) && c.volume > 0)
    .map((c) => (c.low <= c.high ? c : { ...c, low: c.high, high: c.low }));
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

  // Position in row units, snapped when float division lands a hair off a whole number.
  const position = (price: number): number => {
    const x = (price - min) / size;
    return Math.abs(x - Math.round(x)) < 1e-9 ? Math.round(x) : x;
  };
  const clampRow = (i: number): number => Math.min(count - 1, Math.max(0, i));

  for (const c of usable) {
    const first = size === 0 ? 0 : clampRow(Math.floor(position(c.low)));
    // A high exactly on a row boundary belongs to the row below it -- floor would credit
    // the next row too and dilute the candle's volume.
    const last = size === 0 ? 0 : Math.max(first, clampRow(Math.ceil(position(c.high)) - 1));
    const share = c.volume / (last - first + 1);
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
