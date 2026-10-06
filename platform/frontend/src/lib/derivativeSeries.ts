import type { LineData, Time, UTCTimestamp, WhitespaceData } from "lightweight-charts";

import type { FundingItem, LiquidationBarItem } from "../api/schema";
import type { LiquidationMeasure } from "./chartLayout";
import { gapRun } from "./gaps";
import { unitsToNumber } from "./units";

// Story 33.5: the derivatives read models (Story 33.4's routes) mapped onto the chart's bars. Pure
// functions only: every number here was computed by the server (basis, annualised funding, OI change,
// notionals; SSOT-01/02) and is only placed on a bar, converted to a `number` for the plot, or held.
// A value the server did not send is whitespace, never a 0 and never interpolated (AD-F6).

export type SeriesPoint = LineData<Time> | WhitespaceData<Time>;

/** A bucketed page row: `t` is the bucket start in ms; a gap row carries `t` alone. */
export interface BucketRow {
  t: number;
}

const NS_PER_S = 1_000_000_000;

/** The plotted number of a server value: exact decimal text or a server float; null when absent. */
function plotValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** One series of a bucketed page: `field` per row at `t` (ms) as chart seconds; null is whitespace. */
export function bucketPoints<T extends BucketRow>(rows: readonly T[], field: keyof T): SeriesPoint[] {
  return rows.map((row) => {
    const time = (row.t / 1000) as UTCTimestamp;
    const value = plotValue(row[field]);
    return value === null ? { time } : { time, value };
  });
}

/** Points from `firstTime` (chart seconds) on: the candles own the time axis, so a derivative row older
 * than the oldest loaded candle waits for the candles' own scroll-back instead of widening the axis. */
export function fromTime<T extends { time: Time }>(points: readonly T[], firstTime: number | null): T[] {
  if (firstTime === null) return [];
  const start = points.findIndex((p) => (p.time as number) >= firstTime);
  return start <= 0 ? (start === 0 ? (points as T[]) : []) : points.slice(start);
}

/**
 * The slot of `slots` (ascending chart seconds) an event at `tNs` falls in: the latest slot starting
 * at or before it. Null before the first slot, and, with `barSeconds`, at or past that slot's end.
 */
export function eventSlot(tNs: number, slots: readonly number[], barSeconds?: number): number | null {
  const t = tNs / NS_PER_S;
  let lo = 0;
  let hi = slots.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (slots[mid] <= t) {
      found = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  if (found < 0) return null;
  const slot = slots[found];
  // Past its slot's end: after the last slot, or in a hole wider than `gapRun`'s cap between two slots.
  if (barSeconds !== undefined && t >= slot + barSeconds) return null;
  return slot;
}

/** One bar of the funding pane: the event its value comes from, or null (whitespace). */
export interface FundingBar {
  time: number;
  event: FundingItem | null;
}

/**
 * Funding on bars by holding the last event (`FundingItem.t` ns). The stored series is change-deduped
 * (audit D-103/D-108), so a bar after an event and before the next one decodes as "unchanged" -- the
 * hold is that decoding, not a fill. A bar takes the newest event before its end. The hold:
 * - never crosses a gap slot of `candleTimes` (a slot in `gapTimes`, drawn as whitespace): after one,
 *   only an event at or after the gap's start (inside it included) starts a value again;
 * - never starts before the first loaded event (the bars before it are whitespace);
 * - never runs past the newest event's bar, or the live forming bar `formingTime` when later.
 */
export function fundingPerBar(
  events: readonly FundingItem[],
  candleTimes: readonly number[],
  gapTimes: ReadonlySet<number>,
  barSeconds: number,
  formingTime: number | null = null,
): FundingBar[] {
  const newest = events.at(-1);
  const newestSlot = newest === undefined ? null : eventSlot(newest.t, candleTimes);
  const holdEnd = Math.max(newestSlot ?? -Infinity, formingTime ?? -Infinity);
  let held: FundingItem | null = null;
  let next = 0;
  let inGap = false;
  return candleTimes.map((time) => {
    const gap = gapTimes.has(time);
    // Entering a gap run: the rate held before it does not cross it. An event inside the run still
    // counts: it is the newest event at or before the next real bar's end, so it starts that bar.
    if (gap && !inGap) held = null;
    inGap = gap;
    const endNs = (time + barSeconds) * NS_PER_S;
    while (next < events.length && events[next].t < endNs) held = events[next++];
    if (gap) return { time, event: null };
    return { time, event: time <= holdEnd ? held : null };
  });
}

/** The funding bars as plotted points: the held rate, exact text converted for the plot only. */
export function fundingPoints(bars: readonly FundingBar[]): SeriesPoint[] {
  return bars.map(({ time, event }) => {
    const value = event === null ? null : plotValue(event.rate);
    return value === null ? { time: time as UTCTimestamp } : { time: time as UTCTimestamp, value };
  });
}

let unplottableLogged = false;

function sidePoint(time: UTCTimestamp, units: number | null | undefined, precision: number | null | undefined, sign: 1 | -1): SeriesPoint {
  if (units === null || units === undefined || precision === null || precision === undefined) return { time };
  try {
    return { time, value: sign * unitsToNumber(units, precision) };
  } catch (err) {
    // A value `lib/units.ts` cannot read exactly (past 2^53, audit D-170; a bad precision) is a gap,
    // logged once, never a rounded bar and never a crashed chart.
    if (!unplottableLogged) console.error("derivativeSeries: a liquidation value cannot be plotted exactly, drawn as a gap", err);
    unplottableLogged = true;
    return { time };
  }
}

/**
 * The liquidation bars mirrored around 0: long liquidations (forced sells) below, short ones above,
 * by liquidated size (`long_v`/`short_v` at `size_precision`) or by the server's per-side notional
 * (`long_notional_units`/`short_notional_units` at `notional_precision`). A null side is whitespace;
 * a known 0 is a 0 bar.
 */
export function mirroredLiquidations(
  rows: readonly LiquidationBarItem[],
  measure: LiquidationMeasure,
): { long: SeriesPoint[]; short: SeriesPoint[] } {
  const long: SeriesPoint[] = [];
  const short: SeriesPoint[] = [];
  for (const row of rows) {
    const time = (row.t / 1000) as UTCTimestamp;
    if (measure === "size") {
      long.push(sidePoint(time, row.long_v, row.size_precision, -1));
      short.push(sidePoint(time, row.short_v, row.size_precision, 1));
    } else {
      long.push(sidePoint(time, row.long_notional_units, row.notional_precision, -1));
      short.push(sidePoint(time, row.short_notional_units, row.notional_precision, 1));
    }
  }
  return { long, short };
}

function upsertByT<T extends { t: number }>(held: readonly T[], page: readonly T[]): T[] {
  const byT = new Map<number, T>();
  for (const row of held) byT.set(row.t, row);
  for (const row of page) byT.set(row.t, row); // the route wins: a re-read is newer than what was held
  return [...byT.values()].sort((a, b) => a.t - b.t);
}

function gapRows<T extends { t: number }>(after: number, before: number, stepMs: number | undefined): T[] {
  if (stepMs === undefined) return [];
  return gapRun(after, before, stepMs).map((t) => ({ t }) as T);
}

/**
 * A re-read newest page merged into `held`: upserted by `t` (the page wins, so a closed slot takes the
 * route's row over a forming value), and, with `stepMs` (a bucketed page), a page starting more than
 * one bucket after the newest held row gets the `gapRun` slots between them (the candles' seam rule;
 * the span was not observed by this page).
 */
export function mergeNewest<T extends { t: number }>(held: readonly T[], page: readonly T[], stepMs?: number): T[] {
  if (page.length === 0) return held as T[];
  const last = held.at(-1);
  const seam = last === undefined || page[0].t <= last.t ? [] : gapRows<T>(last.t, page[0].t, stepMs);
  return upsertByT([...held, ...seam], page);
}

/** An older page in front of `held`, with the seam's `gapRun` slots between them (bucketed pages). */
export function prependPage<T extends { t: number }>(held: readonly T[], page: readonly T[], stepMs?: number): T[] {
  if (page.length === 0) return held as T[];
  const first = held[0];
  const newest = page[page.length - 1];
  const seam = first === undefined || newest.t >= first.t ? [] : gapRows<T>(newest.t, first.t, stepMs);
  return upsertByT([...page, ...seam], held);
}

/** Event rows kept up to the replay cutoff bar's end: `ts` (ns) before `(cutoff + barSeconds)` s. */
export function eventsUpTo<T>(rows: readonly T[], ts: (row: T) => number, cutoff: number | null, barSeconds: number): T[] {
  if (cutoff === null) return rows as T[];
  const endNs = (cutoff + barSeconds) * NS_PER_S;
  return rows.filter((row) => ts(row) < endNs);
}

// -- live values of the forming bar, kept until the route serves them --------------------------------

/** The most closed-slot live values one series keeps waiting for the route (a few bars' worth: the
 * route serves a bar within about a minute of its close). Past it the oldest is dropped. */
export const MAX_LIVE_SLOTS = 32;
/** The most live funding events kept beyond the route's newest (Hyperliquid's can change every few
 * seconds; the route's 60 s re-read catches up and prunes them). */
export const MAX_LIVE_EVENTS = 2000;

/** One live `derivs:` value as the chart keeps it. */
export interface LiveTick {
  kind: "mark" | "index" | "funding" | "oi";
  t: number;
  value: string;
  interval?: number | null;
  next_funding_ns?: number | null;
  annualised?: number | null;
  basis_mi_bps?: number | null;
}

/**
 * The live values the server sent for bars the route has not served yet, by bucket start (ms): the
 * forming bar's and, once it closed, kept until the route serves that bucket (the archive lags the
 * flush). Funding ticks are server events (`t`, `rate`, ...) kept as events.
 */
export interface LiveSlots {
  oi: ReadonlyMap<number, string>;
  mark: ReadonlyMap<number, string>;
  index: ReadonlyMap<number, string>;
  basis: ReadonlyMap<number, number | null>;
  funding: readonly FundingItem[];
}

export const NO_LIVE_SLOTS: LiveSlots = { oi: new Map(), mark: new Map(), index: new Map(), basis: new Map(), funding: [] };

function withSlot<V>(map: ReadonlyMap<number, V>, slotMs: number, value: V): Map<number, V> {
  const next = new Map(map);
  next.set(slotMs, value);
  while (next.size > MAX_LIVE_SLOTS) next.delete(Math.min(...next.keys()));
  return next;
}

/**
 * `prev` with one live tick added, when its `t` falls in the forming slot `[liveTime, liveTime + bar)`;
 * a tick older than that slot (or any tick without a forming bar, a replay) changes nothing: the route
 * is the source for closed bars. Only the changed series gets a new map.
 */
export function addLiveTick(prev: LiveSlots, tick: LiveTick, liveTime: number | null, barSeconds: number): LiveSlots {
  if (liveTime === null) return prev;
  const t = tick.t / NS_PER_S;
  if (t < liveTime || t >= liveTime + barSeconds) return prev;
  const slotMs = liveTime * 1000;
  switch (tick.kind) {
    case "oi":
      return { ...prev, oi: withSlot(prev.oi, slotMs, tick.value) };
    case "mark":
    case "index": {
      const next = { ...prev, basis: withSlot(prev.basis, slotMs, tick.basis_mi_bps ?? null) };
      return tick.kind === "mark" ? { ...next, mark: withSlot(prev.mark, slotMs, tick.value) } : { ...next, index: withSlot(prev.index, slotMs, tick.value) };
    }
    default: {
      const event: FundingItem = {
        t: tick.t,
        rate: tick.value,
        interval: tick.interval ?? null,
        next_funding_ns: tick.next_funding_ns ?? null,
        annualised: tick.annualised ?? null,
      };
      return { ...prev, funding: mergeNewest(prev.funding, [event]).slice(-MAX_LIVE_EVENTS) };
    }
  }
}

function keepUnserved<V>(map: ReadonlyMap<number, V>, served: (t: number) => boolean): ReadonlyMap<number, V> {
  const kept = [...map].filter(([t]) => !served(t));
  return kept.length === map.size ? map : new Map(kept);
}

/** `live` without what the routes now serve: a bucket whose value the route has, funding events at or
 * before the route's newest. Returns `live` itself when nothing changed. */
export function pruneLiveSlots(
  live: LiveSlots,
  oiRows: readonly { t: number; oi?: string | null }[],
  markIndexRows: readonly { t: number; mark?: string | null; index?: string | null }[],
  fundingRows: readonly FundingItem[],
): LiveSlots {
  const oiAt = new Map(oiRows.map((r) => [r.t, r] as const));
  const miAt = new Map(markIndexRows.map((r) => [r.t, r] as const));
  const newestFunding = fundingRows.at(-1)?.t ?? -Infinity;
  const next: LiveSlots = {
    oi: keepUnserved(live.oi, (t) => oiAt.get(t)?.oi != null),
    mark: keepUnserved(live.mark, (t) => miAt.get(t)?.mark != null),
    index: keepUnserved(live.index, (t) => miAt.get(t)?.index != null),
    basis: keepUnserved(live.basis, (t) => miAt.get(t)?.mark != null && miAt.get(t)?.index != null),
    funding: live.funding.some((e) => e.t <= newestFunding) ? live.funding.filter((e) => e.t > newestFunding) : live.funding,
  };
  const same = (Object.keys(next) as (keyof LiveSlots)[]).every((key) => next[key] === live[key]);
  return same ? live : next;
}

/**
 * Open interest with the live values of the buckets the route has not served (a null `oi` there): the
 * live value, its change unknown (null) until the route serves the bucket, which then wins.
 *
 * Known limit: `oi_change` needs the previous bucket's last value, computed by the route from the
 * archive, so a live bucket's reads `—`. Upgrade path: the live frame carries the server's change
 * against the last closed bucket.
 */
export function overlayOpenInterest<T extends { t: number; oi?: string | null; oi_change?: string | null }>(
  rows: readonly T[],
  live: ReadonlyMap<number, string>,
  stepMs: number,
): T[] {
  if (live.size === 0) return rows as T[];
  const at = new Map(rows.map((r) => [r.t, r] as const));
  const overlay = [...live]
    .filter(([t]) => at.get(t)?.oi == null)
    .map(([t, oi]) => ({ ...at.get(t), t, oi, oi_change: null }) as unknown as T)
    .sort((a, b) => a.t - b.t);
  return overlay.reduce<T[]>((acc, row) => mergeNewest(acc, [row], stepMs), rows as T[]);
}

/**
 * Mark/index with the live values of the buckets the route has not served: per field, the route's
 * value wins once it has one; `basis_mi_bps` is the server's live one until then.
 *
 * Known limit: `basis_ml_bps` needs the bucket's traded close from the candle store, so a live
 * bucket's stays null until the route serves it. Upgrade path: a live frame pairing the mark with the
 * forming candle's close server-side.
 */
export function overlayMarkIndex<
  T extends { t: number; mark?: string | null; index?: string | null; basis_mi_bps?: number | null; basis_ml_bps?: number | null },
>(rows: readonly T[], live: LiveSlots, stepMs: number): T[] {
  const slots = new Set([...live.mark.keys(), ...live.index.keys()]);
  if (slots.size === 0) return rows as T[];
  const at = new Map(rows.map((r) => [r.t, r] as const));
  const overlay = [...slots]
    .sort((a, b) => a - b)
    .map((t) => {
      const route = at.get(t);
      return {
        ...route,
        t,
        mark: route?.mark ?? live.mark.get(t) ?? null,
        index: route?.index ?? live.index.get(t) ?? null,
        basis_mi_bps: route?.basis_mi_bps ?? live.basis.get(t) ?? null,
        basis_ml_bps: route?.basis_ml_bps ?? null,
      } as unknown as T;
    });
  return overlay.reduce<T[]>((acc, row) => mergeNewest(acc, [row], stepMs), rows as T[]);
}

/** The route's funding events with the live ones it does not hold yet, each `t` once (the route wins). */
export function withLiveFunding(rows: readonly FundingItem[], live: readonly FundingItem[]): FundingItem[] {
  return live.length === 0 ? (rows as FundingItem[]) : mergeNewest(live, rows);
}
