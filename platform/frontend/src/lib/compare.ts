import type { LineData, Time, WhitespaceData } from "lightweight-charts";

import type { ChartDatum } from "../hooks/useCandles";
import { MAX_COMPARE_SYMBOLS } from "./chartTypes";

// Story 33.9: compare symbols, drawn as lines on the price pane's Percent (or Indexed) scale, and the
// cross-venue Spread pane. A compare value only ever comes from a real close at the main series' own
// bar time (AD-F6): where either side has no bar the slot is whitespace -- never interpolated, carried
// forward or zero-filled.

/** One aligned slot: a close, or whitespace (the legend's `IndicatorDatum` shape). */
export type ComparePoint = LineData<Time> | WhitespaceData<Time>;

/** The three compare line colours, by slot (the first compare takes the first): chart tokens declared
 * in theme.css's `.chart-workspace` and `components/chart/chartTheme.ts`'s `CHART_TOKENS`. */
export const COMPARE_PALETTE = ["--chart-compare-1", "--chart-compare-2", "--chart-compare-3"] as const;

/** The legend group of a compare's overlay, and of the Spread pane. */
export const COMPARE_GROUP_PREFIX = "compare:";
export const SPREAD_GROUP = "compare-spread";

interface CloseBar {
  time: Time;
  close: number;
}

/**
 * The compare's closes aligned on the main series' slots: one row per `mainTimes` entry, `{time,
 * value: close}` where the compare has a real bar at exactly that time, else whitespace (a gap on
 * either side, or a compare bar the main series lacks). A compare bar at a time the main series does
 * not have is not drawn: one time axis, so the main series' logical indices never move. `liveBar` is
 * the compare's forming bar; it wins at its own time unless older than the compare's newest slot.
 */
export function alignCompare(
  mainTimes: readonly number[],
  compare: readonly ChartDatum[],
  liveBar?: CloseBar | null,
): ComparePoint[] {
  const closes = new Map<number, number>();
  let newest = -Infinity;
  for (const d of compare) {
    if ("close" in d) closes.set(d.time as number, d.close);
    newest = Math.max(newest, d.time as number);
  }
  // A held bar older than the compare's newest slot is stale (a bar closed since): skipped, as the main
  // series' live edge skips one, so it never overwrites a closed close.
  if (liveBar && (liveBar.time as number) >= newest) closes.set(liveBar.time as number, liveBar.close);
  return mainTimes.map((time) => {
    const close = closes.get(time);
    return close === undefined ? { time: time as Time } : { time: time as Time, value: close };
  });
}

/**
 * The cross-venue spread in basis points, slot by slot over two aligned row sets (the main closes and
 * one compare's `alignCompare` rows, same times in the same order): `(a / b - 1) * 1e4`, a the main
 * close and b the compare close. The same formula as `research/domain/correlation.py`'s `basis_bps`
 * (used by `research/application/aligned.py`); both sides assert the shared fixture (101 / 100 = 100
 * bps). Whitespace where either side is whitespace. The two closes are compared as plain numbers: when
 * the instruments are quoted in different currencies (USDT against USD), the spread also holds that
 * quote rate, not only the venue basis (stated on the Docs page). A b <= 0 or a non-finite value is a defect upstream
 * (`basis_bps` raises on it): the slot stays whitespace and a console error names the bar, never a
 * fabricated value.
 */
export function spreadBps(main: readonly ComparePoint[], compare: readonly ComparePoint[]): ComparePoint[] {
  return main.map((a, i) => {
    const b = compare[i];
    if (!("value" in a) || b === undefined || !("value" in b)) return { time: a.time };
    if (!Number.isFinite(a.value) || !Number.isFinite(b.value) || b.value <= 0) {
      console.error(`compare spread: unusable close at bar ${String(a.time)} (main ${a.value}, compare ${b.value})`);
      return { time: a.time };
    }
    return { time: a.time, value: (a.value / b.value - 1) * 1e4 };
  });
}

/** The longest compare id accepted: mirrors `views/preferences.py`'s `MAX_INSTRUMENT_ID_LENGTH`
 * (`test_instrument_id_length_mirrors_the_frontend` pins the pair), so a longer id is refused here
 * rather than by the layout PUT. */
export const MAX_INSTRUMENT_ID_LENGTH = 512;

export type CompareInput = { ok: true; iid: string } | { ok: false; reason: string };

/**
 * Whether the Compare field's text can be added: a trimmed instrument id with a `.VENUE` suffix (the
 * `kernel.venues.venue_of` rule the server applies) of at most `MAX_INSTRUMENT_ID_LENGTH` characters, not
 * the chart's own instrument, not already
 * compared, and at most `MAX_COMPARE_SYMBOLS` in all. A refusal names why; nothing is saved.
 */
export function validateCompareInput(text: string, mainIid: string, current: readonly string[]): CompareInput {
  const iid = text.trim();
  const dot = iid.lastIndexOf(".");
  if (dot <= 0 || dot === iid.length - 1) return { ok: false, reason: "An instrument id needs a .VENUE suffix" };
  if (iid.length > MAX_INSTRUMENT_ID_LENGTH) {
    return { ok: false, reason: `An instrument id is at most ${MAX_INSTRUMENT_ID_LENGTH} characters` };
  }
  if (iid === mainIid) return { ok: false, reason: "This is the chart's own instrument" };
  if (current.includes(iid)) return { ok: false, reason: `${iid} is already compared` };
  if (current.length >= MAX_COMPARE_SYMBOLS) return { ok: false, reason: `At most ${MAX_COMPARE_SYMBOLS} compare symbols` };
  return { ok: true, iid };
}

/**
 * The compare layout with `iid` appended, re-validated against `layout` itself -- the latest state, inside
 * a state updater -- so a second add queued before the first re-rendered (a double Enter or click) can
 * add neither a duplicate nor a symbol past `MAX_COMPARE_SYMBOLS`: it returns `layout` unchanged.
 */
export function withCompareAdded<T extends { symbols: string[] }>(layout: T, iid: string, mainIid: string): T {
  const result = validateCompareInput(iid, mainIid, layout.symbols);
  return result.ok ? { ...layout, symbols: [...layout.symbols, result.iid] } : layout;
}
