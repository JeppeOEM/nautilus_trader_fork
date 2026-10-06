import type { InstrumentPrecision } from "./drawings";
import { NO_VALUE } from "./drawings";
import { MAX_PRECISION, formatDecimal, formatPercent } from "./units";

/**
 * Story 33.6: how a custom indicator's output is printed, from the catalog entry's `units` table
 * (`GET /api/indicators/catalog`, `views.indicator_picker.CustomIndicatorSpec.units`): a price or a
 * size at the instrument definition's decimals, a mean of sizes finer than that (an average trade of
 * 0.0004 on a 0.001 size step must not read 0.000), a count as a whole number, a ratio as a percent. A
 * native entry has no table and keeps the legend's default readout.
 */
export const INDICATOR_UNITS = ["price", "size", "size_mean", "count", "ratio"] as const;
export type IndicatorUnit = (typeof INDICATOR_UNITS)[number];

// A ratio's percent is printed with this many decimals (as Funding's annualised rate is).
const RATIO_PLACES = 2;
// A mean of sizes is printed this many decimals finer than the size step.
export const MEAN_EXTRA_DECIMALS = 3;

export function isIndicatorUnit(value: unknown): value is IndicatorUnit {
  return (INDICATOR_UNITS as readonly unknown[]).includes(value);
}

/**
 * `value` printed in `unit` at `precision`, through `lib/units.ts` only. Guarded like
 * `safeDecimal`: a value `formatDecimal` refuses (not finite, or units beyond safe integers) reads
 * `NO_VALUE` instead of throwing out of the legend's draw.
 */
export function formatIndicatorValue(value: number, unit: IndicatorUnit, precision: InstrumentPrecision): string {
  try {
    switch (unit) {
      case "price":
        return formatDecimal(value, precision.price);
      case "size":
        return formatDecimal(value, precision.size);
      case "size_mean":
        return formatDecimal(value, Math.min(precision.size + MEAN_EXTRA_DECIMALS, MAX_PRECISION));
      case "count":
        return formatDecimal(value, 0);
      case "ratio":
        return `${formatPercent(value, RATIO_PLACES)}%`;
    }
  } catch {
    return NO_VALUE;
  }
}
