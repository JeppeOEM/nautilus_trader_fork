export type FilterOperator = ">" | "<" | ">=" | "<=" | "=";

export const FILTER_OPERATORS: FilterOperator[] = [">", "<", ">=", "<=", "="];

export interface FilterCondition {
  field: string;
  op: FilterOperator;
  value: number;
}

/** A missing/non-numeric value never satisfies a condition -- a row with no data for the
 * filtered field is excluded, not treated as 0. */
export function compare(actual: unknown, op: FilterOperator, value: number): boolean {
  if (typeof actual !== "number" || Number.isNaN(actual)) return false;
  switch (op) {
    case ">":
      return actual > value;
    case "<":
      return actual < value;
    case ">=":
      return actual >= value;
    case "<=":
      return actual <= value;
    case "=":
      return actual === value;
  }
}

/** AND across every condition, order-preserving (rankings are never re-sorted client-side). */
export function applyFilters<T>(
  rows: T[],
  conditions: FilterCondition[],
  read: (row: T, field: string) => unknown,
): T[] {
  if (conditions.length === 0) return rows;
  return rows.filter((row) => conditions.every((c) => compare(read(row, c.field), c.op, c.value)));
}
