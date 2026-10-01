import type { IndicatorConfigEntry } from "../api/schema";

export const DEFAULT_SOURCE = "close";

// The price sources a close-fed indicator offers (the backend's `PRICE_SOURCES`, which refuses
// anything else with a 422). The Source select lists them only where the catalog says
// `source_selectable`.
export const PRICE_SOURCES = ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"] as const;

function pythonText(value: unknown): string {
  // `str()` of a JSON-decoded param on the backend: booleans are title-cased.
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

/**
 * Mirror of `views/indicator_picker.py`'s `indicator_id(name, params, source)`: the registry key
 * prefix of every series one configured indicator draws (`<id>.<output>`), and the id the legend
 * row's eye/gear/x act on. The source joins the id only when it is not `close`, so an id that
 * existed before sources is unchanged and SMA(20) on close and on hl2 are two instances.
 *
 * Known limit: a whole float param (2.0) reaches the backend as the JSON number 2 and both sides
 * print `2`; an exotic float whose Python and JS spellings differ (1e-7 vs 1e-07) would not match.
 * The catalog's params are ordinary periods and multipliers; upgrade path: have the values
 * response return each entry's id.
 */
export function indicatorId(name: string, params: Record<string, unknown> | undefined, source?: string): string {
  const keys = Object.keys(params ?? {}).sort();
  const base = keys.length === 0 ? name : `${name}_${keys.map((k) => `${k}=${pythonText(params?.[k])}`).join(",")}`;
  return !source || source === DEFAULT_SOURCE ? base : `${base}:${source}`;
}

export function entryId(entry: IndicatorConfigEntry): string {
  return indicatorId(entry.name, entry.params, entry.source);
}

/** A series key `<id>.<output>`: its id and output label (the last dot, params may hold dots). */
export function splitSeriesKey(key: string): { id: string; output: string } {
  const dot = key.lastIndexOf(".");
  return dot === -1 ? { id: key, output: key } : { id: key.slice(0, dot), output: key.slice(dot + 1) };
}
