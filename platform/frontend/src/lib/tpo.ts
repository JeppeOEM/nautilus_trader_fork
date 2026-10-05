import { type ProfileCandle, type VolumeProfile, touchedRows, usableCandles } from "./volumeProfile";

/**
 * The Time Price Opportunity (TPO) profile's per-row detail (Story 32.7). The counts themselves come
 * from the one engine (`buildVolumeProfile(..., "time")`: one count per candle per row it touches);
 * this module adds what only a TPO draws: the blocks of a row (capped, the overflow as one longer
 * bar), the letter of each touch and the initial balance. No DOM, so it is tested on its own.
 *
 * Known limit: the TPO is fetched at 30-minute bars whatever its period, so a weekly or monthly TPO
 * pages ~336 / ~1,440 bars per week / 30 days per profile (the session fetch caps at 80 pages).
 * Upgrade path: a coarser TPO candle for the long periods, or a server-side TPO read.
 *
 * Known limit: a TPO period is one 30-minute candle (`TPO_BAR_SECONDS`), the classic Market Profile
 * period, not a setting. Upgrade path: make the period a layout key if a 15- or 60-minute TPO is
 * wanted.
 */

/** The candle size a TPO session is counted from: one letter / block per 30 minutes. */
export const TPO_BAR_SECONDS = 1800;
/** Most blocks drawn in one row: a row touched more often draws this many and one longer bar. */
export const TPO_MAX_BLOCKS_PER_ROW = 30;
/** The default initial balance: the first hour, 2 x 30m. */
export const DEFAULT_IB_MINUTES = 60;
/** The bounds of the initial balance setting (mirrors `views.preferences.MAX_IB_MINUTES`). */
export const MIN_IB_MINUTES = 1;
export const MAX_IB_MINUTES = 1440;

const LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";

/** The letter of the `index`-th candle of a session: A..Z, a..z, then around again. */
export function tpoLetter(index: number): string {
  return LETTERS[((index % LETTERS.length) + LETTERS.length) % LETTERS.length];
}

export interface TpoTouch {
  letter: string;
  /** The touching candle closed at or above its open. */
  up: boolean;
}

export interface TpoRow {
  priceLow: number;
  priceHigh: number;
  /** Every candle that touched the row. */
  count: number;
  /** Blocks drawn: `min(count, maxBlocks)`. */
  blocks: number;
  /** The touches beyond the cap, drawn as ONE bar `overflow` blocks long (0 when none). */
  overflow: number;
  /** The first `blocks` touches in time order (what the blocks and letters show). */
  touches: TpoTouch[];
}

/**
 * The rows of `profile` (built with the time weight from the same `candles`, ascending) with each
 * row's touches: the candle that touched it, in time order. The row mapping is the engine's own
 * (`touchedRows`), so a row's `count` always equals the profile's total for it.
 */
export function tpoRows(
  profile: VolumeProfile,
  candles: readonly ProfileCandle[],
  maxBlocks: number = TPO_MAX_BLOCKS_PER_ROW,
): TpoRow[] {
  const count = profile.rows.length;
  if (count === 0) return [];
  const min = profile.rows[0].priceLow;
  const size = (profile.rows[count - 1].priceHigh - min) / count;
  const rows: TpoRow[] = profile.rows.map((r) => ({
    priceLow: r.priceLow,
    priceHigh: r.priceHigh,
    count: 0,
    blocks: 0,
    overflow: 0,
    touches: [],
  }));
  // The letter follows the candle's place in the session, usable or not: a skipped candle must not
  // shift every later letter.
  candles.forEach((candle, index) => {
    const [usable] = usableCandles([candle], "time");
    if (!usable) return;
    const [first, last] = touchedRows(usable.low, usable.high, min, size, count);
    const touch: TpoTouch = { letter: tpoLetter(index), up: candle.close >= candle.open };
    for (let i = first; i <= last; i++) {
      rows[i].count += 1;
      if (rows[i].touches.length < maxBlocks) rows[i].touches.push(touch);
    }
  });
  for (const row of rows) {
    row.blocks = Math.min(row.count, maxBlocks);
    row.overflow = Math.max(0, row.count - maxBlocks);
  }
  return rows;
}

export interface InitialBalance {
  /** The first and last bar of the band (UTC seconds). */
  startTime: number;
  endTime: number;
  high: number;
  low: number;
}

/**
 * The initial balance of a session: the high..low of its first `ceil(ibMinutes * 60 / barSeconds)`
 * bars (at least one). Null for a session with no bars or a non-positive length.
 */
export function initialBalance(
  bars: readonly { time: number; high: number; low: number }[],
  barSeconds: number,
  ibMinutes: number,
): InitialBalance | null {
  if (bars.length === 0 || !(barSeconds > 0) || !(ibMinutes > 0)) return null;
  const take = Math.max(1, Math.ceil((ibMinutes * 60) / barSeconds));
  const first = bars.slice(0, take);
  let high = -Infinity;
  let low = Infinity;
  for (const b of first) {
    high = Math.max(high, b.high, b.low);
    low = Math.min(low, b.high, b.low);
  }
  if (!Number.isFinite(high) || !Number.isFinite(low)) return null;
  return { startTime: first[0].time, endTime: first[first.length - 1].time, high, low };
}
