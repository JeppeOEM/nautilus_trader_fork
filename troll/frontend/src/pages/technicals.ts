import type { TechnicalsColumn } from "../api/schema";
import { TIMEFRAMES } from "../timeframes";

/** Instrument -> "{entry_index}.{output_attr}" -> value, as `GET /api/rankings/technicals-values` returns. */
export type TechnicalsValues = Record<string, Record<string, number | null>>;

/** Mirrors the backend default for a column saved without a timeframe. */
export const DEFAULT_BAR_SECONDS = 3600;

/** What the column selector offers: the chart's timeframes minus 1W, which the backend rejects. */
export const COLUMN_TIMEFRAMES = TIMEFRAMES.filter((tf) => tf.seconds <= 86400);

export function columnBarSeconds(entry: TechnicalsColumn): number {
  return entry.bar_seconds ?? DEFAULT_BAR_SECONDS;
}

export interface TechnicalsGroup {
  entryIndex: number;
  entry: TechnicalsColumn;
  /** Output names present in the response (one for RSI, several for MACD/Bollinger...).
   * Empty until the first values arrive -- rendered as one placeholder column. */
  attrs: string[];
}

/** One group per configured entry; a multi-output entry's outputs become adjacent columns
 * under one shared header. Grouping comes from the response's keys, since the catalog does
 * not declare an entry's outputs. */
export function buildGroups(entries: TechnicalsColumn[], values: TechnicalsValues | undefined): TechnicalsGroup[] {
  return entries.map((entry, entryIndex) => {
    const prefix = `${entryIndex}.`;
    const attrs: string[] = [];
    for (const perInstrument of Object.values(values ?? {})) {
      for (const key of Object.keys(perInstrument)) {
        const attr = key.startsWith(prefix) ? key.slice(prefix.length) : null;
        if (attr !== null && !attrs.includes(attr)) attrs.push(attr);
      }
    }
    return { entryIndex, entry, attrs };
  });
}

/** Move `from` to `to`; a per-user view preference, never touches data. */
export function reorder<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}
