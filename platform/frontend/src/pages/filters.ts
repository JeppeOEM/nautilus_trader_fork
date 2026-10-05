export type FilterOperator = ">" | "<" | ">=" | "<=" | "=";

export const FILTER_OPERATORS: FilterOperator[] = [">", "<", ">=", "<=", "="];

/** How a numeric cell is shown: `(value / scale).toFixed(decimals)`. Declared once per column
 * and shared by the cell and its filter field, so `=` matches what the cell shows. */
export interface DisplayPrecision {
  decimals: number;
  /** Divisor applied before rounding (volume24h: 1e6, shown in millions); none means 1. */
  scale?: number;
}

/** The cell text for `value` at `precision`, exactly as the column formatters always built it:
 * an unscaled non-number throws in `toFixed`, a scaled one is coerced by the division. */
export function formatFixed(value: number, precision: DisplayPrecision): string {
  const scaled = precision.scale === undefined ? value : value / precision.scale;
  return scaled.toFixed(precision.decimals);
}

/** The value as its cell shows it, in display units (`-0.00` reads as 0). */
export function displayed(value: number, precision: DisplayPrecision): number {
  return Number(formatFixed(value, precision));
}

/** A text condition is a case-insensitive match (only `=`), used for `venue`. A numeric one
 * always carries its field's display precision -- there is no strict-equality path. */
export type FilterCondition =
  | { field: string; op: "="; value: string }
  | { field: string; op: FilterOperator; value: number; precision: DisplayPrecision };

/** A missing/non-numeric value never satisfies a condition -- a row with no data for the
 * filtered field is excluded, not treated as 0.
 *
 * Only equality is tolerant: `=` holds when the actual value as its cell shows it equals the
 * typed value in display units (`value / scale`). The typed value is never rounded, so typing
 * more digits than the cell shows cannot match `=`. Ordering stays on the raw value (raw actual
 * vs raw typed value -- USD for volume24h), so a threshold finer than the display still orders
 * rows correctly. `<`/`>` exclude a shown match and `>=`/`<=` include one, so at most one of
 * `<`, `=`, `>` holds; a value exactly equal to a threshold finer than the display matches
 * `>=` and `<=` (as on the raw value) but not `=`.
 *
 * Known limit: the equality band is the column's fixed display precision, not the instrument's
 * own -- a price below 0.00005 shows `0.0000`, so it `=`-matches 0 and fails `Price > 0`; the same
 * holds for a Technicals output in price units (4 decimals for every output). Upgrade path: carry
 * per-instrument price precision through `frontend/src/lib/units.ts` and compare at it. */
export function compare(actual: unknown, condition: FilterCondition): boolean {
  if (!("precision" in condition)) {
    return typeof actual === "string" && actual.toLowerCase() === condition.value.toLowerCase();
  }
  if (typeof actual !== "number" || Number.isNaN(actual)) return false;
  const { value, precision } = condition;
  const eq = displayed(actual, precision) === value / (precision.scale ?? 1);
  switch (condition.op) {
    case "=":
      return eq;
    case ">=":
      return actual >= value || eq;
    case "<=":
      return actual <= value || eq;
    case ">":
      return actual > value && !eq;
    case "<":
      return actual < value && !eq;
  }
}

/** AND across every condition, order-preserving: rows keep the order they came in (message
 * order, the true rank). Sorting is not a filter's job -- it is RankingsPage's explicit
 * viewer choice (the Symbol/Exchange headers, Story 29.1), applied after the filters. */
export function applyFilters<T>(
  rows: T[],
  conditions: FilterCondition[],
  read: (row: T, field: string) => unknown,
): T[] {
  if (conditions.length === 0) return rows;
  return rows.filter((row) => conditions.every((c) => compare(read(row, c.field), c)));
}
