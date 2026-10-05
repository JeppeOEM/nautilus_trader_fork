import type { FootprintItem, FootprintRow } from "../api/schema";
import type { FootprintMode } from "./chartLayout";
import { formatUnits } from "./units";

// Story 32.8: the pure side of the volume footprint -- page merging, the heat scale, diagonal
// imbalances and the printed text. Every number stays integer units (`{p, b, s}` from
// `GET /api/coin/{iid}/footprint`) until `formatUnits` prints it; arithmetic on sizes is done in
// BigInt, so a delta or a row total is exact whatever the instrument's precision.

/**
 * `incoming` upserted into `prev` by bar time (`t`, ms), ascending; an incoming bar replaces the one
 * held (a newer page carries the same settled bar, or one that only just settled).
 */
export function mergeFootprintPages(prev: readonly FootprintItem[], incoming: readonly FootprintItem[]): FootprintItem[] {
  if (incoming.length === 0) return [...prev];
  const byTime = new Map<number, FootprintItem>();
  for (const item of prev) byTime.set(item.t, item);
  for (const item of incoming) byTime.set(item.t, item);
  return [...byTime.values()].sort((a, b) => a.t - b.t);
}

/** A row's traded size (buy + sell units); a number, used only for the heat scale's proportion. */
export function rowTotal(row: FootprintRow): number {
  return row.b + row.s;
}

/** The bar's fullest row total, the heat scale's 100 %; 0 for a bar without rows. */
export function maxRowTotal(item: FootprintItem): number {
  return item.rows.reduce((max, row) => Math.max(max, rowTotal(row)), 0);
}

export interface RowImbalance {
  /** Buy at this row against sell one row below. */
  buy: boolean;
  /** Sell at this row against buy one row above. */
  sell: boolean;
}

/**
 * The diagonal imbalances of one bar's rows (ascending `p`): a row's buy is an imbalance when it is
 * at least `ratio` times the sell of the row one `rowTicks` below, its sell when at least `ratio`
 * times the buy of the row one `rowTicks` above (where the auction actually crossed: the ask lifted
 * at n against the bid hit at n - 1). A missing neighbour row traded nothing; a zero opposite side
 * counts only when the side itself is non-zero.
 */
export function diagonalImbalances(rows: readonly FootprintRow[], rowTicks: number, ratio: number): RowImbalance[] {
  const byPrice = new Map(rows.map((row) => [row.p, row] as const));
  const beats = (side: number, opposite: number): boolean => side > 0 && side >= ratio * opposite;
  return rows.map((row) => ({
    buy: beats(row.b, byPrice.get(row.p - rowTicks)?.s ?? 0),
    sell: beats(row.s, byPrice.get(row.p + rowTicks)?.b ?? 0),
  }));
}

/** One cell's text in the display mode: "sell × buy", the delta (buy - sell) or the total. */
export function cellText(row: FootprintRow, mode: FootprintMode, sizePrecision: number): string {
  const buy = BigInt(row.b);
  const sell = BigInt(row.s);
  if (mode === "delta") return formatUnits(buy - sell, sizePrecision);
  if (mode === "volume") return formatUnits(buy + sell, sizePrecision);
  return `${formatUnits(sell, sizePrecision)} × ${formatUnits(buy, sizePrecision)}`;
}

/** The footer of a bar with trades: its delta (signed, "+" when positive) and total. */
export function footerText(item: FootprintItem, sizePrecision: number): { delta: string; total: string } | null {
  if (item.no_trades || item.delta === null || item.total === null) return null;
  const delta = formatUnits(item.delta, sizePrecision);
  return { delta: item.delta > 0 ? `+${delta}` : delta, total: formatUnits(item.total, sizePrecision) };
}

const MODE_LABELS: Record<FootprintMode, string> = { bid_ask: "bid×ask", delta: "delta", volume: "volume" };

/** The legend row's readout: the display mode and the row size. */
export function footprintLegendText(mode: FootprintMode, rowTicks: number): string {
  return `${MODE_LABELS[mode]} · ${rowTicks === 0 ? "auto rows" : `${rowTicks} tick${rowTicks === 1 ? "" : "s"}/row`}`;
}
